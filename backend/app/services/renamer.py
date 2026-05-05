"""File renaming for series episodes and movies (v0.10.0).

Takes user-controlled templates from ``UserSettings`` and produces preview /
apply actions for renaming the actual files on disk into a Sonarr/Radarr
style layout. Renames are atomic per file (``os.replace``) and the per-file
override rows in the database get carried along to the new path so the
Episodes tab keeps showing the same mapping after the rename.

The renamer is conservative on purpose:

* Refuses to write outside the original folder.
* Refuses to silently overwrite existing files \u2014 conflicts are surfaced in
  the preview as ``conflict="exists"`` and the caller can decide.
* Only touches files that *parsed cleanly* (or had a manual override) so an
  ambiguous file never gets a wrong name.
* ``preview()`` never mutates the filesystem; ``apply()`` does.
"""
from __future__ import annotations

import os
import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from .. import db
from .parser import (
    detect_season_dirs,
    list_season_episodes,
    season_number_from_dir,
)


# ---- Token formatting ------------------------------------------------------

# Characters that are illegal on Windows / SMB and a frequent foot-gun on
# *nix. We replace them with a single space so the resulting filename is
# safe to copy onto any filesystem.
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _sanitize(value: str) -> str:
    cleaned = _BAD_CHARS.sub(" ", value or "").strip()
    # Collapse repeated whitespace and strip trailing dots which Windows
    # silently drops.
    cleaned = re.sub(r"\s+", " ", cleaned).rstrip(". ")
    return cleaned


_QUALITY_RES = [
    re.compile(r"\b(2160p|1080p|720p|480p|576p)\b", re.IGNORECASE),
    re.compile(r"\b(WEB[- ]?DL|WEB[- ]?Rip|BluRay|BDRip|HDRip|DVDRip|HDTV|REMUX)\b",
               re.IGNORECASE),
]


def _guess_quality(stem: str) -> str:
    """Best-effort quality token extracted from the original filename.

    We pick the first match for resolution and source separately and join
    them with a hyphen, mirroring Sonarr's ``WEB-DL-1080p`` style. Returns
    an empty string if nothing matched.
    """
    parts: list[str] = []
    for rx in _QUALITY_RES:
        m = rx.search(stem)
        if m:
            parts.append(m.group(1).replace(" ", "-"))
    return "-".join(parts)


class _SafeDict(dict):
    """``str.format_map`` helper that returns ``""`` for missing keys."""

    def __missing__(self, key):  # noqa: D401 - dict protocol
        return ""


def render_template(template: str, values: dict) -> str:
    """Render a rename template like ``"{title} - S{season:02}E{episode:02}"``.

    Falls back to an empty string for any token the user references that we
    don't know about, instead of raising. Format-specs (e.g. ``{season:02}``)
    are honoured for integer values.
    """
    out: list[str] = []
    formatter = string.Formatter()
    safe = _SafeDict(values)
    try:
        for literal, field_name, format_spec, conversion in formatter.parse(template):
            if literal:
                out.append(literal)
            if field_name is None:
                continue
            try:
                value, _ = formatter.get_field(field_name, (), safe)
            except Exception:
                value = ""
            if value is None or value == "":
                continue
            if conversion:
                value = formatter.convert_field(value, conversion)
            try:
                out.append(formatter.format_field(value, format_spec or ""))
            except (TypeError, ValueError):
                out.append(str(value))
    except Exception as e:
        logger.warning("rename template parse failed for {!r}: {}", template, e)
        return ""
    return "".join(out)


# ---- Plan / preview / apply ------------------------------------------------


@dataclass
class RenamePlanItem:
    folder_path: str           # series root (or movie folder)
    src: str                   # absolute current path
    dst: str                   # absolute target path
    season: Optional[int]
    episode: Optional[int]
    matched_title: Optional[str]
    conflict: Optional[str] = None  # "exists" | "duplicate" | None


def plan_series_rename(
    folder: Path | str,
    *,
    template: str,
    title: str,
    year: Optional[int],
    episodes_by_se: dict[tuple[int, int], dict],
    overrides_by_file: dict[str, dict],
) -> list[RenamePlanItem]:
    """Produce a rename plan for every episode file in ``folder``.

    ``episodes_by_se`` maps ``(season, episode) -> {name, ...}`` pulled from
    whichever provider the binding points at. ``overrides_by_file`` maps
    absolute file path to ``{season, episode, external_id}`` so user-set
    season/episode values trump the parsed ones.
    """
    folder_p = Path(folder)
    plan: list[RenamePlanItem] = []
    seen: set[str] = set()

    def _emit(parsed_path: Path, parsed_season: int, parsed_episode: int):
        ovr = overrides_by_file.get(str(parsed_path)) or {}
        season = ovr.get("season") if ovr.get("season") is not None else parsed_season
        episode = ovr.get("episode") if ovr.get("episode") is not None else parsed_episode
        ext = parsed_path.suffix.lower()
        ep_meta = (
            episodes_by_se.get((int(season), int(episode)))
            if season is not None and episode is not None
            else None
        )
        ep_title = (ep_meta or {}).get("name") or ""
        quality = _guess_quality(parsed_path.stem)
        new_name_raw = render_template(
            template,
            {
                "title": title or "",
                "year": year if year else "",
                "season": int(season) if season is not None else 0,
                "episode": int(episode) if episode is not None else 0,
                "episode_title": ep_title,
                "ext": ext,
                "quality": quality,
            },
        )
        new_name = _sanitize(new_name_raw) or parsed_path.name
        # Always keep the extension intact \u2014 templates must end in {ext} but
        # if the user dropped it, append it so we never lose the suffix.
        if not new_name.lower().endswith(ext):
            new_name = f"{new_name}{ext}"
        target_dir = parsed_path.parent
        dst = str(target_dir / new_name)
        conflict: Optional[str] = None
        if dst in seen:
            conflict = "duplicate"
        elif Path(dst) != parsed_path and Path(dst).exists():
            conflict = "exists"
        seen.add(dst)
        plan.append(
            RenamePlanItem(
                folder_path=str(folder_p),
                src=str(parsed_path),
                dst=dst,
                season=int(season) if season is not None else None,
                episode=int(episode) if episode is not None else None,
                matched_title=ep_title or None,
                conflict=conflict,
            )
        )

    # Season subdirs
    for sd in detect_season_dirs(folder_p):
        snum = season_number_from_dir(sd.name)
        for parsed in list_season_episodes(sd):
            if not getattr(parsed, "parsed", True):
                ovr = overrides_by_file.get(str(parsed.path)) or {}
                if ovr.get("season") is None or ovr.get("episode") is None:
                    # No way to name a file we can't parse and the user hasn't
                    # assigned. Skip silently.
                    continue
            _emit(parsed.path, snum, parsed.episode)

    # Loose root files (anime / OVA layouts)
    for parsed in list_season_episodes(folder_p):
        if not getattr(parsed, "parsed", True):
            ovr = overrides_by_file.get(str(parsed.path)) or {}
            if ovr.get("season") is None or ovr.get("episode") is None:
                continue
        _emit(parsed.path, parsed.season or 1, parsed.episode)

    return plan


def apply_rename_plan(plan: list[RenamePlanItem], *,
                      skip_conflicts: bool = True) -> dict:
    """Execute the plan. Returns a summary dict.

    Each successful rename also moves any per-file override row in the
    database so the Episodes tab keeps showing the same selection after the
    rename. Source folder is enforced \u2014 any plan item whose ``dst`` would
    leave the parent directory is skipped.
    """
    renamed: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    for item in plan:
        if item.src == item.dst:
            skipped.append({"src": item.src, "reason": "no-op"})
            continue
        if item.conflict and skip_conflicts:
            skipped.append({"src": item.src, "dst": item.dst, "reason": item.conflict})
            continue
        src_p = Path(item.src)
        dst_p = Path(item.dst)
        if dst_p.parent != src_p.parent:
            failed.append({"src": item.src, "dst": item.dst,
                            "reason": "cross-folder rename refused"})
            continue
        if not src_p.exists():
            failed.append({"src": item.src, "reason": "source missing"})
            continue
        try:
            os.replace(src_p, dst_p)
            try:
                db.rename_episode_file_override(item.folder_path,
                                                 item.src, item.dst)
            except Exception as de:
                logger.warning("override row move failed {} -> {}: {}",
                                item.src, item.dst, de)
            renamed.append({"src": item.src, "dst": item.dst})
        except Exception as e:
            failed.append({"src": item.src, "dst": item.dst, "reason": str(e)})

    return {
        "renamed": renamed,
        "skipped": skipped,
        "failed": failed,
    }


def plan_movie_rename(
    folder: Path | str,
    *,
    template: str,
    title: str,
    year: Optional[int],
) -> list[RenamePlanItem]:
    """Produce a rename plan for every video file directly inside ``folder``.

    Movies in Plex/Radarr always live in a per-movie folder so we only touch
    files at depth 0. The ``{ext}`` token is honoured the same way as for
    episodes.
    """
    folder_p = Path(folder)
    plan: list[RenamePlanItem] = []
    seen: set[str] = set()
    if not folder_p.is_dir():
        return plan
    for f in sorted(folder_p.iterdir()):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        from .parser import VIDEO_EXT
        if ext not in VIDEO_EXT:
            continue
        new_name_raw = render_template(
            template,
            {
                "title": title or "",
                "year": year if year else "",
                "ext": ext,
                "quality": _guess_quality(f.stem),
            },
        )
        new_name = _sanitize(new_name_raw) or f.name
        if not new_name.lower().endswith(ext):
            new_name = f"{new_name}{ext}"
        dst = str(folder_p / new_name)
        conflict: Optional[str] = None
        if dst in seen:
            conflict = "duplicate"
        elif Path(dst) != f and Path(dst).exists():
            conflict = "exists"
        seen.add(dst)
        plan.append(
            RenamePlanItem(
                folder_path=str(folder_p),
                src=str(f),
                dst=dst,
                season=None,
                episode=None,
                matched_title=None,
                conflict=conflict,
            )
        )
    return plan
