import asyncio
import sqlite3
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from httpx import ConnectError

from app import config, db, main
from app.routes import api, settings
from app.services import artwork, artwork_download, builder, matcher, scanner, sidecar, tmdb, tvdb


@pytest.fixture
def client(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    connection = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    connection.row_factory = sqlite3.Row
    db._init_schema(connection)
    db._migrate(connection)
    monkeypatch.setattr(db, "_conn", connection)
    monkeypatch.setattr(main, "_API_TOKEN", "test-secret")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(api, "MEDIA_ROOT", media)
    monkeypatch.setattr(api, "CUSTOM_ARTWORK_DIR", tmp_path / "uploads")
    monkeypatch.setattr(artwork_download, "CUSTOM_ARTWORK_DIR", tmp_path / "uploads")
    # No lifespan: test routes never start watcher/scanner or access real config.
    yield TestClient(main.app), media
    connection.close()


@pytest.mark.parametrize("path", ["/api/version", "/api/items", "/docs", "/openapi.json"])
def test_api_and_docs_require_token(client, path):
    http, _ = client
    assert http.get(path).status_code == 401
    assert http.get(path, headers={"X-API-Token": "wrong"}).status_code == 401


def test_auth_transport_and_fail_closed(client, monkeypatch):
    http, _ = client
    assert http.get("/api/version", headers={"Authorization": "Bearer test-secret"}).status_code == 200
    assert http.get("/api/version?api_token=test-secret").status_code == 200
    assert http.get("/api/version?api_token=%F0%9F%94%92").status_code == 401
    assert http.post("/api/settings?api_token=test-secret", json={}).status_code == 401
    monkeypatch.setattr(main, "_API_TOKEN", None)
    assert http.get("/api/version", headers={"X-API-Token": "test-secret"}).status_code == 503


def test_missing_and_escaping_paths_rejected(client, tmp_path):
    http, media = client
    headers = {"X-API-Token": "test-secret"}
    assert http.get("/api/browse", params={"path": str(tmp_path)}, headers=headers).status_code == 400
    assert http.get("/api/items/detail", params={"path": str(media / "missing")}, headers=headers).status_code == 404
    assert http.post("/api/items/clean", json={"folder_path": str(media)}, headers=headers).status_code == 400


def test_only_sidecar_deletion_clears_artwork_picks(client):
    http, media = client
    folder = media / "TV" / "Example"
    folder.mkdir(parents=True)
    sidecar = folder / ".plex-nfo-builder.json"
    sidecar.write_text("{}", encoding="utf-8")
    db.upsert_item_state(str(folder), library="TV", kind="series", title="Example")
    db.upsert_binding(str(folder), "series", "tvdb", "1")
    db.set_artwork_selection(str(folder), "poster", "https://example.com/poster.jpg")
    (folder / "poster.jpg").write_bytes(b"artwork")

    wipe = http.post(
        "/api/items/clean",
        headers={"X-API-Token": "test-secret"},
        json={"folder_path": str(folder), "rescan": False},
    )

    assert wipe.status_code == 200
    assert db.get_artwork_selections(str(folder))["poster"]["url"].endswith("poster.jpg")

    (folder / "poster.jpg").write_bytes(b"artwork")
    library_wipe = http.post(
        "/api/libraries/TV/wipe-nfo",
        headers={"X-API-Token": "test-secret"},
        json={"library": "TV", "rescan": False},
    )

    assert library_wipe.status_code == 200
    assert db.get_artwork_selections(str(folder))["poster"]["url"].endswith("poster.jpg")

    response = http.post(
        "/api/libraries/TV/wipe-sidecars",
        headers={"X-API-Token": "test-secret"},
        json={"library": "TV"},
    )

    assert response.status_code == 200
    assert response.json()["artwork_selections_cleared"] == 1
    assert not sidecar.exists()
    assert db.get_artwork_selections(str(folder)) == {}
    assert db.get_binding(str(folder))["external_id"] == "1"


def test_artwork_ignore_persists_and_resets(client):
    http, media = client
    headers = {"X-API-Token": "test-secret"}
    folder = media / "TV" / "Show"
    folder.mkdir(parents=True)
    db.upsert_item_state(str(folder), library="TV", kind="series", title="Show")
    db.upsert_binding(str(folder), "series", "tvdb", "1")

    response = http.post(
        "/api/artwork/ignore", headers=headers,
        json={"folder_path": str(folder), "slot": "season-08-poster"},
    )

    assert response.status_code == 200
    assert db.get_artwork_selections(str(folder))["season-08-poster"]["ignored"] is True
    assert sidecar.read_sidecar(folder)["artwork_selections"]["season-08-poster"]["ignored"] is True

    reset = http.post(
        "/api/artwork/ignore", headers=headers,
        json={"folder_path": str(folder), "slot": "season-08-poster", "ignored": False},
    )
    assert reset.status_code == 200
    assert db.get_artwork_selections(str(folder)) == {}
    assert sidecar.read_sidecar(folder)["artwork_selections"] == {}


def test_artwork_ignore_rejects_unsupported_slots_and_rolls_back(client, monkeypatch):
    http, media = client
    headers = {"X-API-Token": "test-secret"}
    folder = media / "TV" / "Show"
    folder.mkdir(parents=True)
    db.upsert_binding(str(folder), "series", "tvdb", "1")

    unsupported = http.post(
        "/api/artwork/ignore", headers=headers,
        json={"folder_path": str(folder), "slot": "episode-thumb-123"},
    )
    assert unsupported.status_code == 400

    db.set_artwork_selection(str(folder), "poster", "https://example.com/poster.jpg")
    monkeypatch.setattr(sidecar, "sync_sidecar_from_db", lambda _folder: False)
    failed = http.post(
        "/api/artwork/ignore", headers=headers,
        json={"folder_path": str(folder), "slot": "poster"},
    )
    assert failed.status_code == 500
    restored = db.get_artwork_selections(str(folder))["poster"]
    assert restored["url"] == "https://example.com/poster.jpg"
    assert restored["ignored"] is False


def test_series_artwork_skips_ignored_and_nonlocal_season_posters(client, monkeypatch):
    _, media = client
    folder = media / "TV" / "Show"
    folder.mkdir(parents=True)
    downloaded: list[str] = []

    async def fake_download(_client, _url, dest, *, force=False):
        downloaded.append(dest.name)
        return True

    monkeypatch.setattr(artwork, "_download", fake_download)
    remote = [
        {"type": artwork.SEASON_POSTER, "seasonNumber": 1, "image": "/season-1.jpg"},
        {"type": artwork.SEASON_POSTER, "seasonNumber": 8, "image": "/season-8.jpg"},
    ]
    series = {"seasons": [{"number": 1}, {"number": 8}]}

    asyncio.run(artwork.download_series_canonical(
        folder, series, remote, local_season_numbers=[1],
    ))
    assert downloaded == ["Season01-poster.jpg"]

    downloaded.clear()
    db.set_artwork_ignored(str(folder), "season-01-poster")
    asyncio.run(artwork.download_series_canonical(
        folder, series, remote, local_season_numbers=[1],
    ))
    assert downloaded == []

    db.clear_artwork_selection(str(folder), "season-01-poster")
    db.set_artwork_ignored(str(folder), "poster")
    images = artwork.series_image_urls(
        {"image": "/poster.jpg"}, [], folder_path=str(folder),
    )
    assert images["poster"] is None


def test_banner_artwork_requires_manual_selection(client, monkeypatch):
    _, media = client
    folder = media / "TV" / "Show"
    folder.mkdir(parents=True)
    downloaded: list[tuple[str, str]] = []

    async def fake_download(_client, url, dest, *, force=False):
        downloaded.append((url, dest.name))
        return True

    monkeypatch.setattr(artwork, "_download", fake_download)
    series_artwork = [{"type": artwork.SERIES_BANNER, "image": "/series-banner.jpg"}]
    movie_artwork = [{"type": artwork.MOVIE_BANNER, "image": "/movie-banner.jpg"}]

    assert artwork.series_image_urls({}, series_artwork, folder_path=str(folder))["banner"] is None
    assert artwork.movie_image_urls({}, movie_artwork, folder_path=str(folder))["banner"] is None
    assert builder._pick_art(folder, "banner", {"banner": "https://example.com/automatic.jpg"}, "https://example.com/default.jpg") is None
    asyncio.run(artwork.download_series_canonical(folder, {}, series_artwork))
    asyncio.run(artwork.download_movie_canonical(folder, {}, movie_artwork))
    assert downloaded == []

    db.set_artwork_selection(str(folder), "banner", "https://example.com/banner.jpg")
    assert artwork.series_image_urls({}, series_artwork, folder_path=str(folder))["banner"] == "https://example.com/banner.jpg"
    assert builder._pick_art(folder, "banner", {}, None) == "https://example.com/banner.jpg"
    asyncio.run(artwork.download_series_canonical(folder, {}, series_artwork))
    assert downloaded == [("https://example.com/banner.jpg", "banner.jpg")]


def test_nfo_ignore_updates_status_and_sidecar(client):
    http, media = client
    headers = {"X-API-Token": "test-secret"}
    folder = media / "TV" / "Show"
    season = folder / "Season 01"
    season.mkdir(parents=True)
    (folder / "tvshow.nfo").write_text(scanner.PROVENANCE_TAG, encoding="utf-8")
    (season / "Show S01E01.mkv").write_bytes(b"")
    (season / "Show S01E01.nfo").write_text(scanner.PROVENANCE_TAG, encoding="utf-8")
    (season / "Show S01E02.mkv").write_bytes(b"")
    scanner.scan_series_folder(folder, library="TV")

    response = http.post(
        "/api/items/nfo-ignore", headers=headers,
        json={"folder_path": str(folder), "file_path": "Season 01/Show S01E02.mkv", "ignored": True},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "ignored": ["Season 01/Show S01E02.mkv"],
        "status": "complete",
    }
    assert sidecar.read_sidecar(folder)["ignored_episode_files"] == ["Season 01/Show S01E02.mkv"]
    db.set_nfo_ignored_episode(str(folder), "Season 01/Deleted.mkv")
    detail = http.get("/api/items/nfo-explain", headers=headers, params={"path": str(folder)}).json()
    assert detail["video_count"] == 2 and detail["nfo_count"] == 1
    assert detail["ignored_episode_count"] == 1
    assert detail["ignored_episode_files"] == ["Season 01/Deleted.mkv", "Season 01/Show S01E02.mkv"]
    assert detail["seasons"][0]["missing"] == []
    assert detail["seasons"][0]["ignored_paths"] == ["Season 01/Show S01E02.mkv"]

    unignore = http.post(
        "/api/items/nfo-ignore", headers=headers,
        json={"folder_path": str(folder), "file_path": "Season 01/Show S01E02.mkv", "ignored": False},
    )
    assert unignore.status_code == 200
    assert unignore.json()["status"] == "partial"

    assert http.post(
        "/api/items/nfo-ignore", headers=headers,
        json={"folder_path": str(folder), "file_path": "Season 01/Show S01E02.mkv", "ignored": True},
    ).json()["status"] == "complete"
    clear = http.post("/api/items/nfo-ignore/clear", headers=headers, json={"folder_path": str(folder)})
    assert clear.status_code == 200
    assert clear.json() == {"ok": True, "ignored": [], "status": "partial"}
    assert sidecar.read_sidecar(folder)["ignored_episode_files"] == []


def test_nfo_ignore_rejects_non_missing_or_escaping_target(client):
    http, media = client
    headers = {"X-API-Token": "test-secret"}
    folder = media / "TV" / "Show"
    season = folder / "Season 01"
    season.mkdir(parents=True)
    (folder / "tvshow.nfo").write_text(scanner.PROVENANCE_TAG, encoding="utf-8")
    (season / "Show S01E01.mkv").write_bytes(b"")
    (season / "Show S01E01.nfo").write_text(scanner.PROVENANCE_TAG, encoding="utf-8")
    scanner.scan_series_folder(folder, library="TV")
    for file_path in ("../outside.mkv", "Season 01/Show S01E01.mkv"):
        response = http.post(
            "/api/items/nfo-ignore", headers=headers,
            json={"folder_path": str(folder), "file_path": file_path, "ignored": True},
        )
        assert response.status_code == 400


def test_nfo_ignore_accepts_missing_episode_beyond_diagnostic_50_item_cap(client):
    http, media = client
    headers = {"X-API-Token": "test-secret"}
    folder = media / "TV" / "Show"
    season = folder / "Season 01"
    season.mkdir(parents=True)
    (folder / "tvshow.nfo").write_text(scanner.PROVENANCE_TAG, encoding="utf-8")
    for episode in range(1, 52):
        (season / f"Show S01E{episode:02d}.mkv").write_bytes(b"")
    scanner.scan_series_folder(folder, library="TV")
    target = "Season 01/Show S01E51.mkv"

    ignored = http.post(
        "/api/items/nfo-ignore", headers=headers,
        json={"folder_path": str(folder), "file_path": target, "ignored": True},
    )
    assert ignored.status_code == 200
    assert ignored.json()["ignored"] == [target]
    unignored = http.post(
        "/api/items/nfo-ignore", headers=headers,
        json={"folder_path": str(folder), "file_path": target, "ignored": False},
    )
    assert unignored.status_code == 200
    assert unignored.json()["ignored"] == []


def test_single_item_auto_match_writes_binding_without_building(client, monkeypatch):
    http, media = client
    folder = media / "TV" / "Example"
    folder.mkdir(parents=True)
    video = folder / "Example S01E01.mkv"
    video.write_bytes(b"original video")
    async def match(path, **kwargs):
        db.upsert_binding(str(path), "series", "tvdb", "1", title="Example")
        return {"id": 1, "name": "Example"}
    monkeypatch.setattr(matcher, "auto_match_series", match)
    monkeypatch.setattr(api, "effective_metadata_source", lambda _: "tvdb")
    monkeypatch.setattr(api.build_svc, "start_build", lambda *a, **k: pytest.fail("Matching must not build"))
    response = http.post("/api/match/auto-bulk", headers={"X-API-Token": "test-secret"},
                         json={"folder_paths": [str(folder)]})
    assert response.status_code == 200 and response.json()["matched"] == 1
    assert db.get_binding(str(folder))["external_id"] == "1"
    assert video.read_bytes() == b"original video"
    assert not list(folder.rglob("*.nfo"))
    assert not list(folder.rglob("*.jpg"))


@pytest.mark.parametrize("change", [None, "primary", "secondary", "unmatched"])
def test_secondary_discovery_persists_and_preserves_concurrent_matches(client, monkeypatch, change):
    http, media = client
    folder = media / "TV" / "Example"
    folder.mkdir(parents=True)
    db.upsert_binding(str(folder), "series", "tmdb", "1")
    async def discover(binding):
        if change == "primary":
            db.upsert_binding(str(folder), "series", "tmdb", "3")
        elif change == "secondary":
            db.set_binding_secondary(str(folder), "tvdb", "4")
        return None if change == "unmatched" else "2"
    monkeypatch.setattr(matcher, "discover_secondary", discover)
    response = http.post("/api/match/secondary/discover", headers={"X-API-Token": "test-secret"},
                         json={"folder_path": str(folder)})
    assert response.status_code == (409 if change in ("primary", "secondary") else 200)
    binding = db.get_binding(str(folder))
    assert binding["secondary_external_id"] == {None: "2", "primary": None,
                                                 "secondary": "4", "unmatched": None}[change]
    if change is None:
        assert any('"secondary_external_id": "2"' in p.read_text(encoding="utf-8")
                   for p in folder.iterdir() if p.is_file())
        monkeypatch.setattr(matcher, "discover_secondary", AsyncMock(side_effect=AssertionError("Keep saved link")))
        assert http.post("/api/match/secondary/discover", headers={"X-API-Token": "test-secret"},
                         json={"folder_path": str(folder)}).json()["secondary_external_id"] == "2"


def test_switching_provider_requires_new_provider_id(client):
    http, media = client
    folder = media / "TV" / "Show"
    folder.mkdir(parents=True)
    db.upsert_binding(str(folder), "series", "tvdb", "1")
    result = http.post("/api/match/source", headers={"X-API-Token": "test-secret"},
                       json={"folder_path": str(folder), "provider": "tmdb"})
    assert result.status_code == 400
    assert db.get_binding(str(folder))["provider"] == "tvdb"


@pytest.mark.parametrize("endpoint", ["bind", "source"])
def test_invalid_match_kind_cannot_change_binding(client, endpoint):
    http, media = client
    folder = media / "TV" / "Show"
    folder.mkdir(parents=True)
    db.upsert_binding(str(folder), "series", "tvdb", "1")
    before = dict(db.get_binding(str(folder)))
    result = http.post(f"/api/match/{endpoint}", headers={"X-API-Token": "test-secret"},
                       json={"folder_path": str(folder), "provider": "tvdb",
                             "external_id": "2", "kind": "invalid"})
    assert result.status_code == 422
    assert dict(db.get_binding(str(folder))) == before


@pytest.mark.parametrize("provider", ["tvdb", "tmdb"])
@pytest.mark.parametrize("cached", [False, True])
def test_match_search_without_credentials_uses_cache_or_explains_setup(client, monkeypatch, provider, cached):
    http, _ = client
    monkeypatch.setattr(tvdb, "effective_tvdb_credentials", lambda: (None, None))
    monkeypatch.setattr(tmdb, "effective_tmdb_credentials", lambda: None)
    monkeypatch.setattr(api, "effective_tvdb_credentials", lambda: (None, None))
    monkeypatch.setattr(api, "effective_tmdb_credentials", lambda: None)
    if provider == "tvdb":
        remote = tvdb.TVDBClient()
        monkeypatch.setattr(matcher, "get_client", lambda: remote)
        key = remote._cache_key("/search", {"query": "Cached", "type": "series", "limit": 25})
        payload = {"data": [{"tvdb_id": "123", "name": "Cached", "year": "2024"}]}
    else:
        remote = tmdb.TMDBClient()
        monkeypatch.setattr(matcher, "get_tmdb_client", lambda: remote)
        key = remote._cache_key("/search/tv", {"query": "Cached", "language": "en-US", "include_adult": "true"})
        payload = {"results": [{"id": 123, "name": "Cached", "first_air_date": "2024-01-01"}]}
    request = AsyncMock(side_effect=AssertionError("Search must not contact a provider without credentials"))
    monkeypatch.setattr(remote._client, "get", request)
    monkeypatch.setattr(remote._client, "post", request)
    if cached:
        db.cache_set(key, payload, ttl=3600)
    try:
        result = http.get("/api/match/search", params={"q": "Cached", "provider": provider},
                          headers={"X-API-Token": "test-secret"})
        if cached:
            assert result.status_code == 200
            assert result.json()["results"][0]["name"] == "Cached"
            assert result.json()["provider"] == provider
        else:
            assert result.status_code == 503
            assert provider.upper() in result.json()["detail"]
            assert "Settings > Providers" in result.json()["detail"]
        request.assert_not_awaited()
    finally:
        asyncio.run(remote.aclose())


@pytest.mark.parametrize("provider,error", [
    ("tvdb", tvdb.TVDBError("secret provider error")),
    ("tmdb", tmdb.TMDBError("secret provider error")),
    ("tvdb", ConnectError("secret request URL")),
])
def test_match_search_provider_failure_is_actionable_and_redacted(client, monkeypatch, provider, error):
    http, _ = client
    monkeypatch.setattr(api, "effective_tvdb_credentials", lambda: ("configured-key", None))
    monkeypatch.setattr(api, "effective_tmdb_credentials", lambda: "configured-key")
    monkeypatch.setattr(matcher, "manual_search", AsyncMock(side_effect=error))
    result = http.get("/api/match/search", params={"q": "Missing", "provider": provider},
                      headers={"X-API-Token": "test-secret"})
    assert result.status_code == 502
    assert provider.upper() in result.json()["detail"]
    assert "retry later" in result.json()["detail"]
    assert "secret" not in result.text


def test_schedule_null_library_selects_all_libraries(client):
    http, _ = client
    schedule = db.insert_schedule(library="TV", cron="0 0 * * *", action="scan_only")
    headers = {"X-API-Token": "test-secret"}
    assert http.patch(f"/api/schedules/{schedule}", headers=headers, json={"enabled": False}).status_code == 200
    assert db.get_schedule(schedule)["library"] == "TV"
    assert http.patch(f"/api/schedules/{schedule}", headers=headers, json={"library": None}).status_code == 200
    assert db.get_schedule(schedule)["library"] is None


def test_settings_validation_preserves_file_and_redacts_env_secrets(client, monkeypatch):
    http, _ = client
    headers = {"X-API-Token": "test-secret"}
    config.save_user_settings(config.UserSettings(preferred_language="jpn"))
    before = config.SETTINGS_PATH.read_bytes()
    for invalid in ({"auto_match_threshold": -1}, {"preferred_language": None}, {"plex_path_mappings": [{"from": 5}]}):
        assert http.post("/api/settings", json=invalid, headers=headers).status_code == 422
        assert config.SETTINGS_PATH.read_bytes() == before
    monkeypatch.setattr(settings, "effective_tvdb_credentials", lambda: ("secret-key", "secret-pin"))
    result = http.get("/api/settings", headers=headers)
    assert result.json()["tvdb_api_key_configured"] is True
    assert "secret-key" not in result.text
    assert "tvdb_api_key" not in result.json()


def test_corrupt_settings_are_not_replaced(client):
    http, _ = client
    config.SETTINGS_PATH.write_text('{"preferred_language":', encoding="utf-8")
    before = config.SETTINGS_PATH.read_bytes()
    result = http.post("/api/settings", headers={"X-API-Token": "test-secret"}, json={"fanart_enabled": False})
    assert result.status_code == 503
    assert config.SETTINGS_PATH.read_bytes() == before


def test_upload_uses_raster_content_type(client):
    http, media = client
    folder = media / "TV" / "Show"
    folder.mkdir(parents=True)
    headers = {"X-API-Token": "test-secret"}
    bad = http.post("/api/artwork/upload", headers=headers, data={"folder_path": str(folder)},
                    files={"file": ("poster.jpg", b"<script>alert(1)</script>", "image/jpeg")})
    assert bad.status_code == 400
    good = http.post("/api/artwork/upload", headers=headers, data={"folder_path": str(folder)},
                     files={"file": ("poster.html", b"\x89PNG\r\n\x1a\nimage", "text/html")})
    assert good.status_code == 200
    image = http.get(good.json()["url"], headers=headers)
    assert image.headers["content-type"] == "image/png"
    assert image.headers["x-content-type-options"] == "nosniff"
    unsafe = folder / "unsafe.html"
    unsafe.write_text("<script>alert(1)</script>", encoding="utf-8")
    assert http.get("/api/artwork/file", params={"path": str(unsafe)}, headers=headers).status_code == 415


def test_scan_response_waits_for_completion(client, monkeypatch):
    http, _ = client
    db.upsert_library("TV", "tv")
    completed = []
    monkeypatch.setattr(api.scanner, "scan_library", lambda name: completed.append(name) or 3)
    result = http.post("/api/libraries/TV/scan", headers={"X-API-Token": "test-secret"})
    assert result.json()["scanned"] == 3
    assert completed == ["TV"]


def test_watcher_retry_reports_disabled_watcher(client, monkeypatch):
    http, media = client
    folder = media / "TV" / "Show"
    folder.mkdir(parents=True)
    db.upsert_library("TV", "tv")
    monkeypatch.setattr(api._watcher, "status", lambda: {"running": False})
    result = http.post("/api/watcher/review/retry", headers={"X-API-Token": "test-secret"}, json={"folder_path": str(folder)})
    assert result.status_code == 409


def test_large_library_internal_operations_and_api_pagination(client):
    http, _ = client
    db.conn().executemany(
        "INSERT INTO item_state(folder_path,library,title,kind) VALUES (?, 'TV', ?, 'series')",
        [(f"/media/TV/{index:05}", f"Show {index:05}") for index in range(5001)],
    )
    assert len(db.list_item_state(library="TV")) == 5001
    result = http.get("/api/items", params={"library": "TV", "offset": 5000, "limit": 5}, headers={"X-API-Token": "test-secret"})
    assert result.status_code == 200
    assert result.json()["total"] == 5001
    assert [item["title"] for item in result.json()["items"]] == ["Show 05000"]


def test_items_filter_manual_artwork_per_library(client):
    http, _ = client
    headers = {"X-API-Token": "test-secret"}
    items = [
        ("/media/TV/Complete", "TV", "Complete", "series", "complete"),
        ("/media/TV/Missing", "TV", "Missing", "series", "complete"),
        ("/media/TV/Unknown", "TV", "Unknown", "series", "none"),
        ("/media/TV/Legacy", "TV", "Legacy", "series", "complete"),
        ("/media/TV/Legacy Missing", "TV", "Legacy Missing", "series", "complete"),
        ("/media/Movies/Complete", "Movies", "Complete Movie", "movie", "complete"),
    ]
    db.conn().executemany(
        "INSERT INTO item_state(folder_path,library,title,kind,nfo_status) VALUES (?, ?, ?, ?, ?)",
        items,
    )
    show_slots = ["poster", "background", "banner", "clearlogo", "season-00-poster"]
    db.replace_artwork_required_slots("/media/TV/Complete", show_slots)
    db.replace_artwork_required_slots("/media/TV/Missing", show_slots)
    for slot in ["poster", "background", "clearlogo"]:
        db.set_artwork_selection("/media/TV/Complete", slot, f"https://example.com/{slot}.jpg")
    for slot in ["poster", "background", "banner", "clearart", "episode-thumb-1"]:
        db.set_artwork_selection("/media/TV/Missing", slot, f"https://example.com/{slot}.jpg")
    db.conn().execute(
        "UPDATE item_state SET season_count_local = 1 WHERE folder_path = '/media/TV/Legacy'"
    )
    for slot in ["poster", "background", "clearlogo", "season-01-poster"]:
        db.set_artwork_selection("/media/TV/Legacy", slot, f"https://example.com/{slot}.jpg")
        db.set_artwork_selection("/media/TV/Legacy Missing", slot, f"https://example.com/{slot}.jpg")
    db.conn().execute(
        "UPDATE item_state SET season_count_local = 2 WHERE folder_path = '/media/TV/Legacy Missing'"
    )

    movie_slots = ["poster", "background", "banner", "clearlogo"]
    db.replace_artwork_required_slots("/media/Movies/Complete", movie_slots)
    for slot in ["poster", "background", "clearlogo"]:
        db.set_artwork_selection("/media/Movies/Complete", slot, f"https://example.com/{slot}.jpg")

    complete = http.get(
        "/api/items",
        params={"library": "TV", "manual_artwork": "complete", "status": "complete", "q": "Complete"},
        headers=headers,
    )
    assert complete.status_code == 200
    assert [item["title"] for item in complete.json()["items"]] == ["Complete"]

    incomplete = http.get(
        "/api/items", params={"library": "TV", "manual_artwork": "incomplete"}, headers=headers,
    )
    assert [item["title"] for item in incomplete.json()["items"]] == ["Legacy Missing", "Missing", "Unknown"]

    legacy = http.get(
        "/api/items", params={"library": "TV", "manual_artwork": "complete", "q": "Legacy"}, headers=headers,
    )
    assert [item["title"] for item in legacy.json()["items"]] == ["Legacy"]

    movies = http.get(
        "/api/items", params={"library": "Movies", "manual_artwork": "complete"}, headers=headers,
    )
    assert [item["title"] for item in movies.json()["items"]] == ["Complete Movie"]


def test_artwork_candidates_record_counted_slots(client, monkeypatch):
    http, media = client
    headers = {"X-API-Token": "test-secret"}
    folder = media / "TV" / "Show"
    folder.mkdir(parents=True)
    db.upsert_item_state(str(folder), library="TV", kind="series", title="Show")
    db.upsert_binding(str(folder), "series", "tvdb", "1")
    remote = type("Remote", (), {})()
    remote.series_extended = AsyncMock(return_value={
        "artworks": [],
        "seasons": [{"number": 0}, {"number": 2}],
        "remoteIds": [],
    })
    monkeypatch.setattr(api, "get_client", lambda: remote)
    monkeypatch.setattr(
        api.artwork_svc,
        "list_candidates",
        lambda *_args, season_number=None, **_kwargs: (
            [{"url": f"https://example.com/season-{season_number}.jpg", "score": 0, "language": None}]
            if season_number is not None else []
        ),
    )

    result = http.get(
        "/api/artwork/candidates", params={"path": str(folder), "kind": "series"}, headers=headers,
    )
    assert result.status_code == 200
    assert set(result.json()["slots"]) == {"season-00-poster", "season-02-poster"}

    required = {
        row["slot"] for row in db.conn().execute(
            "SELECT slot FROM artwork_required_slots WHERE folder_path = ?", (str(folder),)
        )
    }
    assert required == {"poster", "background", "clearlogo", "season-02-poster"}

    counted = ["poster", "background", "clearlogo", "season-02-poster"]
    for slot in counted:
        db.set_artwork_selection(str(folder), slot, f"https://example.com/{slot}.jpg")
    db.set_artwork_selection(str(folder), "clearart", "https://example.com/clearart.jpg")
    db.set_artwork_selection(str(folder), "episode-thumb-1", "https://example.com/thumb.jpg")
    filtered = http.get(
        "/api/items", params={"library": "TV", "manual_artwork": "complete"}, headers=headers,
    )
    assert [item["title"] for item in filtered.json()["items"]] == ["Show"]
