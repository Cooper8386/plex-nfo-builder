"""Sweep orphaned NFO + thumbnail companion files left behind by Sonarr/Radarr
upgrades.

Background
----------
When Sonarr or Radarr replaces a video with a new release of the same episode
or movie, it only manages the **video file** itself. The companion sidecars
plex-nfo-builder writes — ``<stem>.nfo`` and ``<stem>-thumb.{jpg,jpeg,png}`` —
are ignored. After an upgrade where the release group (or any other token in
the rename template) changes, the new ``<new-stem>.mkv`` arrives, the old
video is deleted, but the old ``<old-stem>.nfo`` and ``<old-stem>-thumb.jpg``
are left orphaned in the season folder with no matching video.

Plex's "Plex TV Series" agent reads every ``.nfo`` it finds, regardless of
whether the NFO has a paired video file. The orphaned NFO carries its own
``<uniqueid type="tvdb" default="true">`` block which Plex faithfully
indexes — and because that uniqueid does not match the new ``[NewGroup].mkv``
sitting next to it, Plex creates a *second* library entry for the same show
in order to host the orphaned-but-claimed episode. After two upgrade rounds
you get three Plex entries for one folder, etc. — the symptom most users
describe as "my show appears twice in Plex even though there's only one
folder on disk".

This module is the surgical cleanup. It is **video-driven**: it enumerates
the live video files via the same parser the builder uses, then deletes any
companion ``.nfo`` / ``-thumb.{jpg,jpeg,png}`` whose stem doesn't pair with
a current video file. Show-level artwork (``poster.jpg``, ``background.jpg``,
…), the show-level ``tvshow.nfo``, every ``season.nfo``, every video file,
every subtitle, every audio file, and every other unrecognised file is
**always** preserved.

This is intentionally narrower than ``cleaner.clean_folder``, which wipes
**all** generated metadata so the user can rebuild from scratch. The orphan
sweeper only removes sidecars whose paired media has already been deleted.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

from .parser import (
    detect_season_dirs,
    folder_root_videos,
    list_season_episodes,
)


# Suffixes that mark a Plex/Kodi thumbnail companion. We only treat a file
# ending in one of these as a thumbnail when its <stem>-thumb prefix would
# pair with a real video file's stem — i.e. the part **before** "-thumb".
_THUMB_SUFFIXES: tuple[str, ...] = ("-thumb.jpg", "-thumb.jpeg", "-thumb.png")


def _strip_thumb_suffix(name: str) -> Optional[str]:
    """If ``name`` ends in a thumbnail suffix, return the underlying video
    stem it would belong to. Otherwise return None.

    ``"S01E01 - Foo-thumb.jpg"`` -> ``"S01E01 - Foo"``
    ``"poster.jpg"``             -> None
    """
    low = name.lower()
    for sfx in _THUMB_SUFFIXES:
        if low.endswith(sfx):
            # Use the original (un-lowercased) name to recover case so we
            # match the live video stem exactly.
            return name[: -len(sfx)]
    return None


def _empty_summary() -> dict:
    return {
        "nfo_removed": 0,
        "thumb_removed": 0,
        "files": [],  # list[str] — relative to the folder being swept
    }


def _record_remove(summary: dict, folder: Path, target: Path, kind: str,
                   *, dry_run: bool) -> None:
    """Register ``target`` as a removal in ``summary``. Performs the unlink
    when ``dry_run`` is False; otherwise just records what *would* be removed.
    """
    try:
        rel = str(target.relative_to(folder))
    except ValueError:
        rel = str(target)
    if dry_run:
        summary["files"].append(rel)
        if kind == "nfo":
            summary["nfo_removed"] += 1
        else:
            summary["thumb_removed"] += 1
        return
    try:
        target.unlink()
    except FileNotFoundError:
        return
    except Exception as e:  # noqa: BLE001
        logger.warning("orphans: could not delete {}: {}", target, e)
        return
    summary["files"].append(rel)
    if kind == "nfo":
        summary["nfo_removed"] += 1
    else:
        summary["thumb_removed"] += 1


def _sweep_directory(folder: Path, season_dir: Path,
                     video_stems: set[str], summary: dict,
                     *, dry_run: bool) -> None:
    """Sweep one season-directory's worth of orphans.

    Hard rules:
      * never delete ``season.nfo`` (it's a directory-level sidecar)
      * never delete a video file
      * never delete a subtitle, audio, or other unknown file
      * only delete ``<stem>.nfo`` when ``<stem>`` is not in ``video_stems``
      * only delete ``<stem>-thumb.{jpg,jpeg,png}`` when ``<stem>`` is not
        in ``video_stems``
    """
    try:
        entries = list(season_dir.iterdir())
    except (PermissionError, OSError) as e:
        logger.warning("orphans: cannot read {} ({}); skipping", season_dir, e)
        return
    for f in entries:
        if not f.is_file():
            continue
        name = f.name
        low = name.lower()
        # Episode .nfo? — anything ending in .nfo, except season.nfo.
        if low.endswith(".nfo"):
            if low == "season.nfo":
                continue
            stem = f.stem  # filename without the trailing ".nfo"
            if stem in video_stems:
                continue
            _record_remove(summary, folder, f, "nfo", dry_run=dry_run)
            continue
        # Episode thumbnail companion?
        thumb_stem = _strip_thumb_suffix(name)
        if thumb_stem is not None:
            if thumb_stem in video_stems:
                continue
            _record_remove(summary, folder, f, "thumb", dry_run=dry_run)


def sweep_series_orphans(folder: Path, *, dry_run: bool = False) -> dict:
    """Sweep orphaned NFOs + thumbnails from every season directory under
    ``folder``. Returns a summary dict::

        {
          "nfo_removed":   int,
          "thumb_removed": int,
          "files":         list[str],  # relative paths that were (or would be) removed
        }

    Show-root files (``tvshow.nfo``, ``poster.jpg``, ``background.jpg``,
    ``Season01-poster.jpg``, ``season-specials-poster.jpg``, …) are never
    touched — those don't have a per-video stem and can't go orphaned in the
    same sense. Empty season directories are also preserved.

    Raises ``FileNotFoundError`` if ``folder`` is not a directory so callers
    can surface the problem instead of silently no-op-ing.
    """
    if not folder.is_dir():
        raise FileNotFoundError(str(folder))

    summary = _empty_summary()
    season_dirs = detect_season_dirs(folder)
    if not season_dirs:
        # Some series (anime, OVAs) keep their videos at the show root. Still
        # honour that layout — the sweep should reach those companions too.
        roots = folder_root_videos(folder)
        if roots:
            video_stems: set[str] = {p.stem for p in roots}
            _sweep_directory(folder, folder, video_stems, summary,
                             dry_run=dry_run)
        return summary

    for sd in season_dirs:
        eps = list_season_episodes(sd)
        video_stems = {ep.path.stem for ep in eps}
        _sweep_directory(folder, sd, video_stems, summary, dry_run=dry_run)
    return summary


def sweep_movie_orphans(folder: Path, *, dry_run: bool = False) -> dict:
    """Sweep orphaned NFOs + thumbnails from a movie folder.

    The "live" stems for a movie folder are the stems of every video file
    sitting directly inside ``folder`` (Radarr typically keeps just one,
    but multi-version setups may have several). Show-level artwork files
    are still preserved exactly as in the series sweeper.
    """
    if not folder.is_dir():
        raise FileNotFoundError(str(folder))

    summary = _empty_summary()
    videos = folder_root_videos(folder)
    video_stems: set[str] = {v.stem for v in videos}
    if not video_stems:
        # No live video at all — refuse to sweep. This is the "downloads
        # haven't finished" edge case. Better to leave companions in place
        # than to nuke them while the video is in flight.
        return summary
    _sweep_directory(folder, folder, video_stems, summary, dry_run=dry_run)
    return summary


def preview_series_orphans(folder: Path) -> dict:
    """Convenience wrapper for the API preview path."""
    return sweep_series_orphans(folder, dry_run=True)


def preview_movie_orphans(folder: Path) -> dict:
    return sweep_movie_orphans(folder, dry_run=True)


# v0.11.11 ---------------------------------------------------------------
# Cheap orphan probe used by the scanner to populate item_state.orphan_count.
# Returns just the integer count (nfo + thumb) so we don't carry around a
# files list we'll never look at. Functionally equivalent to running the
# preview and reading ``nfo_removed + thumb_removed`` but skips list
# bookkeeping per file.


def _count_directory_orphans(season_dir: Path, video_stems: set[str]) -> int:
    try:
        entries = list(season_dir.iterdir())
    except (PermissionError, OSError):
        return 0
    count = 0
    for f in entries:
        if not f.is_file():
            continue
        name = f.name
        low = name.lower()
        if low.endswith(".nfo"):
            if low == "season.nfo":
                continue
            if f.stem in video_stems:
                continue
            count += 1
            continue
        thumb_stem = _strip_thumb_suffix(name)
        if thumb_stem is not None and thumb_stem not in video_stems:
            count += 1
    return count


def count_series_orphans(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    season_dirs = detect_season_dirs(folder)
    if not season_dirs:
        roots = folder_root_videos(folder)
        if not roots:
            return 0
        stems = {p.stem for p in roots}
        return _count_directory_orphans(folder, stems)
    total = 0
    for sd in season_dirs:
        eps = list_season_episodes(sd)
        stems = {ep.path.stem for ep in eps}
        total += _count_directory_orphans(sd, stems)
    return total


def count_movie_orphans(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    videos = folder_root_videos(folder)
    if not videos:
        # Refuse to count anything when no live video is present — we never
        # sweep these folders either.
        return 0
    stems = {v.stem for v in videos}
    return _count_directory_orphans(folder, stems)


__all__: Iterable[str] = (
    "sweep_series_orphans",
    "sweep_movie_orphans",
    "preview_series_orphans",
    "preview_movie_orphans",
    "count_series_orphans",
    "count_movie_orphans",
)
