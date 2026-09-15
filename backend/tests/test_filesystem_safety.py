"""Regression checks use only temporary media trees and an isolated SQLite DB."""
import json
import asyncio
import os
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import httpx
import pytest

from app import db
from app.config import UserSettings
from app.services import builder, cleaner, matcher, media_files, nfo, orphans, parser, scanner, sidecar


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.sqlite")
    monkeypatch.setattr(db, "_conn", None)
    yield db.conn()
    db.conn().close()


def _directory_link(link: Path, target: Path):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"OS does not permit creating test symlink: {error}")


def test_linked_season_cannot_escape_cleanup_boundary(tmp_path, touch):
    target = touch("Outside/Old.nfo", "protected")
    touch("Outside/New.mkv")
    folder = tmp_path / "Show"
    folder.mkdir()
    _directory_link(folder / "Season 01", target.parent)
    assert parser.detect_season_dirs(folder) == []
    assert cleaner.preview_clean(folder) == []
    assert cleaner.clean_folder(folder)["nfo_deleted"] == 0
    assert orphans.sweep_series_orphans(folder)["nfo_removed"] == 0
    assert target.read_text() == "protected"


def test_prune_preserves_folder_with_linked_directory(tmp_path, touch):
    media = touch("Outside/Video.mkv")
    folder = tmp_path / "Show"
    folder.mkdir()
    _directory_link(folder / "Season 01", media.parent)
    assert scanner.folder_has_media(folder)


def test_clean_preview_matches_deletion_and_preserves_media(tmp_path, touch):
    for name in ("tvshow.nfo", "POSTER.JPG", "Season 01/Old.nfo", "Season 01/Old-thumb.png",
                 "Season 01/Video.mkv", "Season 01/Video.en.srt", ".plex-nfo-builder.json"):
        touch(name, name)
    preview = cleaner.preview_clean(tmp_path)
    assert len(preview) == 4
    result = cleaner.clean_folder(tmp_path)
    assert sorted(preview) == sorted(result["files"])
    assert not result["failed"]
    assert (tmp_path / ".plex-nfo-builder.json").exists()
    assert (tmp_path / "Season 01/Video.mkv").exists()
    assert (tmp_path / "Season 01/Video.en.srt").exists()


def test_root_orphan_sweep_preserves_directory_nfos_even_with_seasons(tmp_path, touch):
    for name in ("OVA.mkv", "OVA.nfo", "tvshow.nfo", "movie.nfo", "Old.nfo", "Season 01/Ep.mkv"):
        touch(name, name)
    assert orphans.count_series_orphans(tmp_path) == 1
    result = orphans.sweep_series_orphans(tmp_path)
    assert result["files"] == ["Old.nfo"]
    assert (tmp_path / "tvshow.nfo").exists() and (tmp_path / "movie.nfo").exists()


def test_empty_season_companions_are_preserved_until_video_returns(tmp_path, touch):
    orphan = touch("Season 01/Old.nfo", "metadata")
    assert orphans.count_series_orphans(tmp_path) == 0
    assert orphans.sweep_series_orphans(tmp_path)["nfo_removed"] == 0
    assert orphan.exists()


def test_orphan_nfo_cannot_mask_missing_episode_metadata(tmp_path, touch):
    touch("tvshow.nfo", scanner.PROVENANCE_TAG)
    touch("Season 01/Live.mkv")
    touch("Season 01/Old.nfo", scanner.PROVENANCE_TAG)
    assert scanner._scan_nfo_state(tmp_path, 1, "series")[0] == "partial"
    detail = scanner.explain_nfo_state(tmp_path, "series")
    assert detail["status"] == "partial" and detail["nfo_count"] == 0
    assert detail["seasons"][0]["missing"] == ["Live.mkv"]


def test_foreign_episode_prevents_complete_status(tmp_path, touch):
    touch("tvshow.nfo", scanner.PROVENANCE_TAG)
    touch("Season 01/Live.mkv")
    touch("Season 01/Live.nfo", "<episodedetails/>")
    assert scanner._scan_nfo_state(tmp_path, 1, "series")[0] == "mixed"
    assert scanner.explain_nfo_state(tmp_path, "series")["status"] == "mixed"


def test_root_episode_diagnostics_agree_with_scanner(tmp_path, touch):
    touch("tvshow.nfo", scanner.PROVENANCE_TAG)
    touch("Live.mkv")
    touch("Live.nfo", scanner.PROVENANCE_TAG)
    assert scanner._scan_nfo_state(tmp_path, 1, "series")[0] == "complete"
    detail = scanner.explain_nfo_state(tmp_path, "series")
    assert detail["status"] == "complete" and detail["nfo_count"] == 1


@pytest.mark.parametrize("data", [[1], "text", 42, {"binding": ["tvdb"]}, {"version": 999}])
def test_malformed_sidecar_is_rejected(tmp_path, data):
    sidecar.sidecar_path(tmp_path).write_text(json.dumps(data), encoding="utf-8")
    assert sidecar.read_sidecar(tmp_path) is None


def test_sidecar_restore_blocks_traversal_and_normalizes_cross_platform_paths(tmp_path, isolated_db):
    paths = ["../outside.mkv", "/outside.mkv", "C:\\outside.mkv", "..\\outside.mkv", "Season 01\\Live.mkv"]
    data = {"episode_file_overrides": {path: {"season": 1, "episode": 2} for path in paths}}
    assert sidecar.write_sidecar(tmp_path, data)
    assert sidecar.restore_from_sidecar(tmp_path)
    assert list(db.get_episode_file_overrides(str(tmp_path))) == [str(tmp_path / "Season 01/Live.mkv")]
    payload = sidecar.build_sidecar_payload(tmp_path)
    assert list(payload["episode_file_overrides"]) == ["Season 01/Live.mkv"]


def test_sidecar_restores_ignored_episode_paths_safely(tmp_path, isolated_db):
    data = {"ignored_episode_files": ["Season 01\\Live.mkv", "../outside.mkv", "C:\\outside.mkv"]}
    assert sidecar.write_sidecar(tmp_path, data)
    assert sidecar.restore_from_sidecar(tmp_path)
    assert db.get_nfo_ignored_episodes(str(tmp_path)) == ["Season 01/Live.mkv"]
    assert sidecar.build_sidecar_payload(tmp_path)["ignored_episode_files"] == ["Season 01/Live.mkv"]


def test_ignored_missing_episode_changes_only_effective_series_status(tmp_path, touch, isolated_db):
    touch("tvshow.nfo", scanner.PROVENANCE_TAG)
    touch("Season 01/Live.mkv")
    touch("Season 01/Live.nfo", scanner.PROVENANCE_TAG)
    touch("Season 01/Missing.mkv")
    folder = tmp_path
    db.set_nfo_ignored_episode(str(folder), "Season 01/Missing.mkv")
    db.set_nfo_ignored_episode(str(folder), "Season 01/Deleted.mkv")

    scanner.scan_series_folder(folder, library="TV")
    assert db.get_item_state(str(folder))["nfo_status"] == "complete"
    detail = scanner.explain_nfo_state(
        folder, "series", ignored_episode_files=set(db.get_nfo_ignored_episodes(str(folder))),
    )
    assert detail["status"] == "complete"
    assert detail["video_count"] == 2 and detail["nfo_count"] == 1
    assert detail["ignored_episode_count"] == 1
    assert detail["ignored_episode_files"] == ["Season 01/Deleted.mkv", "Season 01/Missing.mkv"]
    assert detail["seasons"][0]["missing"] == []
    assert detail["seasons"][0]["ignored"] == ["Missing.mkv"]
    assert detail["seasons"][0]["ignored_paths"] == ["Season 01/Missing.mkv"]

    db.clear_nfo_ignored_episode(str(folder))
    detail = scanner.explain_nfo_state(folder, "series", ignored_episode_files=set())
    assert detail["ignored_episode_files"] == []

    touch("Season 01/New.mkv")
    scanner.scan_series_folder(folder, library="TV")
    assert db.get_item_state(str(folder))["nfo_status"] == "partial"


def test_sidecar_restore_rolls_back_partial_binding_for_retry(tmp_path, isolated_db, monkeypatch):
    data = {"binding": {"kind": "series", "provider": "tvdb", "external_id": "42"},
            "custom_tags": ["Favourite"]}
    assert sidecar.write_sidecar(tmp_path, data)
    original = db.bulk_set_custom_tags

    def fail(*args):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(db, "bulk_set_custom_tags", fail)
    assert not sidecar.restore_from_sidecar(tmp_path)
    assert db.get_binding(str(tmp_path)) is None
    monkeypatch.setattr(db, "bulk_set_custom_tags", original)
    assert sidecar.restore_from_sidecar(tmp_path)
    assert db.list_custom_tags(str(tmp_path)) == ["Favourite"]


def test_sidecar_restore_preserves_existing_manual_binding(tmp_path, isolated_db):
    assert sidecar.write_sidecar(tmp_path, {"binding": {"kind": "series", "provider": "tvdb", "external_id": "42"}})
    db.upsert_binding(str(tmp_path), "series", "tmdb", "99", source_locked=True)
    assert not sidecar.restore_from_sidecar(tmp_path)
    assert db.get_binding(str(tmp_path))["external_id"] == "99"


def test_sidecar_atomic_write_failure_preserves_previous_file(tmp_path, monkeypatch):
    target = sidecar.sidecar_path(tmp_path)
    target.write_text("original", encoding="utf-8")

    def fail_replace(*args):
        raise OSError("share disconnected")

    monkeypatch.setattr(os, "replace", fail_replace)
    assert not sidecar.write_sidecar(tmp_path, {"version": 1})
    assert target.read_text() == "original"
    assert list(tmp_path.glob(".pnb-*.tmp")) == []


def test_nfo_strips_invalid_xml_characters_without_losing_unicode():
    root = ET.Element("movie")
    nfo._el(root, "title", "A\x00\x0b & 東京")
    text = nfo._pretty(root, {"tvdb_id": "42----><injected/>"})
    parsed = ET.fromstring(text)
    assert parsed.findtext("title") == "A & 東京"
    assert parsed.find("injected") is None


@pytest.mark.parametrize(("build", "payload"), [
    (nfo.build_series_nfo, {"id": 1, "name": "The Bear"}),
    (nfo.build_series_nfo_tmdb, {"id": 1, "name": "The Bear"}),
])
def test_series_nfo_uses_sonarr_sort_title(build, payload):
    root = ET.fromstring(build(payload, language="eng", fallbacks=[]))
    assert root.findtext("sorttitle") == "Bear"


def test_media_temp_uses_normal_creation_mode_and_exclusive_name(tmp_path, monkeypatch):
    monkeypatch.setattr(media_files.secrets, "token_hex", lambda size: "fixed")
    original_open = os.open
    creation = []

    def capture_open(path, flags, mode):
        creation.append((flags, mode))
        return original_open(path, flags, mode)

    monkeypatch.setattr(media_files.os, "open", capture_open)
    descriptor, temporary = media_files.create_media_temp(tmp_path / "poster.jpg")
    os.write(descriptor, b"existing transfer")
    os.close(descriptor)
    assert creation[0][1] == 0o666
    assert creation[0][0] & os.O_EXCL
    with pytest.raises(FileExistsError):
        media_files.create_media_temp(tmp_path / "poster.jpg")
    assert temporary.read_bytes() == b"existing transfer"


def test_media_temp_does_not_follow_destination_symlink(tmp_path):
    outside = tmp_path / "original.jpg"
    outside.write_bytes(b"protected")
    outside.chmod(0o644)
    destination = tmp_path / "poster.jpg"
    try:
        destination.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"OS does not permit creating test symlink: {error}")
    previous_mask = os.umask(0o077)
    try:
        descriptor, temporary = media_files.create_media_temp(destination)
    finally:
        os.umask(previous_mask)
    os.write(descriptor, b"replacement")
    os.close(descriptor)
    if os.name == "posix":
        assert stat.S_IMODE(temporary.stat().st_mode) == 0o600
    os.replace(temporary, destination)
    assert outside.read_bytes() == b"protected"
    assert destination.read_bytes() == b"replacement" and not destination.is_symlink()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions and umask")
@pytest.mark.parametrize(("existing_mode", "mask", "expected"), [
    (None, 0o027, 0o640), (0o644, 0o077, 0o644), (0o600, 0o022, 0o600),
])
def test_published_nfo_keeps_media_permissions(tmp_path, monkeypatch, existing_mode, mask, expected):
    monkeypatch.setattr(builder, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(builder, "get_user_settings", UserSettings)
    destination = tmp_path / "tvshow.nfo"
    if existing_mode is not None:
        destination.write_text("<!-- plex-nfo-builder -->\n<tvshow/>", encoding="utf-8")
        destination.chmod(existing_mode)
    previous_mask = os.umask(mask)
    try:
        builder._atomic_write_text(destination, "<!-- plex-nfo-builder -->\n<updated/>")
    finally:
        os.umask(previous_mask)
    assert destination.read_text().endswith("<updated/>")
    assert stat.S_IMODE(destination.stat().st_mode) == expected


@pytest.mark.parametrize("kind", ["series", "movie"])
def test_tvdb_outage_preserves_explicit_id_without_fuzzy_fallback(tmp_path, isolated_db, monkeypatch, kind):
    folder = tmp_path / "Show (2020) {tvdb-42}"
    folder.mkdir()
    calls = []

    async def fail(*args, **kwargs):
        raise httpx.ReadTimeout("provider unavailable")

    async def search(*args, **kwargs):
        calls.append("search")
        return [{"id": 99, "name": "Show"}]

    client = SimpleNamespace(series_extended=fail, movie_extended=fail, search=search)
    monkeypatch.setattr(matcher, "get_client", lambda: client)
    action = matcher.auto_match_series if kind == "series" else matcher.auto_match_movie
    result = asyncio.run(action(folder))
    assert result["id"] == "42" and calls == []
    assert db.get_binding(str(folder))["external_id"] == "42"


def test_tmdb_timeout_does_not_rebind_series_to_unrelated_movie(tmp_path, isolated_db, monkeypatch):
    folder = tmp_path / "Show {tmdb-42}"
    folder.mkdir()
    calls = []

    async def fail(*args, **kwargs):
        raise httpx.ReadTimeout("provider unavailable")

    async def movie(*args, **kwargs):
        calls.append("movie")
        return {"id": 42, "title": "Unrelated movie"}

    monkeypatch.setattr(matcher, "get_tmdb_client", lambda: SimpleNamespace(tv_details=fail, movie_details=movie))
    asyncio.run(matcher.auto_match_series_tmdb(folder))
    assert calls == [] and db.get_binding(str(folder))["kind"] == "series"


def test_tmdb_mode_honors_tvdb_folder_tag(tmp_path, isolated_db, monkeypatch):
    folder = tmp_path / "Show {tvdb-42}"
    folder.mkdir()

    async def details(*args, **kwargs):
        return {"id": 42, "name": "Show"}

    monkeypatch.setattr(matcher, "get_client", lambda: SimpleNamespace(series_extended=details))
    asyncio.run(matcher.auto_match_series_tmdb(folder))
    assert db.get_binding(str(folder))["provider"] == "tvdb"


def test_matching_prefers_exact_year_even_when_titles_both_score_100():
    tvdb = [{"id": 1, "name": "Dune", "year": "1984"}, {"id": 2, "name": "Dune", "year": "2021"}]
    tmdb = [{"id": 1, "title": "Dune", "release_date": "1984-01-01"},
            {"id": 2, "title": "Dune", "release_date": "2021-01-01"}]
    assert matcher._pick_best(tvdb, "Dune", 2021)["id"] == 2
    assert matcher._pick_best_tmdb(tmdb, "Dune", 2021, "movie")["id"] == 2
    assert all("_score" not in candidate for candidate in tvdb + tmdb)


def test_scan_movie_library_detects_root_series_and_preserves_empty_binding(tmp_path, touch, isolated_db, monkeypatch):
    touch("Movies/Show/Show - S01E01.mkv")
    empty = tmp_path / "Movies/Empty"
    empty.mkdir()
    db.upsert_library("Movies", "movies")
    db.upsert_binding(str(empty), "series", "tmdb", "42")
    monkeypatch.setattr(scanner, "MEDIA_ROOT", tmp_path)
    scanner.scan_library("Movies")
    assert db.get_item_state(str(tmp_path / "Movies/Show"))["kind"] == "series"
    assert db.get_binding(str(empty))["kind"] == "series"


@pytest.mark.parametrize("provider", ["tvdb", "tmdb"])
@pytest.mark.parametrize("layout", ["unparsed", "root", "daily"])
@pytest.mark.parametrize("binding_mode", ["bound", "other_provider"])
def test_build_honors_file_mapping_and_root_daily_episodes(
    tmp_path, isolated_db, monkeypatch, provider, layout, binding_mode,
):
    folder = tmp_path / "Show"
    relative = {"unparsed": "Season 01/random-release.mkv", "root": "Show - S01E01.mkv",
                "daily": "Show - 2020-01-07.mkv"}[layout]
    video = folder / relative
    video.parent.mkdir(parents=True)
    video.write_text("fixture media", encoding="utf-8")
    if binding_mode == "bound":
        db.upsert_binding(str(folder), "series", provider, "42", title="Show", year=2020)
    if layout != "daily":
        db.set_episode_file_override(str(folder), str(video), 2 if layout == "unparsed" else None,
                                     7 if layout == "unparsed" else None, "107")
    initial_provider = provider if binding_mode == "bound" else ("tmdb" if provider == "tvdb" else "tvdb")
    settings = UserSettings(metadata_source=initial_provider)
    monkeypatch.setattr(builder, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(builder, "get_user_settings", lambda: settings)
    monkeypatch.setattr(builder, "effective_metadata_source", lambda _: initial_provider)
    log = SimpleNamespace(**{method: lambda *args, **kwargs: None for method in ("info", "warning", "error", "exception")})
    monkeypatch.setattr(builder, "job_logger", lambda _: log)
    monkeypatch.setattr(builder, "close_job_logger", lambda _: None)
    monkeypatch.setattr(builder, "_maybe_sweep_orphans", lambda *args: None)
    monkeypatch.setattr(builder, "_maybe_schedule_plex_refresh", lambda *args: None)

    async def nothing(*args, **kwargs):
        return {}

    matching_calls = []

    async def match(*args, **kwargs):
        matching_calls.append(provider)
        db.upsert_binding(str(folder), "series", provider, "42", title="Show", year=2020)
        return {"id": 42, "name": "Show"}

    monkeypatch.setattr(builder, "auto_match_series", match)
    monkeypatch.setattr(builder, "auto_match_series_tmdb", match)

    for name in ("resolve_preferred_artwork_series", "download_series_canonical", "_download_url",
                 "_download_actor_portraits_tvdb", "_download_actor_portraits_tmdb", "_hydrate_tvdb_character_thumbs",
                 "hydrate_ratings"):
        monkeypatch.setattr(builder, name, nothing)
    season_calls = []

    async def tvdb_series(*args, **kwargs):
        return {"id": 42, "name": "Show", "seasons": [], "characters": [{"personName": "Lead"}]}

    async def tvdb_episodes(*args, **kwargs):
        return [{"id": 101, "seasonNumber": 1, "number": 1, "name": "Wrong"},
                {"id": 107, "seasonNumber": 2, "number": 7, "name": "Chosen", "aired": "2020-01-07"}]

    async def tvdb_episode(identifier, **kwargs):
        episode = next(episode for episode in await tvdb_episodes() if episode["id"] == identifier)
        return {**episode, "characters": [{"personName": "Guest"}]}

    async def tmdb_series(*args, **kwargs):
        return {"id": 42, "name": "Show", "seasons": [{"season_number": 1}, {"season_number": 2}]}

    async def tmdb_season(identifier, number, **kwargs):
        season_calls.append(number)
        episode = ({"id": 101, "season_number": 1, "episode_number": 1, "name": "Wrong"} if number == 1 else
                   {"id": 107, "season_number": 2, "episode_number": 7, "name": "Chosen", "air_date": "2020-01-07"})
        return {"episodes": [episode]}

    monkeypatch.setattr(builder, "get_client", lambda: SimpleNamespace(
        series_extended=tvdb_series, series_episodes=tvdb_episodes, episode_extended=tvdb_episode, best_translation=nothing))
    async def tmdb_episode(identifier, season, episode, **kwargs):
        assert (identifier, season, episode) == (42, 2, 7)
        return {"credits": {"cast": [{"name": "Guest"}]}}

    async def hydrate(record, *, force):
        assert force is True
        record.setdefault("credits", {"cast": [{"name": "Lead"}]})
        record["credits"]["cast"][0]["profile_path"] = "/portrait.jpg"

    async def hydrate_tvdb(people, **kwargs):
        for person in people or []:
            person["personImgURL"] = "https://images.example/portrait.jpg"

    async def fill_ratings(record, *, kind, provider, log, force, **kwargs):
        assert force is True
        record["_ratings"] = {"imdb": {"value": 9.1 if kind == "episode" else 8.7, "max": 10}}

    portraits = []

    async def save_portraits(folder, people, **kwargs):
        portraits.extend(person.get("personName") or person["name"] for person in people or [])

    monkeypatch.setattr(builder, "hydrate_ratings", fill_ratings)
    monkeypatch.setattr(builder, "_hydrate_tvdb_character_thumbs", hydrate_tvdb)
    monkeypatch.setattr(builder, "_download_actor_portraits_tvdb", save_portraits)
    monkeypatch.setattr(builder, "_download_actor_portraits_tmdb", save_portraits)
    monkeypatch.setattr(builder, "get_tmdb_client", lambda: SimpleNamespace(
        tv_details=tmdb_series, tv_season=tmdb_season, tv_episode=tmdb_episode, hydrate_credits=hydrate))
    job_id = asyncio.run(builder.build_series(folder, force=True))
    job = builder.get_job(job_id)
    assert job["status"] == "completed", job["messages"]
    generated = ET.parse(video.with_suffix(".nfo")).getroot()
    assert generated.findtext("title") == "Chosen"
    assert generated.findtext("uniqueid") == "107"
    assert generated.find("uniqueid").get("type") == provider
    assert generated.findtext("actor/name") == "Guest"
    assert generated.findtext("actor/thumb").endswith("/portrait.jpg")
    assert ET.parse(folder / "tvshow.nfo").findtext("actor/thumb").endswith("/portrait.jpg")
    assert portraits == ["Guest", "Lead"]
    assert generated.findtext("ratings/rating[@name='imdb']/value") == "9.1"
    assert ET.parse(folder / "tvshow.nfo").findtext("ratings/rating[@name='imdb']/value") == "8.7"
    assert scanner._scan_nfo_state(folder, 1, "series")[0] == "complete"
    assert len(season_calls) == len(set(season_calls))
    assert matching_calls == ([] if binding_mode == "bound" else [provider])


@pytest.mark.parametrize("provider", ["tvdb", "tmdb"])
def test_movie_build_hydrates_cast_before_nfo_and_saves_local_portrait(tmp_path, isolated_db, monkeypatch, provider):
    folder = tmp_path / "Movie"
    folder.mkdir()
    video = folder / "Movie.mkv"
    video.touch()
    db.upsert_binding(str(folder), "movie", provider, "42")
    monkeypatch.setattr(builder, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(builder, "get_user_settings", UserSettings)
    monkeypatch.setattr(builder, "job_logger", lambda _: SimpleNamespace(
        **{name: lambda *args: None for name in ("info", "warning", "error", "exception")}))
    monkeypatch.setattr(builder, "close_job_logger", lambda _: None)
    for name in ("_maybe_sweep_orphans", "_maybe_schedule_plex_refresh"):
        monkeypatch.setattr(builder, name, lambda *args: None)

    async def nothing(*args, **kwargs):
        return {}

    for name in ("resolve_preferred_artwork_movie", "download_movie_canonical", "hydrate_ratings"):
        monkeypatch.setattr(builder, name, nothing)

    async def details(*args, **kwargs):
        return {"id": 42, "name": "Movie", "characters": [{"personName": "Lead"}],
                "credits": {"cast": [{"name": "Lead"}]}}

    async def hydrate_tmdb(record, *, force):
        assert force is True
        record["credits"]["cast"][0]["profile_path"] = "/portrait.jpg"

    async def hydrate_tvdb(people, **kwargs):
        people[0]["personImgURL"] = "https://images.example/portrait.jpg"

    async def fill_ratings(record, **kwargs):
        assert kwargs["kind"] == "movie" and kwargs["provider"] == provider and kwargs["force"] is True
        record["_ratings"] = {"imdb": {"value": 8.1, "max": 10}}

    downloads = []

    async def download(url, dest, **kwargs):
        if url:
            downloads.append(dest)
        return bool(url)

    monkeypatch.setattr(builder, "_download_url", download)
    monkeypatch.setattr(builder, "hydrate_ratings", fill_ratings)
    monkeypatch.setattr(builder, "_hydrate_tvdb_character_thumbs", hydrate_tvdb)
    monkeypatch.setattr(builder, "get_client", lambda: SimpleNamespace(movie_extended=details, best_translation=nothing))
    monkeypatch.setattr(builder, "get_tmdb_client", lambda: SimpleNamespace(
        movie_details=details, hydrate_credits=hydrate_tmdb))
    job_id = asyncio.run(builder.build_movie(folder, force=True))
    assert builder.get_job(job_id)["status"] == "completed", builder.get_job(job_id)["messages"]
    assert ET.parse(video.with_suffix(".nfo")).findtext("actor/thumb").endswith("/portrait.jpg")
    assert ET.parse(video.with_suffix(".nfo")).findtext("ratings/rating[@name='imdb']/value") == "8.1"
    assert folder / ".actors" / "Lead.jpg" in downloads


@pytest.mark.parametrize("provider", ["tmdb", "tvdb"])
def test_failed_episode_portrait_does_not_hide_working_series_portrait(tmp_path, monkeypatch, provider):
    calls = []

    async def download(url, dest, **kwargs):
        calls.append(url)
        return "good" in url

    monkeypatch.setattr(builder, "_download_url", download)
    download_people = getattr(builder, f"_download_actor_portraits_{provider}")
    seen = set()
    log = SimpleNamespace(info=lambda *args: None)

    def person(url):
        return ({"name": "Lead", "profile_path": url} if provider == "tmdb"
                else {"personName": "Lead", "personImgURL": url})

    async def run():
        bad = person("https://images.example/bad.jpg")
        await download_people(tmp_path, [bad, bad], force=True, log=log, seen=seen)
        assert seen == set()
        good = person("https://images.example/good.jpg")
        await download_people(tmp_path, [good], force=True, log=log, seen=seen)
        await download_people(tmp_path, [good], force=True, log=log, seen=seen)
        assert seen == {"Lead"}
        assert len(calls) == 2
    asyncio.run(run())
