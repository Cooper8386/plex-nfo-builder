"""Library auto-detection and item state scanning."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from loguru import logger

from .. import db
from ..config import MEDIA_ROOT
from .sidecar import restore_from_sidecar
from .parser import (
    ParsedFolder,
    SeriesFolderScan,
    detect_season_dirs,
    is_video,
    list_season_episodes,
    parse_folder_name,
    parse_movie_filename,
    season_number_from_dir,
)


PROVENANCE_TAG = "<!-- plex-nfo-builder"


def detect_libraries(media_root: Optional[Path] = None) -> list[dict]:
    """Scan top-level dirs of /media and infer kind."""
    root = media_root or MEDIA_ROOT
    out: list[dict] = []
    if not root.exists():
        logger.warning("Media root does not exist: {}", root)
        return out
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        kind = _infer_library_kind(entry)
        db.upsert_library(entry.name, kind)
        out.append({"name": entry.name, "kind": kind, "path": str(entry)})
    return out


def _infer_library_kind(library_dir: Path) -> str:
    sample = []
    for c in library_dir.iterdir():
        if c.is_dir() and not c.name.startswith("."):
            sample.append(c)
        if len(sample) >= 12:
            break
    if not sample:
        return "mixed"
    has_seasons = 0
    movie_like = 0
    for d in sample:
        seasons = detect_season_dirs(d)
        if seasons:
            has_seasons += 1
            continue
        # also consider movie if any direct video file inside
        if any(is_video(f) for f in d.iterdir() if f.is_file()):
            movie_like += 1
    if has_seasons >= movie_like:
        return "tv"
    return "movies"


def scan_library(name: str) -> int:
    lib_path = MEDIA_ROOT / name
    if not lib_path.is_dir():
        return 0
    rows = db.list_libraries()
    kind = next((r["kind"] for r in rows if r["name"] == name), "mixed")
    count = 0
    for entry in sorted(lib_path.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if kind == "movies":
            scan_movie_folder(entry, library=name)
        else:
            scan_series_folder(entry, library=name)
        count += 1
    return count


def scan_series_folder(folder: Path, library: str) -> SeriesFolderScan:
    # Restore binding/overrides from a sidecar file if the DB has nothing yet.
    # Safe to call repeatedly; it no-ops when a binding already exists.
    try:
        restore_from_sidecar(folder)
    except Exception as e:
        logger.warning("sidecar restore failed for {}: {}", folder, e)
    pf = parse_folder_name(folder.name)
    seasons: dict[int, list] = {}
    total_eps = 0
    for sd in detect_season_dirs(folder):
        snum = season_number_from_dir(sd.name)
        eps = list_season_episodes(sd)
        if eps:
            seasons[snum] = eps
            total_eps += len(eps)

    nfo_state, has_prov, nfo_count = _scan_nfo_state(folder, total_eps, kind="series")
    poster = _local_poster_for(folder)
    binding = db.get_binding(str(folder))
    provider = pf.provider or (binding["provider"] if binding else None)
    eid = pf.external_id or (binding["external_id"] if binding else None)

    db.upsert_item_state(
        str(folder),
        library=library,
        kind="series",
        title=pf.title,
        year=pf.year,
        provider=provider,
        external_id=eid,
        nfo_status=nfo_state,
        episode_count_local=total_eps,
        episode_count_tvdb=None,
        last_scanned=int(__import__("time").time()),
        poster_path=str(poster) if poster else None,
    )
    return SeriesFolderScan(folder=pf, seasons=seasons, nfo_state=nfo_state,
                            has_provenance=has_prov, nfo_episode_count=nfo_count,
                            episode_count=total_eps)


def scan_movie_folder(folder: Path, library: str) -> dict:
    try:
        restore_from_sidecar(folder)
    except Exception as e:
        logger.warning("sidecar restore failed for {}: {}", folder, e)
    # primary video file
    videos = [f for f in folder.iterdir() if f.is_file() and is_video(f)]
    if not videos:
        return {}
    main = videos[0]
    pm = parse_movie_filename(main)
    nfo_path = main.with_suffix(".nfo")
    has_nfo = nfo_path.exists()
    has_prov = False
    if has_nfo:
        try:
            head = nfo_path.read_text(errors="ignore")[:2000]
            has_prov = PROVENANCE_TAG in head
        except Exception:
            pass
    folder_pf = parse_folder_name(folder.name)
    binding = db.get_binding(str(folder))
    provider = pm.provider or folder_pf.provider or (binding["provider"] if binding else None)
    eid = pm.external_id or folder_pf.external_id or (binding["external_id"] if binding else None)
    state = "complete" if has_nfo and has_prov else ("foreign" if has_nfo else "none")
    poster = _local_poster_for(folder)
    db.upsert_item_state(
        str(folder),
        library=library,
        kind="movie",
        title=pm.title or folder_pf.title,
        year=pm.year or folder_pf.year,
        provider=provider,
        external_id=eid,
        nfo_status=state,
        episode_count_local=1,
        episode_count_tvdb=None,
        last_scanned=int(__import__("time").time()),
        poster_path=str(poster) if poster else None,
    )
    return {"title": pm.title, "state": state}


def _scan_nfo_state(folder: Path, expected_episodes: int, kind: str) -> tuple[str, bool, int]:
    """Return (status, has_provenance_anywhere, nfo_episode_count)."""
    show_nfo = folder / "tvshow.nfo"
    has_show = show_nfo.exists()
    show_prov = False
    if has_show:
        try:
            show_prov = PROVENANCE_TAG in show_nfo.read_text(errors="ignore")[:2000]
        except Exception:
            pass

    nfo_eps = 0
    foreign_eps = 0
    for sd in detect_season_dirs(folder):
        for f in sd.iterdir():
            if f.is_file() and f.suffix.lower() == ".nfo":
                nfo_eps += 1
                try:
                    head = f.read_text(errors="ignore")[:2000]
                    if PROVENANCE_TAG not in head:
                        foreign_eps += 1
                except Exception:
                    pass

    has_prov_anywhere = show_prov or (nfo_eps > foreign_eps and nfo_eps > 0)

    if not has_show and nfo_eps == 0:
        return "none", False, 0
    if has_show and nfo_eps == expected_episodes and expected_episodes > 0 and (show_prov or foreign_eps == 0):
        return "complete", has_prov_anywhere, nfo_eps
    if not show_prov and nfo_eps > 0 and foreign_eps == nfo_eps:
        return "foreign", False, nfo_eps
    if has_show and nfo_eps < expected_episodes:
        return "partial", has_prov_anywhere, nfo_eps
    if has_show and nfo_eps > 0:
        return "mixed", has_prov_anywhere, nfo_eps
    return "partial", has_prov_anywhere, nfo_eps


# Local poster lookup ---------------------------------------------------------

POSTER_NAMES = ("poster.jpg", "poster.png", "folder.jpg", "cover.jpg")


def _local_poster_for(folder: Path) -> Optional[Path]:
    for n in POSTER_NAMES:
        p = folder / n
        if p.exists():
            return p
    return None


def hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
