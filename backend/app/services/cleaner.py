"""Wipe generated NFOs and artwork from a media folder, leaving season
folders and media files alone. Returns a summary of what was removed.

What is removed:
  - Show / movie .nfo (tvshow.nfo, <movie>.nfo at the folder level)
  - Episode .nfo and season.nfo inside Season XX/ subfolders
  - Show-level artwork: poster.jpg/png, background.jpg/png, fanart.jpg,
    banner.jpg/png, clearlogo.png, folder.jpg, cover.jpg, season<NN>-poster.jpg
  - Per-season artwork: <season-dir>/poster.jpg, <season-dir>/banner.jpg
  - The .plex-nfo-builder.json sidecar (optional, controlled by `keep_sidecar`)

What is NEVER removed:
  - Season folders themselves
  - Any video / audio / subtitle files
  - Sub-files we don't recognize
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from loguru import logger

from .scanner import detect_season_dirs, is_video


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


def clean_folder(folder: Path, *, keep_sidecar: bool = True) -> dict:
    """Delete generated NFOs and artwork. Returns counts by category.

    `keep_sidecar=True` preserves `.plex-nfo-builder.json` so the next
    scan can restore the binding + overrides without the user having to
    re-bind. Pass False to wipe it as well.
    """
    if not folder.is_dir():
        raise FileNotFoundError(str(folder))

    summary = {
        "nfo_deleted": 0,
        "artwork_deleted": 0,
        "sidecar_deleted": 0,
        "files": [],  # list[str]
    }

    def _remove(p: Path, kind: str) -> None:
        try:
            p.unlink()
            summary[f"{kind}_deleted"] += 1
            summary["files"].append(str(p.relative_to(folder)))
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("clean: could not delete {}: {}", p, e)

    # 1. Folder-level NFOs (tvshow.nfo + movie .nfo files)
    for f in folder.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() == ".nfo":
            _remove(f, "nfo")
        elif f.name in SHOW_ARTWORK:
            _remove(f, "artwork")
        elif f.name.startswith("Season") and f.name.endswith("-poster.jpg"):
            _remove(f, "artwork")
        elif keep_sidecar is False and f.name == ".plex-nfo-builder.json":
            _remove(f, "sidecar")

    # 2. Season folders: episode .nfo + season.nfo + season-level artwork
    for sd in detect_season_dirs(folder):
        for f in sd.iterdir():
            if not f.is_file():
                continue
            if is_video(f):
                continue
            if f.suffix.lower() == ".nfo":
                _remove(f, "nfo")
            elif f.name.lower() in SEASON_ARTWORK:
                _remove(f, "artwork")

    return summary


def preview_clean(folder: Path) -> list[str]:
    """Return relative paths that would be deleted by clean_folder()."""
    if not folder.is_dir():
        return []
    out: list[str] = []
    for f in folder.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() == ".nfo" or f.name in SHOW_ARTWORK:
            out.append(f.name)
        elif f.name.startswith("Season") and f.name.endswith("-poster.jpg"):
            out.append(f.name)
    for sd in detect_season_dirs(folder):
        for f in sd.iterdir():
            if not f.is_file() or is_video(f):
                continue
            if f.suffix.lower() == ".nfo" or f.name.lower() in SEASON_ARTWORK:
                out.append(str(f.relative_to(folder)))
    return out


__all__: Iterable[str] = ("clean_folder", "preview_clean")
