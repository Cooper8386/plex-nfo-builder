"""File renaming for series episodes and movies (v0.11.0).

Implements Sonarr/Radarr-compatible naming templates so users can paste
their Profilarr / Trash-Guides template strings in directly. Supports:

* Plain tokens: ``{Series TitleYear}``, ``{Episode CleanTitle}``, ...
* Zero-padded ints: ``{season:00}`` -> ``01``
* Sonarr conditional groups: ``{[Quality Full]}`` -> drop group + separator
  when the value is empty. Multi-token groups like
  ``{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}`` are supported.
* Square-bracket conditional groups: ``[{MediaInfo VideoBitDepth}bit]``
  -> drop the bracketed block when the first token is empty.
* Prefix-conditional: ``{-Release Group}`` -> prefix with ``-`` only if
  the token resolves to a non-empty value.
* Nested templates: ``{tvdb-{TvdbId}}`` -> recursively rendered; the
  whole group is dropped when inner tokens are empty.
* Safe-dict fallback: unknown tokens resolve to ``""`` rather than
  raising.

MediaInfo values are pulled from ffprobe via :mod:`.mediainfo`. Rendering
always post-processes the output to clean up orphan separators left by
dropped conditional groups.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

from .. import db
from . import mediainfo as mi_svc
from .parser import (
    ANIME_RE,
    detect_season_dirs,
    list_season_episodes,
    season_number_from_dir,
)


# ---- Sanitisation ----------------------------------------------------------

# Characters illegal on Windows / SMB. We replace them with a single space
# so the resulting filename is safe to copy onto any filesystem.
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def sanitize(value: str) -> str:
    cleaned = _BAD_CHARS.sub(" ", value or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned).rstrip(". ")
    return cleaned


def clean_title(value: str) -> str:
    """Sonarr-style CleanTitle: strip filesystem-unsafe chars only."""
    return sanitize(value)


# ---- Sonarr/Radarr template grammar ----------------------------------------


def render_sonarr_template(template: str, ctx: dict) -> str:
    """Render a Sonarr/Radarr template string using ``ctx``.

    Returns the fully rendered filename (or folder name). Unknown tokens
    resolve to an empty string; conditional groups collapse neatly along
    with their surrounding separators.
    """
    text, _ = _render_inner(template, ctx)
    return _cleanup_whitespace(text)


def _render_inner(template: str, ctx: dict) -> tuple[str, bool]:
    """Render ``template`` and return (text, any_token_resolved_to_value).

    The second element is used by the outer parser to decide whether
    nested groups should be kept or dropped (e.g. ``{tvdb-{TvdbId}}``
    disappears entirely when ``TvdbId`` is empty).
    """
    out: list[str] = []
    had_value = False
    i = 0
    n = len(template)

    while i < n:
        ch = template[i]

        # Sonarr conditional group: {[...]} possibly spanning {[...}{...]}
        # When at least one token resolves, the rendered text is wrapped in
        # literal [] brackets in the output (Sonarr/Trash Guides convention).
        if ch == "{" and i + 1 < n and template[i + 1] == "[":
            end = template.find("]}", i + 2)
            if end != -1:
                body = template[i + 2 : end]
                rendered = _render_cond_group(body, ctx)
                if rendered:
                    out.append(f"[{rendered}]")
                    had_value = True
                else:
                    _strip_trailing_sep(out)
                i = end + 2
                continue

        # Square-bracket conditional group: [{Token}suffix]
        # Only triggered when the next non-bracket char is '{' so we don't
        # eat plain literal brackets inside tokens.
        if ch == "[" and i + 1 < n and template[i + 1] == "{":
            close = _match_bracket(template, i)
            if close != -1:
                body = template[i + 1 : close]
                sub_text, sub_had = _render_inner(body, ctx)
                if sub_had and sub_text.strip():
                    out.append("[")
                    out.append(sub_text)
                    out.append("]")
                    had_value = True
                else:
                    _strip_trailing_sep(out)
                i = close + 1
                continue

        if ch != "{":
            out.append(ch)
            i += 1
            continue

        # Regular {...} group. Track brace depth so {tvdb-{TvdbId}} finds
        # the outer '}' and not the inner one.
        j = _match_brace(template, i)
        if j == -1:
            out.append(ch)
            i += 1
            continue
        inner = template[i + 1 : j]

        if inner.startswith("-"):
            # Prefix-conditional: {-Token}
            tok = inner[1:].strip()
            val = _lookup(tok, ctx, None)
            if val:
                out.append(f"-{val}")
                had_value = True
            i = j + 1
            continue

        if "{" in inner:
            # Nested template: recurse. Drop if no inner token had value.
            sub_text, sub_had = _render_inner(inner, ctx)
            if sub_had and sub_text:
                out.append(sub_text)
                had_value = True
            else:
                _strip_trailing_sep(out)
            i = j + 1
            continue

        tok, fmt = _split_token_fmt(inner)
        val = _lookup(tok, ctx, fmt)
        if val:
            out.append(val)
            had_value = True
        i = j + 1

    return "".join(out), had_value


def _render_cond_group(body: str, ctx: dict) -> str:
    """Render the content between ``{[`` and ``]}``.

    The body may contain ``}{`` separators for multi-token groups, e.g.
    ``Mediainfo AudioCodec}{ Mediainfo AudioChannels``. Each piece after
    the first may carry a literal separator before its token name (the
    space in ``{ Mediainfo AudioChannels}``). Empty tokens contribute
    nothing. Returns an empty string when every piece resolved empty.
    """
    parts = body.split("}{")
    rendered: list[str] = []
    for idx, part in enumerate(parts):
        if idx == 0:
            sep = ""
            name_raw = part
        else:
            stripped = part.lstrip()
            sep = part[: len(part) - len(stripped)]
            name_raw = stripped
        # In malformed templates a stray ']' can leak into the last token.
        name_raw = name_raw.rstrip("]").strip()
        tok, fmt = _split_token_fmt(name_raw)
        val = _lookup(tok, ctx, fmt)
        if val:
            rendered.append(sep + val)
    return "".join(rendered).strip()


def _match_brace(s: str, i: int) -> int:
    """Return the index of the matching ``}`` for the ``{`` at ``i``."""
    depth = 0
    j = i
    while j < len(s):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def _match_bracket(s: str, i: int) -> int:
    """Return the index of the matching ``]`` for the ``[`` at ``i``."""
    depth = 0
    j = i
    while j < len(s):
        if s[j] == "[":
            depth += 1
        elif s[j] == "]":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def _strip_trailing_sep(out: list[str]) -> None:
    """Remove a trailing separator left by a dropped conditional group."""
    while out and out[-1] in (" ", "\t"):
        out.pop()


def _split_token_fmt(inner: str) -> tuple[str, Optional[str]]:
    """Split ``name:spec`` into ``(name, spec)``; Sonarr uses ``:00`` style."""
    if ":" in inner:
        name, spec = inner.split(":", 1)
        return name.strip(), spec.strip()
    return inner.strip(), None


_MULTISPACE_RE = re.compile(r"  +")
_SPACE_BEFORE_EXT_RE = re.compile(r"\s+(\.[A-Za-z0-9]+)$")
_TRAIL_DASH_RE = re.compile(r"[\s-]+$")


def _cleanup_whitespace(text: str) -> str:
    """Tidy double spaces and orphan trailing separators."""
    out = _MULTISPACE_RE.sub(" ", text)
    out = _SPACE_BEFORE_EXT_RE.sub(r"\1", out)
    # Strip a trailing dash/space that may have been left by an empty
    # release group when the template used `` -{Release Group}`` style.
    # We only strip if there's an extension, so we don't kill a real title
    # that ends in whitespace.
    m = re.search(r"^(.*?)(\.[A-Za-z0-9]+)$", out)
    if m:
        stem, ext = m.group(1), m.group(2)
        stem = _TRAIL_DASH_RE.sub("", stem)
        out = stem + ext
    else:
        out = _TRAIL_DASH_RE.sub("", out)
    return out


# ---- Token lookup ----------------------------------------------------------

# Normalised alias -> internal ctx key. Keys compared lowercase with the
# internal spaces preserved but hyphens kept (e.g. ``air-date``).
_TOKEN_ALIASES: dict[str, str] = {
    # Series / movie titles
    "series titleyear": "series_titleyear",
    "series titleThe": "series_title_the",
    "series title": "title",
    "series cleantitle": "series_cleantitle",
    "movie title": "title",
    "movie cleantitle": "movie_cleantitle",
    "movie titlethe": "title",
    "movie titleyear": "series_titleyear",
    # Episode info
    "season number": "season",
    "season": "season",
    "episode number": "episode",
    "episode": "episode",
    "episode cleantitle": "episode_cleantitle",
    "episode title": "episode_title",
    "air-date": "air_date",
    "air date": "air_date",
    # Quality
    "quality full": "quality_full",
    "quality title": "quality_full",
    # MediaInfo (both capitalisations Sonarr uses)
    "mediainfo videocodec": "video_codec",
    "mediainfo videobitdepth": "video_bit_depth",
    "mediainfo videodynamicrangetype": "hdr_type",
    "mediainfo videodynamicrange": "hdr_type",
    "mediainfo audiocodec": "audio_codec",
    "mediainfo audiochannels": "audio_channels",
    "mediainfo audiolanguages": "audio_languages",
    "mediainfo subtitlelanguages": "",
    "mediainfo 3d": "is_3d",
    "mediainfo simplevideocodec": "video_codec",
    "mediainfo simpleaudiocodec": "audio_codec",
    # Release / custom
    "release group": "release_group",
    "releasegroup": "release_group",
    "custom formats": "custom_formats",
    "custom format": "custom_formats",
    # Movie-specific
    "release year": "year",
    "(release year)": "year_parens",
    "edition tags": "edition_tags",
    # IDs
    "tvdbid": "tvdb_id",
    "tvdb id": "tvdb_id",
    "tmdbid": "tmdb_id",
    "tmdb id": "tmdb_id",
    "imdbid": "imdb_id",
    "imdb id": "imdb_id",
}


def _lookup(token: str, ctx: dict, fmt: Optional[str]) -> str:
    """Resolve ``token`` against ``ctx`` and apply ``fmt`` (``"00"`` pads)."""
    if not token:
        return ""
    key = token.strip().lower()
    mapped = _TOKEN_ALIASES.get(key)
    if mapped is None:
        # Allow direct ctx hits too so user-defined tokens work.
        mapped = key.replace(" ", "_")
    raw = ctx.get(mapped)
    if raw is None or raw == "":
        return ""

    if fmt and re.fullmatch(r"0+", fmt):
        width = len(fmt)
        try:
            return f"{int(raw):0{width}d}"
        except (TypeError, ValueError):
            return str(raw)

    # Boolean tokens (e.g. {MediaInfo 3D}) only render a marker when True.
    if isinstance(raw, bool):
        if not raw:
            return ""
        return "3D" if mapped == "is_3d" else str(raw)

    return str(raw)


# ---- Context builder -------------------------------------------------------


def build_context(
    *,
    title: str,
    year: Optional[int],
    tvdb_id: Optional[str] = None,
    tmdb_id: Optional[str] = None,
    imdb_id: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    episode_title: str = "",
    air_date: str = "",
    release_group: str = "",
    quality_full: str = "",
    mi: Optional[mi_svc.MediaInfo] = None,
    edition_tags: str = "",
    custom_formats: str = "",
) -> dict:
    """Build the render-context dict used by ``render_sonarr_template``.

    Callers can populate as many or as few fields as make sense. Empty
    values let Sonarr-style conditional groups collapse naturally.
    """
    mi = mi or mi_svc.MediaInfo()
    title_clean = clean_title(title or "")
    titleyear = f"{title_clean} ({year})" if year else title_clean
    return {
        # Titles
        "title": title_clean,
        "series_titleyear": titleyear,
        "series_cleantitle": title_clean,
        "movie_cleantitle": title_clean,
        # Year
        "year": year if year else "",
        "year_parens": f"({year})" if year else "",
        # IDs
        "tvdb_id": str(tvdb_id) if tvdb_id else "",
        "tmdb_id": str(tmdb_id) if tmdb_id else "",
        "imdb_id": str(imdb_id) if imdb_id else "",
        # Episode info
        "season": season if season is not None else "",
        "episode": episode if episode is not None else "",
        "episode_title": clean_title(episode_title or ""),
        "episode_cleantitle": clean_title(episode_title or ""),
        "air_date": air_date or "",
        # Quality
        "quality_full": quality_full,
        # MediaInfo
        "video_codec": mi.video_codec,
        "video_bit_depth": mi.video_bit_depth,
        "hdr_type": mi.hdr_type,
        "audio_codec": mi.audio_codec,
        "audio_channels": mi.audio_channels,
        "audio_languages": mi.audio_languages,
        "is_3d": bool(mi.is_3d),
        # Release / custom
        "release_group": release_group or "",
        "edition_tags": edition_tags or "",
        "custom_formats": custom_formats or "",
    }


# ---- Plan / apply ----------------------------------------------------------


@dataclass
class RenamePlanItem:
    folder_path: str           # series root (or movie folder)
    src: str                   # absolute current path
    dst: str                   # absolute target path
    season: Optional[int]
    episode: Optional[int]
    matched_title: Optional[str]
    conflict: Optional[str] = None  # "exists" | "duplicate" | None


def _looks_anime(path_name: str) -> bool:
    return ANIME_RE.match(path_name) is not None


def plan_series_rename(
    folder: Path | str,
    *,
    standard_template: str,
    daily_template: str,
    anime_template: str,
    series_type: str,                           # "standard" | "daily" | "anime" | "auto"
    title: str,
    year: Optional[int],
    tvdb_id: Optional[str] = None,
    tmdb_id: Optional[str] = None,
    episodes_by_se: dict[tuple[int, int], dict],
    overrides_by_file: dict[str, dict],
) -> list[RenamePlanItem]:
    """Produce a rename plan for every episode file under ``folder``.

    ``series_type="auto"`` picks per-file: the anime template is used for
    files matching the fansub regex, the daily template for files where
    the parser extracted an air-date, otherwise the standard template.
    """
    folder_p = Path(folder)
    plan: list[RenamePlanItem] = []
    seen: set[str] = set()
    manual = (series_type or "auto").lower()

    def _emit(parsed_path: Path, parsed_season: int, parsed_episode: int,
              parsed_air_date: Optional[str]):
        ovr = overrides_by_file.get(str(parsed_path)) or {}
        season = ovr.get("season") if ovr.get("season") is not None else parsed_season
        episode = ovr.get("episode") if ovr.get("episode") is not None else parsed_episode
        ext = parsed_path.suffix.lower()
        stem = parsed_path.stem
        ep_meta = (
            episodes_by_se.get((int(season), int(episode)))
            if season is not None and episode is not None
            else None
        )
        ep_title = (ep_meta or {}).get("name") or ""
        if ep_meta is not None and parsed_air_date is None:
            parsed_air_date = (ep_meta.get("aired") or "")
        mi = mi_svc.probe_file(parsed_path)
        quality_full = mi_svc.build_quality_full(stem, mi)
        release_group = mi_svc.extract_release_group(stem)

        # Pick template.
        if manual == "standard":
            template = standard_template
        elif manual == "daily":
            template = daily_template
        elif manual == "anime":
            template = anime_template
        else:
            if _looks_anime(parsed_path.name):
                template = anime_template
            elif parsed_air_date:
                template = daily_template
            else:
                template = standard_template

        ctx = build_context(
            title=title,
            year=year,
            tvdb_id=tvdb_id,
            tmdb_id=tmdb_id,
            season=int(season) if season is not None else None,
            episode=int(episode) if episode is not None else None,
            episode_title=ep_title,
            air_date=parsed_air_date or "",
            release_group=release_group,
            quality_full=quality_full,
            mi=mi,
        )
        new_name_raw = render_sonarr_template(template, ctx)
        new_name = sanitize(new_name_raw) or parsed_path.name
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

    # Season subdirs.
    for sd in detect_season_dirs(folder_p):
        snum = season_number_from_dir(sd.name)
        for parsed in list_season_episodes(sd):
            if not getattr(parsed, "parsed", True):
                ovr = overrides_by_file.get(str(parsed.path)) or {}
                if ovr.get("season") is None or ovr.get("episode") is None:
                    continue
            _emit(parsed.path, snum, parsed.episode, parsed.air_date)

    # Loose root files (anime / OVA layouts).
    for parsed in list_season_episodes(folder_p):
        if not getattr(parsed, "parsed", True):
            ovr = overrides_by_file.get(str(parsed.path)) or {}
            if ovr.get("season") is None or ovr.get("episode") is None:
                continue
        _emit(parsed.path, parsed.season or 1, parsed.episode, parsed.air_date)

    return plan


def plan_movie_rename(
    folder: Path | str,
    *,
    template: str,
    title: str,
    year: Optional[int],
    tmdb_id: Optional[str] = None,
    tvdb_id: Optional[str] = None,
    imdb_id: Optional[str] = None,
) -> list[RenamePlanItem]:
    """Produce a rename plan for every video file directly inside ``folder``."""
    folder_p = Path(folder)
    plan: list[RenamePlanItem] = []
    seen: set[str] = set()
    if not folder_p.is_dir():
        return plan
    from .parser import VIDEO_EXT  # local import to avoid cycle
    for f in sorted(folder_p.iterdir()):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in VIDEO_EXT:
            continue
        mi = mi_svc.probe_file(f)
        ctx = build_context(
            title=title,
            year=year,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
            release_group=mi_svc.extract_release_group(f.stem),
            quality_full=mi_svc.build_quality_full(f.stem, mi),
            mi=mi,
        )
        new_name_raw = render_sonarr_template(template, ctx)
        new_name = sanitize(new_name_raw) or f.name
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


def apply_rename_plan(plan: Iterable[RenamePlanItem], *,
                      skip_conflicts: bool = True) -> dict:
    """Execute the plan. Returns a summary dict.

    Each successful rename also moves any per-file override row in the
    database so the Episodes tab keeps showing the same selection after
    the rename. Source folder is enforced - any plan item whose ``dst``
    would leave the parent directory is skipped.
    """
    renamed: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    for item in plan:
        if item.src == item.dst:
            skipped.append({"src": item.src, "reason": "no-op"})
            continue
        if item.conflict and skip_conflicts:
            skipped.append({"src": item.src, "dst": item.dst,
                            "reason": item.conflict})
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
                db.rename_episode_file_override(
                    item.folder_path, item.src, item.dst,
                )
            except Exception as de:  # noqa: BLE001
                logger.warning(
                    "override row move failed {} -> {}: {}",
                    item.src, item.dst, de,
                )
            renamed.append({"src": item.src, "dst": item.dst})
        except Exception as e:  # noqa: BLE001
            failed.append({"src": item.src, "dst": item.dst, "reason": str(e)})

    return {
        "renamed": renamed,
        "skipped": skipped,
        "failed": failed,
    }
