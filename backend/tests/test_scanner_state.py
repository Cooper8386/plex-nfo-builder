import hashlib
from pathlib import Path

from app.services.scanner import (
    PROVENANCE_TAG,
    _scan_nfo_state,
    _scan_series_state,
    hash_text,
)

OURS = f"{PROVENANCE_TAG} -->\n<episodedetails/>"
FOREIGN = "<episodedetails/>"


class FakeEp:
    """_scan_series_state only reads ep.path.stem."""
    def __init__(self, path: Path):
        self.path = path


def _season(tmp_path, n_eps: int, nfo_text=None, orphans=0):
    """Build Season 01 with n_eps fake videos; optional nfo per ep; extra orphan nfos."""
    sd = tmp_path / "Show" / "Season 01"
    sd.mkdir(parents=True, exist_ok=True)
    eps = []
    for i in range(1, n_eps + 1):
        stem = f"Show - S01E{i:02d}"
        eps.append(FakeEp(sd / f"{stem}.mkv"))  # video need not exist on disk
        if nfo_text is not None:
            (sd / f"{stem}.nfo").write_text(nfo_text, encoding="utf-8")
    for i in range(orphans):
        (sd / f"Gone {i}.nfo").write_text(OURS, encoding="utf-8")
    return tmp_path / "Show", [(sd, eps)]


def _tvshow(folder: Path, text: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tvshow.nfo").write_text(text, encoding="utf-8")


# ---- status ladder ----------------------------------------------------------

def test_status_none(tmp_path):
    folder, resolved = _season(tmp_path, 2)
    assert _scan_series_state(folder, resolved, [], 2) == ("none", False, 0, 0)


def test_status_none_still_counts_orphans(tmp_path):
    folder, resolved = _season(tmp_path, 2)
    (resolved[0][0] / "Old-thumb.jpg").write_bytes(b"")
    status, prov, nfo_eps, orphans = _scan_series_state(folder, resolved, [], 2)
    assert (status, nfo_eps, orphans) == ("none", 0, 1)


def test_status_complete(tmp_path):
    folder, resolved = _season(tmp_path, 2, nfo_text=OURS)
    _tvshow(folder, OURS)
    assert _scan_series_state(folder, resolved, [], 2) == ("complete", True, 2, 0)


def test_status_foreign(tmp_path):
    # every episode nfo lacks our provenance tag, no tvshow.nfo
    folder, resolved = _season(tmp_path, 2, nfo_text=FOREIGN)
    assert _scan_series_state(folder, resolved, [], 2) == ("foreign", False, 2, 0)


def test_status_partial(tmp_path):
    folder, resolved = _season(tmp_path, 1, nfo_text=OURS)
    _tvshow(folder, OURS)
    status, prov, nfo_eps, _ = _scan_series_state(folder, resolved, [], 2)
    assert (status, prov, nfo_eps) == ("partial", True, 1)


def test_status_mixed(tmp_path):
    # more NFOs than expected episodes
    folder, resolved = _season(tmp_path, 2, nfo_text=OURS)
    _tvshow(folder, OURS)
    status, prov, nfo_eps, _ = _scan_series_state(folder, resolved, [], 1)
    assert (status, nfo_eps) == ("mixed", 2)


# ---- provenance detection ---------------------------------------------------

def test_provenance_beyond_2000_chars_reads_as_foreign(tmp_path):
    padded = " " * 3000 + PROVENANCE_TAG
    folder, resolved = _season(tmp_path, 2, nfo_text=padded)
    status, prov, nfo_eps, _ = _scan_series_state(folder, resolved, [], 2)
    assert status == "foreign"
    assert prov is False


def test_season_nfo_ignored(tmp_path):
    folder, resolved = _season(tmp_path, 1, nfo_text=OURS)
    _tvshow(folder, OURS)
    (resolved[0][0] / "season.nfo").write_text(FOREIGN, encoding="utf-8")
    assert _scan_series_state(folder, resolved, [], 1) == ("complete", True, 1, 0)


def test_root_eps_layout_excludes_tvshow_nfo_from_episode_and_orphan_counts(tmp_path):
    # anime/OVA: videos at show root, no season dirs.
    folder = tmp_path / "OVA"
    folder.mkdir()
    _tvshow(folder, OURS)
    (folder / "Ep 01.nfo").write_text(OURS, encoding="utf-8")
    root_eps = [FakeEp(folder / "Ep 01.mkv")]
    assert _scan_series_state(folder, [], root_eps, 1) == ("complete", True, 1, 0)


# ---- back-compat shim + hash_text -------------------------------------------

def test_scan_nfo_state_shim_delegates(tmp_path, touch):
    touch("Show/Season 01/Show - S01E01.mkv")
    touch("Show/Season 01/Show - S01E01.nfo", OURS)
    touch("Show/tvshow.nfo", OURS)
    status, prov, nfo_eps = _scan_nfo_state(tmp_path / "Show", 1, "series")
    assert (status, prov, nfo_eps) == ("complete", True, 1)


def test_hash_text():
    assert hash_text("abc") == "sha256:" + hashlib.sha256(b"abc").hexdigest()
    assert hash_text("abc") == hash_text("abc")
