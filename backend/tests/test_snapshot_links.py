"""Legacy artwork links become portable files without escaping the selected library."""
import os
import stat
import zipfile

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


@pytest.mark.parametrize("kind", ["other_library", "outside", "broken", "directory", "cycle", "special"])
def test_unsafe_links_fail_without_publishing(library, tmp_path, kind):
    target = library / "target.png"
    if kind == "other_library":
        target = library.parent / "Movies" / "poster.png"
        target.parent.mkdir()
    elif kind == "outside":
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
    elif kind not in {"broken", "cycle"}:
        target.write_bytes(b"private artwork")
    link(library / "clearart.png", target, directory=kind == "directory")
    with pytest.raises((OSError, ValueError)):
        snapshots.create_snapshot("TV", {"progress": 0})
    assert not list(snapshots.storage_path("TV").iterdir())


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
