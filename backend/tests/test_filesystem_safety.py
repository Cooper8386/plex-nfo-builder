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
from app.services import builder, cleaner, matcher, media_files, mediainfo, nfo, orphans, parser, renamer, scanner, sidecar


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.sqlite")
    monkeypatch.setattr(db, "_conn", None)
    yield db.conn()
    db.conn().close()


def _plan(folder, source="Old.mkv", destination="New.mkv"):
    return renamer.RenamePlanItem(str(folder), str(folder / source), str(folder / destination), 1, 1, None)


def test_rename_preserves_destination_created_after_preview(tmp_path, touch, isolated_db):
    source = touch("Old.mkv", "source")
    plan = _plan(tmp_path)
    target = touch("New.mkv", "new download")
    result = renamer.apply_rename_plan([plan])
    assert result["skipped"] and not result["renamed"]
    assert source.read_text() == "source"
    assert target.read_text() == "new download"


def test_rename_companion_conflict_keeps_entire_family(tmp_path, touch, isolated_db):
    for name in ("Old.mkv", "Old.nfo", "New.nfo", "Old.en.srt"):
        touch(name, name)
    result = renamer.apply_rename_plan([_plan(tmp_path)])
    assert result["skipped"] and not result["renamed"]
    assert not (tmp_path / "New.mkv").exists()
    assert (tmp_path / "New.nfo").read_text() == "New.nfo"
    assert (tmp_path / "Old.en.srt").exists()


def test_rename_moves_companions_and_mapping_together(tmp_path, touch, isolated_db):
    for name in ("Old.mkv", "Old.nfo", "Old-thumb.jpg", "Old.en.forced.srt"):
        touch(name, name)
    db.set_episode_file_override(str(tmp_path), str(tmp_path / "Old.mkv"), 2, 7, "42")
    result = renamer.apply_rename_plan([_plan(tmp_path)])
    assert len(result["renamed"]) == 1 and len(result["companions_moved"]) == 3
    assert not result["failed"]
    assert db.get_episode_file_overrides(str(tmp_path))[str(tmp_path / "New.mkv")]["external_id"] == "42"
    for suffix in (".mkv", ".nfo", "-thumb.jpg", ".en.forced.srt"):
        assert (tmp_path / f"New{suffix}").exists()
        assert not (tmp_path / f"Old{suffix}").exists()


def test_rename_rolls_back_files_when_companion_move_fails(tmp_path, touch, isolated_db, monkeypatch):
    touch("Old.mkv", "video")
    touch("Old.nfo", "metadata")
    move = renamer._rename_without_overwrite

    def fail_companion(source, target):
        if source.name == "Old.nfo":
            raise PermissionError("share temporarily unavailable")
        move(source, target)

    monkeypatch.setattr(renamer, "_rename_without_overwrite", fail_companion)
    result = renamer.apply_rename_plan([_plan(tmp_path)])
    assert result["failed"] and not result["renamed"]
    assert (tmp_path / "Old.mkv").read_text() == "video"
    assert (tmp_path / "Old.nfo").read_text() == "metadata"
    assert not (tmp_path / "New.mkv").exists()


@pytest.mark.parametrize("platform", ["native", "posix"])
def test_atomic_move_never_overwrites_existing_destination(tmp_path, touch, monkeypatch, platform):
    source, target = touch("Old.mkv", "old"), touch("New.mkv", "new")
    if platform == "posix":
        monkeypatch.setattr(renamer, "os", SimpleNamespace(name="posix", link=os.link))
    with pytest.raises(FileExistsError):
        renamer._rename_without_overwrite(source, target)
    assert source.read_text() == "old" and target.read_text() == "new"


def test_posix_move_preserves_source_if_unlink_fails(tmp_path, touch, monkeypatch):
    source, target = touch("Old.mkv", "video"), tmp_path / "New.mkv"
    monkeypatch.setattr(renamer, "os", SimpleNamespace(name="posix", link=os.link))
    unlink = Path.unlink

    def fail_source(path, *args, **kwargs):
        if path == source:
            raise PermissionError("read-only source")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_source)
    with pytest.raises(PermissionError):
        renamer._rename_without_overwrite(source, target)
    assert source.read_text() == "video" and not target.exists()


def test_rename_rejects_source_outside_declared_folder(tmp_path, touch):
    source = touch("Outside/Old.mkv", "protected")
    folder = tmp_path / "Library"
    folder.mkdir()
    item = _plan(source.parent)
    item.folder_path = str(folder)
    result = renamer.apply_rename_plan([item])
    assert result["failed"] and source.exists()


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


def _series_plan(folder, monkeypatch, metadata, overrides=None):
    monkeypatch.setattr(mediainfo, "probe_file", lambda path: mediainfo.MediaInfo())
    return renamer.plan_series_rename(
        folder, standard_template="Standard S{season:00}E{episode:00} {Episode Title}",
        daily_template="Daily {Air-Date} {Episode Title}", anime_template="Anime {Episode Title}",
        series_type="auto", title="Show", year=2020, episodes_by_se=metadata, overrides_by_file=overrides or {},
    )


def test_auto_rename_does_not_confuse_metadata_air_date_with_daily_filename(tmp_path, touch, monkeypatch):
    touch("Season 01/Show - S01E01.mkv")
    plan = _series_plan(tmp_path, monkeypatch, {(1, 1): {"id": 42, "name": "Pilot", "aired": "2020-01-01"}})
    assert Path(plan[0].dst).name == "Standard S01E01 Pilot.mkv"


def test_daily_rename_matches_provider_episode_by_air_date(tmp_path, touch, monkeypatch):
    touch("Season 01/Show - 2020-01-01.mkv")
    plan = _series_plan(tmp_path, monkeypatch, {(1, 7): {"id": 42, "name": "Daily", "aired": "2020-01-01"}})
    assert plan[0].episode == 7 and plan[0].matched_title == "Daily"
    assert Path(plan[0].dst).name == "Daily 2020-01-01 Daily.mkv"


def test_rename_respects_explicit_provider_episode_id(tmp_path, touch, monkeypatch):
    video = touch("Season 01/Show - S01E01.mkv")
    metadata = {(1, 1): {"id": 41, "name": "Wrong"}, (2, 7): {"id": 42, "name": "Chosen"}}
    plan = _series_plan(tmp_path, monkeypatch, metadata, {str(video): {"external_id": "42"}})
    assert Path(plan[0].dst).name == "Standard S02E07 Chosen.mkv"


def test_rename_preview_marks_companion_collision(tmp_path, touch, monkeypatch):
    touch("Season 01/Show - S01E01.mkv")
    touch("Season 01/Show - S01E01.nfo")
    touch("Season 01/Standard S01E01 Pilot.nfo", "existing metadata")
    plan = _series_plan(tmp_path, monkeypatch, {(1, 1): {"id": 42, "name": "Pilot"}})
    assert plan[0].conflict == "exists"


def test_rename_preserves_multi_episode_range(tmp_path, touch, monkeypatch):
    touch("Season 01/Show - S01E01-E02.mkv")
    plan = _series_plan(tmp_path, monkeypatch, {(1, 1): {"id": 42, "name": "Pilot"}})
    assert Path(plan[0].dst).name == "Standard S01E01-E02 Pilot.mkv"


def test_nfo_strips_invalid_xml_characters_without_losing_unicode():
    root = ET.Element("movie")
    nfo._el(root, "title", "A\x00\x0b & 東京")
    text = nfo._pretty(root, {"tvdb_id": "42----><injected/>"})
    parsed = ET.fromstring(text)
    assert parsed.findtext("title") == "A & 東京"
    assert parsed.find("injected") is None


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


def test_probe_cache_invalidates_changes_inside_same_second(tmp_path, touch, monkeypatch):
    video = touch("Video.mkv", "one")
    mediainfo.clear_cache()
    monkeypatch.setattr(mediainfo, "_ffprobe_bin", lambda: "ffprobe")
    calls = []

    def probe(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='{"streams": []}')

    monkeypatch.setattr(mediainfo.subprocess, "run", probe)
    mediainfo.probe_file(video)
    first_stamp = video.stat().st_mtime_ns
    video.write_text("new release", encoding="utf-8")
    os.utime(video, ns=(first_stamp, first_stamp))
    mediainfo.probe_file(video)
    mediainfo.probe_file(video)
    assert len(calls) == 2
    mediainfo.clear_cache()


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
                 "_download_actor_portraits_tvdb", "_download_actor_portraits_tmdb", "_hydrate_tvdb_character_thumbs"):
        monkeypatch.setattr(builder, name, nothing)
    season_calls = []

    async def tvdb_series(*args, **kwargs):
        return {"id": 42, "name": "Show", "seasons": []}

    async def tvdb_episodes(*args, **kwargs):
        return [{"id": 101, "seasonNumber": 1, "number": 1, "name": "Wrong"},
                {"id": 107, "seasonNumber": 2, "number": 7, "name": "Chosen", "aired": "2020-01-07"}]

    async def tvdb_episode(identifier, **kwargs):
        return next(episode for episode in await tvdb_episodes() if episode["id"] == identifier)

    async def tmdb_series(*args, **kwargs):
        return {"id": 42, "name": "Show", "seasons": [{"season_number": 1}, {"season_number": 2}]}

    async def tmdb_season(identifier, number, **kwargs):
        season_calls.append(number)
        episode = ({"id": 101, "season_number": 1, "episode_number": 1, "name": "Wrong"} if number == 1 else
                   {"id": 107, "season_number": 2, "episode_number": 7, "name": "Chosen", "air_date": "2020-01-07"})
        return {"episodes": [episode]}

    monkeypatch.setattr(builder, "get_client", lambda: SimpleNamespace(
        series_extended=tvdb_series, series_episodes=tvdb_episodes, episode_extended=tvdb_episode, best_translation=nothing))
    monkeypatch.setattr(builder, "get_tmdb_client", lambda: SimpleNamespace(tv_details=tmdb_series, tv_season=tmdb_season))
    job_id = asyncio.run(builder.build_series(folder, force=True))
    job = builder.get_job(job_id)
    assert job["status"] == "completed", job["messages"]
    generated = ET.parse(video.with_suffix(".nfo")).getroot()
    assert generated.findtext("title") == "Chosen"
    assert generated.findtext("uniqueid") == "107"
    assert generated.find("uniqueid").get("type") == provider
    assert scanner._scan_nfo_state(folder, 1, "series")[0] == "complete"
    assert len(season_calls) == len(set(season_calls))
    assert matching_calls == ([] if binding_mode == "bound" else [provider])
