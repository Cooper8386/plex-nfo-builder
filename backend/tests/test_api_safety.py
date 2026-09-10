import asyncio
import sqlite3
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from httpx import ConnectError

from app import config, db, main
from app.routes import api, settings
from app.services import artwork_download, matcher, tmdb, tvdb
from app.services.renamer import RenamePlanItem


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


def test_stale_rename_preview_never_applies(client, monkeypatch):
    http, media = client
    folder = media / "Movies" / "Movie"
    folder.mkdir(parents=True)
    source, destination = str(folder / "old.mkv"), str(folder / "new.mkv")
    db.upsert_binding(str(folder), "movie", "tmdb", "1", title="Movie")
    monkeypatch.setattr(api, "_resolve_localized_title", AsyncMock(return_value=("Movie", 2020)))
    plan = [RenamePlanItem(folder_path=str(folder), src=source, dst=destination, season=None, episode=None, matched_title=None)]
    monkeypatch.setattr(api.renamer_svc, "plan_movie_rename", lambda *args, **kwargs: plan)
    applied = []
    monkeypatch.setattr(api.renamer_svc, "apply_rename_plan", lambda entries: applied.append(entries) or {"renamed": 0})
    headers = {"X-API-Token": "test-secret"}
    result = http.post("/api/episodes/rename/apply", headers=headers, json={
        "folder_path": str(folder), "only_src": [source],
        "expected_plan": [{"src": source, "dst": str(folder / "previous-preview.mkv")}],
    })
    assert result.status_code == 409
    assert applied == []
    result = http.post("/api/episodes/rename/apply", headers=headers, json={"folder_path": str(folder), "only_src": []})
    assert result.status_code == 200
    assert applied == [[]]


def test_tmdb_rename_index_keeps_air_dates(monkeypatch):
    provider = AsyncMock()
    provider.tv_details.return_value = {"seasons": [{"season_number": 1}]}
    provider.tv_season.return_value = {"episodes": [{"id": 12, "season_number": 1, "episode_number": 2, "air_date": "2026-01-02"}]}
    monkeypatch.setattr(api, "get_tmdb_client", lambda: provider)
    index = asyncio.run(api._build_episodes_index({"provider": "tmdb", "external_id": "1"}, "eng"))
    assert index[(1, 2)]["aired"] == "2026-01-02"
