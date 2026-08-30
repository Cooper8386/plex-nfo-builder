from pathlib import Path

import pytest

from app.services import scanner
from app.services.orphans import (
    _count_directory_orphans,
    _strip_thumb_suffix,
    _sweep_directory,
    count_movie_orphans,
    count_series_orphans,
    preview_movie_orphans,
    preview_series_orphans,
    sweep_movie_orphans,
    sweep_series_orphans,
)


# ---- _strip_thumb_suffix ----------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("S01E01 - Foo-thumb.jpg", "S01E01 - Foo"),
    ("Ep-thumb.jpeg", "Ep"),
    ("Ep-thumb.png", "Ep"),
    ("EP-THUMB.JPG", "EP"),          # case-insensitive match, original case kept
    ("poster.jpg", None),
    ("thumb.jpg", None),             # no "-thumb" boundary
    ("Ep-thumb.gif", None),          # unknown extension
])
def test_strip_thumb_suffix(name, expected):
    assert _strip_thumb_suffix(name) == expected


# ---- sweep_series_orphans ---------------------------------------------------

def _series_tree(touch):
    """Season 01 with one live video; one orphan nfo + thumb pair; protected files."""
    touch("Show (2020)/Season 01/Show - S01E01 [Grp].mkv")
    touch("Show (2020)/Season 01/Show - S01E01 [Grp].nfo", "x")
    touch("Show (2020)/Season 01/Show - S01E01 [Grp]-thumb.jpg")
    touch("Show (2020)/Season 01/Show - S01E01 [Old].nfo", "x")       # orphan
    touch("Show (2020)/Season 01/Show - S01E01 [Old]-thumb.jpg")      # orphan
    touch("Show (2020)/Season 01/season.nfo", "x")                    # protected
    touch("Show (2020)/Season 01/Show - S01E01 [Grp].srt")            # untouched
    touch("Show (2020)/tvshow.nfo", "x")                              # root not swept
    touch("Show (2020)/poster.jpg")


def test_sweep_series_removes_only_orphans(tmp_path, touch):
    _series_tree(touch)
    folder = tmp_path / "Show (2020)"
    summary = sweep_series_orphans(folder)
    assert summary["nfo_removed"] == 1
    assert summary["thumb_removed"] == 1
    assert sorted(summary["files"]) == [
        str(Path("Season 01/Show - S01E01 [Old]-thumb.jpg")),
        str(Path("Season 01/Show - S01E01 [Old].nfo")),
    ]
    season = folder / "Season 01"
    assert (season / "Show - S01E01 [Grp].nfo").exists()
    assert (season / "Show - S01E01 [Grp]-thumb.jpg").exists()
    assert (season / "season.nfo").exists()
    assert (season / "Show - S01E01 [Grp].srt").exists()
    assert (folder / "tvshow.nfo").exists()
    assert not (season / "Show - S01E01 [Old].nfo").exists()
    assert not (season / "Show - S01E01 [Old]-thumb.jpg").exists()


def test_sweep_series_raises_on_missing_folder(tmp_path):
    with pytest.raises(FileNotFoundError):
        sweep_series_orphans(tmp_path / "nope")


def test_sweep_series_root_video_fallback(tmp_path, touch):
    touch("OVA/[Grp] OVA - 01.mkv")
    touch("OVA/[Grp] OVA - 01.nfo", "x")
    touch("OVA/[Grp] OVA - 00.nfo", "x")  # orphan at root
    summary = sweep_series_orphans(tmp_path / "OVA")
    assert summary["nfo_removed"] == 1
    assert (tmp_path / "OVA/[Grp] OVA - 01.nfo").exists()
    assert not (tmp_path / "OVA/[Grp] OVA - 00.nfo").exists()


def test_sweep_series_no_seasons_no_videos_is_noop(tmp_path, touch):
    touch("Empty/old.nfo", "x")
    summary = sweep_series_orphans(tmp_path / "Empty")
    assert summary == {"nfo_removed": 0, "thumb_removed": 0, "files": []}
    assert (tmp_path / "Empty/old.nfo").exists()


def test_preview_series_is_non_destructive(tmp_path, touch):
    _series_tree(touch)
    folder = tmp_path / "Show (2020)"
    summary = preview_series_orphans(folder)
    assert summary["nfo_removed"] == 1
    assert summary["thumb_removed"] == 1
    assert (folder / "Season 01/Show - S01E01 [Old].nfo").exists()
    assert (folder / "Season 01/Show - S01E01 [Old]-thumb.jpg").exists()


# ---- sweep_movie_orphans ----------------------------------------------------

def test_sweep_movie_removes_orphans(tmp_path, touch):
    touch("Movie (2020)/Movie (2020) [New].mkv")
    touch("Movie (2020)/Movie (2020) [New].nfo", "x")
    touch("Movie (2020)/Movie (2020) [Old].nfo", "x")
    touch("Movie (2020)/Movie (2020) [Old]-thumb.jpg")
    summary = sweep_movie_orphans(tmp_path / "Movie (2020)")
    assert summary["nfo_removed"] == 1
    assert summary["thumb_removed"] == 1
    assert (tmp_path / "Movie (2020)/Movie (2020) [New].nfo").exists()


def test_sweep_movie_refuses_without_live_video(tmp_path, touch):
    # "downloads haven't finished" safety: no video => no sweep at all
    touch("Movie (2020)/Movie (2020) [Old].nfo", "x")
    touch("Movie (2020)/Movie (2020) [Old]-thumb.jpg")
    summary = sweep_movie_orphans(tmp_path / "Movie (2020)")
    assert summary == {"nfo_removed": 0, "thumb_removed": 0, "files": []}
    assert (tmp_path / "Movie (2020)/Movie (2020) [Old].nfo").exists()
    assert (tmp_path / "Movie (2020)/Movie (2020) [Old]-thumb.jpg").exists()
    assert count_movie_orphans(tmp_path / "Movie (2020)") == 0


def test_preview_movie_is_non_destructive(tmp_path, touch):
    touch("Movie (2020)/Movie (2020).mkv")
    touch("Movie (2020)/stale.nfo", "x")
    summary = preview_movie_orphans(tmp_path / "Movie (2020)")
    assert summary["nfo_removed"] == 1
    assert (tmp_path / "Movie (2020)/stale.nfo").exists()


# ---- count/sweep/scanner triplicated logic must agree -----------------------

def test_orphan_counting_implementations_agree(tmp_path, touch):
    video = touch("M/Live.mkv")
    touch("M/Live.nfo", "x")
    touch("M/Live-thumb.jpg")
    touch("M/Gone.nfo", "x")
    touch("M/Gone-thumb.png")
    touch("M/season.nfo", "x")
    touch("M/poster.jpg")
    folder = tmp_path / "M"
    stems = {video.stem}

    count = _count_directory_orphans(folder, stems)
    summary = {"nfo_removed": 0, "thumb_removed": 0, "files": []}
    _sweep_directory(folder, folder, stems, summary, dry_run=True)
    inline = scanner._count_movie_orphans_inline(folder, [video])

    assert count == 2
    assert summary["nfo_removed"] + summary["thumb_removed"] == count
    assert inline == count
    assert count_movie_orphans(folder) == count
    # root-video fallback path counts the same orphans
    assert count_series_orphans(folder) == count
