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
    folder_looks_like_movie,
    folder_root_videos,
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
    """Infer ``tv`` / ``movies`` from a sample of the library.

    Note we still report a single kind even for libraries that mix movie
    and series folders (anime libraries commonly do). The per-folder scan
    in :func:`scan_library` then routes movie-like folders to
    :func:`scan_movie_folder` regardless of the library kind.
    """
    sample = []
    for c in library_dir.iterdir():
        if c.is_dir() and not c.name.startswith("."):
            sample.append(c)
        if len(sample) >= 16:
            break
    if not sample:
        return "mixed"
    has_seasons = 0
    movie_like = 0
    for d in sample:
        if detect_season_dirs(d):
            has_seasons += 1
            continue
        if folder_looks_like_movie(d):
            movie_like += 1
    if has_seasons >= movie_like:
        return "tv"
    return "movies"


def scan_library(name: str) -> int:
    lib_path = MEDIA_ROOT / name
    if not lib_path.is_dir():
        return 0
    rows = db.list_libraries()
    lib_row = next((r for r in rows if r["name"] == name), None)
    if lib_row is not None and not int(lib_row["enabled"] or 0):
        logger.info("scan_library skipped (disabled): {}", name)
        return 0
    kind = lib_row["kind"] if lib_row else "mixed"
    count = 0
    for entry in sorted(lib_path.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        # v0.9.0: per-folder kind detection so a Radarr movie sitting in a
        # mostly-TV library (common for anime libraries) still gets scanned
        # as a movie. Folder-level routing rules:
        #   - has season subdirs -> series
        #   - else has direct video file(s) -> movie
        #   - else (empty) -> follow the library's declared kind
        effective = kind
        if detect_season_dirs(entry):
            effective = "tv"
        elif folder_looks_like_movie(entry):
            effective = "movies"
        # v0.9.2: self-heal a stale binding whose kind no longer matches
        # the folder's actual content. Without this a binding written by
        # v0.9.0 (movie) keeps the build pipeline stuck on movie_details
        # for an id that is actually a TV show.
        binding = db.get_binding(str(entry))
        if binding:
            want = "series" if effective == "tv" else "movie"
            if binding["kind"] != want:
                logger.info(
                    "Rewriting binding kind for {}: {} \u2192 {} (folder content disagrees)",
                    entry.name, binding["kind"], want,
                )
                db.upsert_binding(
                    str(entry), want, binding["provider"], binding["external_id"],
                    title=binding["title"], year=binding["year"],
                    language=binding["language"], respect_lock=False,
                )
        if effective == "movies":
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
    # v0.9.0: also include video files dropped in the series root (a few
    # users keep a single "movie" video at the top of an anime folder, or
    # specials live there). They count toward the local episode total even
    # if we can't parse them so the UI doesn't claim "0 video files".
    root_eps = list_season_episodes(folder)
    if root_eps:
        seasons.setdefault(0, []).extend(root_eps)
        total_eps += len(root_eps)

    nfo_state, has_prov, nfo_count = _scan_nfo_state(folder, total_eps, kind="series")
    poster = _local_poster_for(folder)
    binding = db.get_binding(str(folder))
    provider = pf.provider or (binding["provider"] if binding else None)
    eid = pf.external_id or (binding["external_id"] if binding else None)

    # v0.11.4: cache the effective sort title alongside item_state so the
    # library list orders Plex/Sonarr-style without having to re-derive it on
    # every list call. Manual ``sorttitle`` overrides take precedence; the
    # fallback strips a leading article.
    series_overrides = db.get_nfo_overrides(str(folder)).get("series", {})
    sort_title = db.compute_sort_title(pf.title, series_overrides.get("sorttitle"))
    db.upsert_item_state(
        str(folder),
        library=library,
        kind="series",
        title=pf.title,
        sort_title=sort_title,
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
    # primary video file. v0.9.0: even if no video file exists yet (folder
    # was created but the download is incomplete) we still upsert basic
    # state from the folder name so the user sees the item, can match it
    # manually, and the builder doesn't silently skip it later.
    videos = folder_root_videos(folder)
    main = videos[0] if videos else None
    pm = parse_movie_filename(main) if main else None
    nfo_path = main.with_suffix(".nfo") if main else (folder / "movie.nfo")
    has_nfo = nfo_path.exists() if main else False
    has_prov = False
    if has_nfo:
        try:
            head = nfo_path.read_text(errors="ignore")[:2000]
            has_prov = PROVENANCE_TAG in head
        except Exception:
            pass
    folder_pf = parse_folder_name(folder.name)
    binding = db.get_binding(str(folder))
    pm_provider = pm.provider if pm else None
    pm_eid = pm.external_id if pm else None
    pm_title = pm.title if pm else None
    pm_year = pm.year if pm else None
    provider = pm_provider or folder_pf.provider or (binding["provider"] if binding else None)
    eid = pm_eid or folder_pf.external_id or (binding["external_id"] if binding else None)
    state = "complete" if has_nfo and has_prov else ("foreign" if has_nfo else "none")
    poster = _local_poster_for(folder)
    movie_overrides = db.get_nfo_overrides(str(folder)).get("movie", {})
    movie_title = pm_title or folder_pf.title
    sort_title = db.compute_sort_title(movie_title, movie_overrides.get("sorttitle"))
    db.upsert_item_state(
        str(folder),
        library=library,
        kind="movie",
        title=movie_title,
        sort_title=sort_title,
        year=pm_year or folder_pf.year,
        provider=provider,
        external_id=eid,
        nfo_status=state,
        episode_count_local=len(videos),
        episode_count_tvdb=None,
        last_scanned=int(__import__("time").time()),
        poster_path=str(poster) if poster else None,
    )
    return {"title": pm_title or folder_pf.title, "state": state}


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
            if not (f.is_file() and f.suffix.lower() == ".nfo"):
                continue
            # season.nfo lives next to episode .nfo files but is a season-level
            # sidecar, not an episode. Don't count it toward episode coverage.
            if f.name.lower() == "season.nfo":
                continue
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


# v0.11.5: "empty folder" detection used by the Prune-empty button.
#
# A folder is considered empty when no descendant file matches our video
# extension list. We walk the *entire* tree (not just season subdirs) so a
# stray video sitting at the show root, or in any non-standard subdirectory,
# is still treated as media. NFOs, posters, sidecars, and other generated
# files are deliberately ignored — the whole point of this check is to
# distinguish a folder that contains only generated metadata from one that
# actually has media in it. The walker bails out the instant it sees a
# video file, so it's cheap even on huge libraries.

def folder_has_media(folder: Path) -> bool:
    """True if *any* descendant file is a recognised video file.

    Walks the directory tree depth-first and returns ``True`` on the first
    video hit. Symlinks are followed only if they don't escape the folder
    — ``os.walk`` with ``followlinks=False`` is safe enough; we'd rather
    miss a symlinked video and refuse to prune than accidentally delete a
    record for a folder whose media lives behind a link.

    Permission errors and unreadable subtrees are treated as "might contain
    media" — i.e. we return ``True`` to be safe. Better to leave a tracked
    row in place than to forget a folder we couldn't fully inspect.
    """
    try:
        if not folder.exists() or not folder.is_dir():
            # Folder is gone — that's a different problem (handled by the
            # ordinary ``/items/prune`` endpoint), and definitely not
            # something Prune-empty should claim is "empty of media".
            return False
    except OSError:
        return True
    try:
        # Iterative DFS so we can early-out on the first video.
        stack: list[Path] = [folder]
        while stack:
            cur = stack.pop()
            try:
                entries = list(cur.iterdir())
            except (PermissionError, OSError) as e:
                logger.warning("folder_has_media: cannot read {} ({}); treating as \"has media\" for safety", cur, e)
                return True
            for entry in entries:
                try:
                    if entry.is_symlink():
                        # Don't traverse symlinks to avoid loops, but if the
                        # link points at a video file we still count it as
                        # media so users keeping their videos behind a
                        # symlink farm aren't surprised by a prune.
                        if entry.suffix.lower() and is_video(entry):
                            return True
                        continue
                    if entry.is_file():
                        if is_video(entry):
                            return True
                    elif entry.is_dir():
                        if entry.name.startswith("."):
                            continue
                        stack.append(entry)
                except (PermissionError, OSError) as e:
                    logger.warning("folder_has_media: stat failed for {} ({}); treating as \"has media\" for safety", entry, e)
                    return True
        return False
    except Exception as e:  # pragma: no cover
        logger.warning("folder_has_media: unexpected error in {} ({}); treating as \"has media\" for safety", folder, e)
        return True

