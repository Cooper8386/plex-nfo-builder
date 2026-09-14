"""Wipe generated NFOs and artwork from a media folder, leaving season
folders and media files alone. Returns a summary of what was removed.

What is removed:
  - Show / movie .nfo (tvshow.nfo, <movie>.nfo at the folder level)
  - Movie-level companion files: <movie>-thumb.jpg/png next to the video
  - Episode .nfo and season.nfo inside Season XX/ subfolders
  - Per-episode Plex thumbnails: <episode-stem>-thumb.{jpg,jpeg,png}
    sitting next to the video file
  - Show-level artwork: poster.jpg/png, background.jpg/png, fanart.jpg,
    banner.jpg/png, clearlogo.png, folder.jpg, cover.jpg, Season<NN>-poster.jpg,
    season-specials-poster.jpg (Plex's season-0 filename), and the legacy
    Season00-poster.jpg written by older versions
  - Per-season artwork: <season-dir>/poster.jpg, <season-dir>/banner.jpg
  - The .plex-nfo-builder.json sidecar (optional, controlled by `keep_sidecar`)

What is NEVER removed:
  - Season folders themselves
  - Any video / audio / subtitle files
  - Sub-files we don't recognize
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from loguru import logger

from .artwork import is_season_poster_filename
from .parser import detect_season_dirs, is_video, is_within_folder


# Top-level artwork filenames Plex / nfo-builder writes.
SHOW_ARTWORK = {
    "poster.jpg", "poster.png",
    "background.jpg", "background.png",
    "fanart.jpg", "fanart.png",
    "banner.jpg", "banner.png",
    "clearlogo.png", "clearlogo.jpg",
    "folder.jpg", "cover.jpg",
}

SEASON_ARTWORK = {
    "poster.jpg", "poster.png",
    "banner.jpg", "banner.png",
    "fanart.jpg",
}

# File-name suffixes that indicate a Plex / Kodi thumbnail. Any image file
# ending in one of these is treated as a generated thumbnail and wiped.
# We don't require the stem to match a current video file - that lets us
# clean up orphan thumbs left behind by older filenames.
_THUMB_SUFFIXES = ("-thumb.jpg", "-thumb.jpeg", "-thumb.png")


def _is_thumb_filename(name: str) -> bool:
    """True if ``name`` ends with a recognised thumbnail suffix."""
    low = name.lower()
    return any(low.endswith(sfx) for sfx in _THUMB_SUFFIXES)


def clean_folder(folder: Path, *, keep_sidecar: bool = True) -> dict:
    """Delete generated NFOs and artwork. Returns counts by category.

    `keep_sidecar=True` preserves `.plex-nfo-builder.json` so the next
    scan can restore the binding + overrides without the user having to
    re-bind. Pass False to wipe it as well.
    """
    if not folder.is_dir():
        raise FileNotFoundError(str(folder))

    summary: dict = {
        "nfo_deleted": 0,
        "artwork_deleted": 0,
        "sidecar_deleted": 0,
        "files": [],  # list[str]
        "failed": [],
    }

    for p, kind in _clean_candidates(folder, keep_sidecar=keep_sidecar):
        try:
            if not is_within_folder(p.parent, folder):
                raise ValueError("cleanup target outside media folder")
            p.unlink()
            summary[f"{kind}_deleted"] += 1
            summary["files"].append(str(p.relative_to(folder)))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            summary["failed"].append({"path": str(p.relative_to(folder)), "reason": str(e)})
            logger.warning("clean: could not delete {}: {}", p, e)

    return summary


def _clean_candidates(folder: Path, *, keep_sidecar: bool = True) -> Iterator[tuple[Path, str]]:
    """Use the same file classification for previews and execution."""
    for directory in [folder, *detect_season_dirs(folder)]:
        artwork = SHOW_ARTWORK if directory == folder else SEASON_ARTWORK
        for entry in directory.iterdir():
            if not entry.is_file() or is_video(entry):
                continue
            if entry.suffix.lower() == ".nfo":
                yield entry, "nfo"
            elif (entry.name.lower() in artwork or _is_thumb_filename(entry.name)
                  or (directory == folder and is_season_poster_filename(entry.name))):
                yield entry, "artwork"
            elif not keep_sidecar and directory == folder and entry.name == ".plex-nfo-builder.json":
                yield entry, "sidecar"


def preview_clean(folder: Path, *, keep_sidecar: bool = True) -> list[str]:
    """Return relative paths that would be deleted by clean_folder()."""
    if not folder.is_dir():
        return []
    return [str(path.relative_to(folder)) for path, _ in _clean_candidates(folder, keep_sidecar=keep_sidecar)]


__all__ = ("clean_folder", "preview_clean")
