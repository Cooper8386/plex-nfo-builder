"""Legacy artwork links become portable files without escaping the selected library."""
import os
import json
import stat
import zipfile
from pathlib import Path

import pytest

from app.services import snapshots


@pytest.fixture
def library(tmp_path, monkeypatch):
    media = tmp_path / "media"
    folder = media / "TV"
    folder.mkdir(parents=True)
    monkeypatch.setattr(snapshots, "MEDIA_ROOT", media)
    monkeypatch.setattr(snapshots, "CONFIG_DIR", tmp_path / "config")
    return folder


def link(path, target, *, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"Symlink creation is not permitted on this host: {error}")


@pytest.mark.parametrize("absolute", [False, True])
def test_legacy_artwork_links_restore_as_regular_files(library, tmp_path, absolute):
    artwork = library / "Show" / ".artwork"
    artwork.mkdir(parents=True)
    target = artwork / "clearart.png"
    target.write_bytes(b"saved artwork")
    alias = artwork.parent / "clearart.png"
    link(alias, target if absolute else target.relative_to(artwork.parent))
    saved = snapshots.create_snapshot("TV", {"progress": 0})
    with zipfile.ZipFile(snapshots.download_path("TV", saved["id"])) as archive:
        assert set(archive.namelist()) == {"Show/.artwork/clearart.png", "Show/clearart.png"}
        info = archive.getinfo("Show/clearart.png")
        assert stat.S_ISREG(info.external_attr >> 16)
        restored = tmp_path / "restored"
        archive.extractall(restored)
    restored_alias = restored / "Show" / "clearart.png"
    assert not restored_alias.is_symlink()
    assert restored_alias.read_bytes() == b"saved artwork"


def test_media_links_and_disguised_media_targets_are_excluded(library):
    media = library / "Episode.MKV"
    media.write_bytes(b"actual media")
    artwork = library / "poster.png"
    artwork.write_bytes(b"artwork")
    link(library / "clearart.png", media)
    link(library / "linked-media.mkv", media)
    link(library / "artwork-named-media.MP4", artwork)
    saved = snapshots.create_snapshot("TV", {"progress": 0})
    with zipfile.ZipFile(snapshots.download_path("TV", saved["id"])) as archive:
        assert archive.namelist() == ["poster.png"]
        assert archive.read("poster.png") == b"artwork"


@pytest.mark.parametrize("kind", ["other_library", "outside", "missing_outside", "directory", "cycle", "special"])
def test_unsafe_links_fail_without_publishing(library, tmp_path, kind):
    target = library / "target.png"
    if kind == "other_library":
        target = library.parent / "Movies" / "poster.png"
        target.parent.mkdir()
    elif kind in {"outside", "missing_outside"}:
        target = tmp_path / "private.png"
    elif kind == "cycle":
        target = library / "clearart.png"
    if kind == "directory":
        target.mkdir()
        (target / "poster.png").write_bytes(b"artwork")
    elif kind == "special":
        if not hasattr(os, "mkfifo"):
            pytest.skip("Named pipes require POSIX")
        os.mkfifo(target)
    elif kind not in {"missing_outside", "cycle"}:
        target.write_bytes(b"private artwork")
    link(library / "clearart.png", target, directory=kind == "directory")
    with pytest.raises((OSError, ValueError)):
        snapshots.create_snapshot("TV", {"progress": 0})
    assert not list(snapshots.storage_path("TV").iterdir())


def test_already_broken_artwork_link_warns_and_preserves_existing_metadata(library):
    show = library / "A Certain Scientific Railgun (2009) {tvdb-114921}"
    show.mkdir()
    alias = show / "clearart.png"
    link(alias, Path(".artwork") / "clearart" / "62754780-eng.png")
    (show / "tvshow.nfo").write_bytes(b"saved metadata")
    (show / "episode.mkv").write_bytes(b"media")
    job = {"progress": 0, "messages": []}
    saved = snapshots.create_snapshot("TV", job)
    assert saved["file_count"] == 1 and saved["skipped_link_count"] == 1
    assert job["progress"] == job["total"] == 1
    assert "Skipped broken link:" in job["messages"][0] and "clearart.png" in job["messages"][0]
    assert alias.is_symlink() and not alias.exists()  # Source library is untouched.
    with zipfile.ZipFile(snapshots.download_path("TV", saved["id"])) as archive:
        assert archive.namelist() == [f"{show.name}/tvshow.nfo"]
        assert archive.read(archive.namelist()[0]) == b"saved metadata"
        assert json.loads(archive.comment)["skipped_link_count"] == 1
    assert snapshots.list_snapshots("TV")[0]["skipped_link_count"] == 1


@pytest.mark.parametrize("change", ["target_created", "link_removed", "link_retargeted", "valid_target_removed"])
def test_links_changing_during_snapshot_still_fail(library, monkeypatch, change):
    (library / "tvshow.nfo").write_bytes(b"metadata")
    target = library / "missing.png"
    if change == "valid_target_removed":
        target.write_bytes(b"artwork")
    alias = library / "clearart.png"
    link(alias, target)
    copy = snapshots.shutil.copyfileobj
    changed = False

    def copy_and_change(source, destination, **kwargs):
        nonlocal changed
        copy(source, destination, **kwargs)
        if changed:
            return
        changed = True
        if change == "target_created":
            target.write_bytes(b"new artwork")
        elif change == "link_removed":
            alias.unlink()
        elif change == "link_retargeted":
            alias.unlink()
            alias.symlink_to(library / "another-missing.png")
        else:
            target.unlink()

    monkeypatch.setattr(snapshots.shutil, "copyfileobj", copy_and_change)
    with pytest.raises((OSError, ValueError)):
        snapshots.create_snapshot("TV", {"progress": 0})
    assert not list(snapshots.storage_path("TV").glob("*.zip"))


def test_regular_file_disappearing_during_scan_is_not_skipped(library, monkeypatch):
    path = library / "tvshow.nfo"
    path.write_bytes(b"metadata")
    resolve = type(path).resolve

    def disappear(candidate, *args, **kwargs):
        if candidate == path and kwargs.get("strict"):
            path.unlink()
        return resolve(candidate, *args, **kwargs)

    monkeypatch.setattr(type(path), "resolve", disappear)
    with pytest.raises(FileNotFoundError):
        snapshots.create_snapshot("TV", {"progress": 0})
    assert not list(snapshots.storage_path("TV").glob("*.zip"))


@pytest.mark.parametrize("change", ["retarget_before_open", "retarget_during_copy", "target_content"])
def test_link_changes_do_not_publish_or_replace_saved_snapshot(library, monkeypatch, change):
    target = library / "target.png"
    target.write_bytes(b"saved artwork")
    replacement = library / "replacement.png"
    replacement.write_bytes(b"other artwork")
    alias = library / "clearart.png"
    link(alias, target)
    saved = snapshots.create_snapshot("TV", {"progress": 0})
    path = snapshots.download_path("TV", saved["id"])
    original_zip = path.read_bytes()
    original_files = snapshots._files
    original_copy = snapshots.shutil.copyfileobj

    def change_link():
        alias.unlink()
        alias.symlink_to(replacement)

    def files(folder):
        result = original_files(folder)
        change_link()
        return result

    def copy(source, destination, **kwargs):
        original_copy(source, destination, **kwargs)
        if change == "target_content":
            target.write_bytes(b"changed artwork contents")
        else:
            change_link()

    if change == "retarget_before_open":
        monkeypatch.setattr(snapshots, "_files", files)
    else:
        monkeypatch.setattr(snapshots.shutil, "copyfileobj", copy)
    with pytest.raises(ValueError, match="Library changed"):
        snapshots.create_snapshot("TV", {"progress": 0})
    assert path.read_bytes() == original_zip
    assert list(path.parent.iterdir()) == [path]
