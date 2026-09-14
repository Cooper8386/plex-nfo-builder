"""Snapshot round trips and failure boundaries use disposable libraries only."""
import asyncio
import shutil
import sqlite3
import zipfile
from datetime import datetime

import httpx
import pytest

from app import db, main
from app.services import jobs, snapshots


@pytest.fixture
def library(tmp_path, monkeypatch):
    media = tmp_path / "media"
    folder = media / "TV"
    folder.mkdir(parents=True)
    monkeypatch.setattr(snapshots, "MEDIA_ROOT", media)
    monkeypatch.setattr(snapshots, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(main, "_API_TOKEN", "snapshot-test")
    monkeypatch.setattr(jobs, "_jobs", {})
    connection = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    connection.row_factory = sqlite3.Row
    db._init_schema(connection)
    db._migrate(connection)
    monkeypatch.setattr(db, "_conn", connection)
    db.upsert_library("TV", "tv")
    db.upsert_library("Movies", "movies")
    yield folder
    connection.close()


def test_snapshot_api_round_trip_and_preservation(library):
    expected = {
        ".plex-nfo-builder.json": b'{"binding": "saved"}',
        "Show/Season 01/Episode.nfo": b"<episodedetails/>",
        "Show/poster.jpg": b"artwork",
        "Show/Season 01/Episode.en.srt": b"subtitle",
        "Show/.hidden/custom.data": b"other companion",
        "library.xml": b"library metadata",
    }
    for name, content in expected.items():
        path = library / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    media_extensions = (".mkv", ".mp4", ".avi", ".m2ts", ".iso", ".mp3", ".flac", ".hevc", ".ssif")
    for extension in media_extensions:
        (library / f"actual-media{extension.upper()}").write_bytes(b"media bytes")
    other = library.parent / "Movies"
    other.mkdir()
    (other / "movie.nfo").write_bytes(b"unrelated library")

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url="http://test") as client:
            url = "/api/libraries/TV/snapshots"
            assert (await client.post(url)).status_code == 401
            client.headers["X-API-Token"] = "snapshot-test"
            assert (await client.get(url)).json()["snapshots"] == []
            response = await client.post(url)
            assert response.status_code == 202
            await jobs.wait_build(response.json()["job_id"])
            listing = (await client.get(url)).json()
            assert listing["jobs"][0]["status"] == "completed"
            assert listing["jobs"][0]["progress"] == len(expected)
            saved = listing["snapshots"][0]
            assert saved["file_count"] == len(expected)
            created = datetime.fromisoformat(saved["created_at"])
            assert saved["filename"] == f"TV-{created.strftime('%Y-%m-%d_%H-%M-%S')}Z.zip"
            path = snapshots.download_path("TV", saved["id"])
            original_zip = path.read_bytes()
            with zipfile.ZipFile(path) as archive:
                assert archive.testzip() is None
                assert {name: archive.read(name) for name in archive.namelist()} == expected
                assert all(info.compress_type == zipfile.ZIP_DEFLATED for info in archive.infolist())
                # Simulate cleanup, then restore exactly into this library.
                for name in expected:
                    (library / name).unlink()
                archive.extractall(library)
            assert all((library / name).read_bytes() == content for name, content in expected.items())
            assert all((library / f"actual-media{ext.upper()}").read_bytes() == b"media bytes"
                       for ext in media_extensions)
            second = await client.post(url)
            await jobs.wait_build(second.json()["job_id"])
            assert len((await client.get(url)).json()["snapshots"]) == 2
            assert path.read_bytes() == original_zip
            # History survives in-memory job loss and even a removed library directory.
            jobs._jobs.clear()
            shutil.rmtree(library)
            downloaded = await client.get(f"{url}/{saved['id']}/download")
            assert downloaded.status_code == 200 and downloaded.content == original_zip
            assert downloaded.headers["content-type"] == "application/zip"
            assert downloaded.headers["content-disposition"] == f'attachment; filename="{saved["filename"]}"'
            assert len((await client.get(url)).json()["snapshots"]) == 2
            assert (await client.post(url)).status_code == 404
            assert (await client.get(f"/api/libraries/Movies/snapshots/{saved['id']}/download")).status_code == 404
            assert (await client.get(f"{url}/invalid/download")).status_code == 400
            assert (await client.post("/api/libraries/missing/snapshots")).status_code == 404
            client.headers.clear()
            assert (await client.get(f"{url}/{saved['id']}/download")).status_code == 401
        await jobs.shutdown_builds()

    asyncio.run(run())


@pytest.mark.parametrize("name", ["", ".", "..", "../TV", "TV/Show", "TV\\Show", "C:media"])
def test_snapshot_scope_rejects_root_and_traversal(library, name):
    with pytest.raises(ValueError, match="single library"):
        snapshots.library_path(name)


def test_storage_inside_media_is_rejected(library, monkeypatch):
    monkeypatch.setattr(snapshots, "CONFIG_DIR", library / "config")
    with pytest.raises(ValueError, match="outside MEDIA_ROOT"):
        snapshots.create_snapshot("TV", {"progress": 0})
    assert not (library / "config").exists()


@pytest.mark.parametrize("failure", ["read", "changed", "added", "walk", "flush"])
def test_failed_snapshot_never_publishes_partial_or_changes_saved_zip(library, monkeypatch, failure):
    path = library / "tvshow.nfo"
    path.write_bytes(b"original")
    saved = snapshots.create_snapshot("TV", {"progress": 0})
    archive = snapshots.download_path("TV", saved["id"])
    original = archive.read_bytes()
    copy = snapshots.shutil.copyfileobj

    def broken_copy(source, target, **kwargs):
        if failure == "read":
            raise OSError("disk read failed")
        copy(source, target, **kwargs)
        if failure == "changed":
            path.write_bytes(b"changed during snapshot")
        if failure == "added":
            (library / "new.nfo").write_bytes(b"added during snapshot")

    def unreadable_walk(folder, *, onerror, **kwargs):
        onerror(PermissionError("unreadable directory"))
        return iter(())

    def failed_flush(descriptor):
        raise OSError("disk full")

    monkeypatch.setattr(snapshots.shutil, "copyfileobj", broken_copy)
    if failure == "walk":
        monkeypatch.setattr(snapshots.os, "walk", unreadable_walk)
    if failure == "flush":
        monkeypatch.setattr(snapshots.os, "fsync", failed_flush)
    with pytest.raises((OSError, ValueError)):
        snapshots.create_snapshot("TV", {"progress": 0})
    assert archive.read_bytes() == original
    assert list(archive.parent.iterdir()) == [archive]


@pytest.mark.parametrize("directory", [False, True])
def test_symlinks_fail_snapshot_instead_of_silently_omitting_files(library, tmp_path, directory):
    target = tmp_path / "private"
    if directory:
        target.mkdir()
        (target / "secret.nfo").write_text("secret")
    else:
        target.write_text("secret")
    try:
        (library / "link").symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("Symlink creation is not permitted on this host")
    with pytest.raises(ValueError, match="symbolic links"):
        snapshots.create_snapshot("TV", {"progress": 0})
    assert not list(snapshots.storage_path("TV").iterdir())


def test_background_snapshot_coalesces_clicks_and_reports_failure(library, monkeypatch):
    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(snapshots, "create_snapshot", fail)

    async def run():
        first = snapshots.start_snapshot("TV")
        assert snapshots.start_snapshot("TV") == first
        await jobs.wait_build(first)
        job = jobs.get_job(first)
        assert job["status"] == "error" and job["finished_at"] is not None
        assert "disk full" in job["messages"][-1]
        await jobs.shutdown_builds()

    asyncio.run(run())


def test_empty_library_produces_valid_empty_zip(library):
    saved = snapshots.create_snapshot("TV", {"progress": 0})
    assert saved["file_count"] == 0
    with zipfile.ZipFile(snapshots.download_path("TV", saved["id"])) as archive:
        assert archive.namelist() == []


@pytest.mark.parametrize("comment", [b"null", b"[]", b"not json"])
def test_damaged_snapshot_metadata_does_not_break_history(library, comment):
    saved = snapshots.create_snapshot("TV", {"progress": 0})
    with zipfile.ZipFile(snapshots.download_path("TV", saved["id"]), "a") as archive:
        archive.comment = comment
    assert snapshots.list_snapshots("TV") == []
