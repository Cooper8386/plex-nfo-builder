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
    is_within_folder,
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

    if mapped == "episode" and ctx.get("end_episode") is not None:
        width = len(fmt) if fmt and re.fullmatch(r"0+", fmt) else 1
        return f"{int(raw):0{width}d}-E{int(ctx['end_episode']):0{width}d}"

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
    end_episode: Optional[int] = None,
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
        "end_episode": end_episode,
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
    release_group_override: Optional[str] = None,
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
              parsed_air_date: Optional[str], parsed_end_episode: Optional[int]):
        if parsed_path.is_symlink():
            return
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
        # Explicit episode IDs and daily filenames select the same provider
        # episode as the builder, even when S/E cannot be parsed from the name.
        external_id = ovr.get("external_id")
        if external_id or (parsed_air_date and ovr.get("episode") is None):
            for (candidate_season, candidate_episode), candidate in episodes_by_se.items():
                matches = (str(candidate.get("id")) == str(external_id) if external_id
                           else candidate.get("aired") == parsed_air_date)
                if matches:
                    season, episode, ep_meta = candidate_season, candidate_episode, candidate
                    break
        ep_title = (ep_meta or {}).get("name") or ""
        mi = mi_svc.probe_file(parsed_path)
        quality_full = mi_svc.build_quality_full(stem, mi)
        # v0.11.7: anime fansub names sometimes use a bracket layout that
        # extract_release_group() can't safely guess (e.g. ``[Group A][Group B]Title``
        # or ``Title (Group)``). When the user supplies a manual override we
        # honour it verbatim and skip auto-detection so the {Release Group}
        # token in their template actually expands instead of going blank.
        rg_override = (release_group_override or "").strip()
        release_group = rg_override if rg_override else mi_svc.extract_release_group(stem)

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

        # Metadata air dates enrich a chosen template; they must not turn
        # every standard episode into a daily episode in Auto mode.
        if ep_meta is not None and parsed_air_date is None:
            parsed_air_date = ep_meta.get("aired") or ""
        ctx = build_context(
            title=title,
            year=year,
            tvdb_id=tvdb_id,
            tmdb_id=tmdb_id,
            season=int(season) if season is not None else None,
            episode=int(episode) if episode is not None else None,
            end_episode=(int(episode) + parsed_end_episode - parsed_episode
                         if episode is not None and parsed_end_episode is not None
                         and parsed_end_episode > parsed_episode else None),
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
        if os.path.normcase(dst) in seen:
            conflict = "duplicate"
        elif Path(dst) != parsed_path and os.path.lexists(dst):
            conflict = "exists"
        elif any(os.path.lexists(parsed_path.parent / f"{Path(dst).stem}{suffix}")
                 for _, suffix in _companion_files_for(parsed_path) if Path(dst).stem != parsed_path.stem):
            conflict = "exists"
        seen.add(os.path.normcase(dst))
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
            _emit(parsed.path, snum, parsed.episode, parsed.air_date, parsed.end_episode)

    # Loose root files (anime / OVA layouts).
    for parsed in list_season_episodes(folder_p):
        if not getattr(parsed, "parsed", True):
            ovr = overrides_by_file.get(str(parsed.path)) or {}
            if ovr.get("season") is None or ovr.get("episode") is None:
                continue
        _emit(parsed.path, parsed.season or 1, parsed.episode, parsed.air_date, parsed.end_episode)

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
    release_group_override: Optional[str] = None,
) -> list[RenamePlanItem]:
    """Produce a rename plan for every video file directly inside ``folder``."""
    folder_p = Path(folder)
    plan: list[RenamePlanItem] = []
    seen: set[str] = set()
    if not folder_p.is_dir():
        return plan
    from .parser import VIDEO_EXT  # local import to avoid cycle
    for f in sorted(folder_p.iterdir()):
        if not f.is_file() or f.is_symlink():
            continue
        ext = f.suffix.lower()
        if ext not in VIDEO_EXT:
            continue
        mi = mi_svc.probe_file(f)
        rg_override = (release_group_override or "").strip()
        rg = rg_override if rg_override else mi_svc.extract_release_group(f.stem)
        ctx = build_context(
            title=title,
            year=year,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
            release_group=rg,
            quality_full=mi_svc.build_quality_full(f.stem, mi),
            mi=mi,
        )
        new_name_raw = render_sonarr_template(template, ctx)
        new_name = sanitize(new_name_raw) or f.name
        if not new_name.lower().endswith(ext):
            new_name = f"{new_name}{ext}"
        dst = str(folder_p / new_name)
        conflict: Optional[str] = None
        if os.path.normcase(dst) in seen:
            conflict = "duplicate"
        elif Path(dst) != f and os.path.lexists(dst):
            conflict = "exists"
        elif any(os.path.lexists(f.parent / f"{Path(dst).stem}{suffix}")
                 for _, suffix in _companion_files_for(f) if Path(dst).stem != f.stem):
            conflict = "exists"
        seen.add(os.path.normcase(dst))
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


# Companion-file extensions / suffixes that travel with a video file when it
# gets renamed. ``.nfo`` is the Kodi/Plex sidecar; ``-thumb.{jpg,png}`` is the
# Plex episode thumbnail. Subtitles in arbitrary languages are matched by
# stem-prefix so ``foo.en.srt`` / ``foo.en.forced.srt`` move with ``foo.mkv``.
_COMPANION_SIMPLE_SUFFIXES = (".nfo",)
_COMPANION_THUMB_SUFFIXES = ("-thumb.jpg", "-thumb.jpeg", "-thumb.png")
_SUBTITLE_EXTS = (".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup")


def _companion_files_for(src_p: Path) -> list[tuple[Path, str]]:
    """Return [(companion_path, suffix_relative_to_video_stem)] for ``src_p``.

    The suffix is the *trailing* portion that should be reattached to the new
    stem - e.g. ``".nfo"``, ``"-thumb.jpg"``, ``".en.srt"``. Only files that
    sit next to ``src_p`` and obviously belong to it are matched.
    """
    stem = src_p.stem  # video filename without extension
    parent = src_p.parent
    out: list[tuple[Path, str]] = []
    if not parent.is_dir():
        return out
    for sib in parent.iterdir():
        if not sib.is_file() or sib == src_p:
            continue
        name = sib.name
        # 1. Exact-stem .nfo: "<stem>.nfo"
        for sfx in _COMPANION_SIMPLE_SUFFIXES:
            if name == f"{stem}{sfx}":
                out.append((sib, sfx))
                break
        else:
            # 2. Plex episode thumb: "<stem>-thumb.{jpg,jpeg,png}"
            matched = False
            for sfx in _COMPANION_THUMB_SUFFIXES:
                if name == f"{stem}{sfx}":
                    out.append((sib, sfx))
                    matched = True
                    break
            if matched:
                continue
            # 3. Subtitle / lang-tagged sidecar: "<stem>.<anything>.<ext>"
            #    where <ext> is a known subtitle / nfo suffix. We only move
            #    these when the trailing extension matches one we recognise -
            #    refuse to touch random files that happen to share a prefix.
            if name.startswith(stem + "."):
                tail = name[len(stem):]  # starts with "."
                low = tail.lower()
                for ext in _SUBTITLE_EXTS:
                    if low.endswith(ext):
                        out.append((sib, tail))
                        break
    return out


def apply_rename_plan(plan: Iterable[RenamePlanItem], *,
                      skip_conflicts: bool = True) -> dict:
    """Execute the plan. Returns a summary dict.

    Each successful video rename also relocates the matching ``.nfo``,
    ``-thumb.{jpg,jpeg,png}``, and known subtitle sidecars so they stay
    paired with the renamed video. Per-file override rows in the database
    are migrated to the new path so the Episodes tab keeps showing the
    same selection. Source folder is enforced - any plan item whose
    ``dst`` would leave the parent directory is skipped.
    """
    renamed: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    companions_moved: list[dict] = []
    companions_failed: list[dict] = []

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
        if not is_within_folder(src_p.parent, Path(item.folder_path)):
            failed.append({"src": item.src, "dst": item.dst,
                           "reason": "source outside media folder"})
            continue
        if src_p.is_symlink() or not src_p.is_file():
            failed.append({"src": item.src, "reason": "source missing"})
            continue
        # Snapshot companions BEFORE moving the video, since some are
        # detected via stem-prefix and the stem is about to change.
        companions = _companion_files_for(src_p)
        new_stem = dst_p.stem
        moves = [(src_p, dst_p)] + [
            (source, source.parent / f"{new_stem}{suffix}")
            for source, suffix in companions
            if source.name != f"{new_stem}{suffix}"
        ]
        collision = next((target for _, target in moves if os.path.lexists(target)), None)
        if collision is not None:
            skipped.append({"src": item.src, "dst": item.dst,
                            "reason": f"destination exists: {collision.name}"})
            continue
        completed: list[tuple[Path, Path]] = []
        try:
            for source, target in moves:
                if source.is_symlink() or not is_within_folder(source.parent, Path(item.folder_path)):
                    raise ValueError("linked or out-of-folder source refused")
                _rename_without_overwrite(source, target)
                completed.append((source, target))
            db.rename_episode_file_override(item.folder_path, item.src, item.dst)
            renamed.append({"src": item.src, "dst": item.dst})
            companions_moved.extend({"src": str(source), "dst": str(target)}
                                    for source, target in completed[1:])
        except Exception as e:  # noqa: BLE001
            rollback_errors = []
            for source, target in reversed(completed):
                try:
                    _rename_without_overwrite(target, source)
                except OSError as rollback_error:
                    rollback_errors.append(f"{target}: {rollback_error}")
            reason = str(e)
            if rollback_errors:
                reason += "; partial rename; rollback failed: " + "; ".join(rollback_errors)
                logger.error("{}", reason)
            failed.append({"src": item.src, "dst": item.dst, "reason": reason})

    return {
        "renamed": renamed,
        "skipped": skipped,
        "failed": failed,
        "companions_moved": companions_moved,
        "companions_failed": companions_failed,
    }


def _rename_without_overwrite(source: Path, target: Path) -> None:
    """Move a file without replacing a destination created after the preview.

    Windows rename already refuses existing destinations. POSIX rename does
    not, so atomically create a hard link first. Unsupported filesystems fail
    safely with the source intact rather than falling back to an unsafe move.
    """
    if os.name == "nt":
        os.rename(source, target)
        return
    os.link(source, target, follow_symlinks=False)
    try:
        source.unlink()
    except OSError:
        target.unlink()
        raise
