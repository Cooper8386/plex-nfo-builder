"""Folder & filename parsing for the user's media layout.

v0.9.0 makes parsing tolerant of Sonarr/Radarr's recommended naming schemes
(see https://trash-guides.info). The TL;DR:

* **Sonarr standard episode**: ``Title (Year) - SxxExx - Episode Title
  [WEB-1080p][x264][...] -GROUP.mkv`` \u2014 already handled by ``EP_RE``.
* **Sonarr daily episode**: ``Title (Year) - YYYY-MM-DD - Episode Title
  [...].mkv`` \u2014 newly supported via ``DAILY_RE``.
* **Sonarr anime episode**: same as standard but with extra brackets like
  ``[10bit]`` and ``[JA]`` between the title and release group. ``EP_RE``
  already matches the SxxExx; the bracket stripping below tolerates the
  rest.
* **Radarr movie**: ``Movie CleanTitle (Year) {tmdb-NNNN} {edition-...}
  [WEB-1080p][...] -GROUP.mkv``. The ``{tmdb-NNNN}`` tag is recognised
  inside both folder names and filenames.

Crucially we now *also* keep video files that we **can't** parse so the
scanner can show \u201cN video files\u201d even if they don't follow a known
scheme \u2014 the user can then bind them manually instead of being told the
folder is empty.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

# Title (YYYY) {tvdb-12345}  OR  Title (YYYY) {tmdb-12345}  OR  Title (YYYY)  OR  Title
FOLDER_RE = re.compile(
    r"^(?P<title>.+?)"
    r"(?:\s*\((?P<year>\d{4})\))?"
    r"(?:\s*\{(?P<provider>tvdb|tmdb|imdb)-(?P<eid>[^}]+)\})?"
    r"\s*$"
)

# Episode patterns supported (anywhere in name):
#   SxxExx, sxxexx, SxxExxExx (multi-ep), then optional title up to first bracket [
EP_RE = re.compile(r"S(?P<s>\d{1,2})E(?P<e>\d{1,3})(?:[-E](?P<e2>\d{1,3}))?", re.IGNORECASE)

# Sonarr daily-format: "Title (YYYY) - YYYY-MM-DD - Episode CleanTitle ...".
# We require an isolated YYYY-MM-DD anchored by spaces/dashes so we don't
# accidentally match release years or random numeric dashes.
DAILY_RE = re.compile(
    r"(?<![0-9])(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?![0-9])"
)

# Strip release/quality tags like [WEBDL-1080p][8bit][x264][AAC 2.0][JA] -GROUP
BRACKETS_RE = re.compile(r"\[[^\]]*\]")

# Movie tag in filename: {tmdb-12345} {imdb-tt12345} {tvdb-…}
ID_TAG_RE = re.compile(r"\{(?P<provider>tvdb|tmdb|imdb)-(?P<eid>[^}]+)\}")

# Edition tag (Radarr): {edition-Director's Cut}
EDITION_TAG_RE = re.compile(r"\{edition-[^}]*\}", re.IGNORECASE)

# Common video file extensions. We're conservative; Plex understands more,
# but these cover ~99% of real-world libraries.
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".webm", ".wmv", ".flv"}


@dataclass
class ParsedFolder:
    raw: str
    title: str
    year: Optional[int] = None
    provider: Optional[str] = None
    external_id: Optional[str] = None


@dataclass
class ParsedEpisode:
    path: Path
    season: int
    episode: int
    end_episode: Optional[int] = None
    raw_title: Optional[str] = None
    extension: str = ""
    air_date: Optional[str] = None  # YYYY-MM-DD for daily-format files
    parsed: bool = True              # False if we kept the file but couldn't parse


@dataclass
class ParsedMovie:
    path: Path
    title: str
    year: Optional[int] = None
    provider: Optional[str] = None
    external_id: Optional[str] = None


@dataclass
class SeriesFolderScan:
    folder: ParsedFolder
    seasons: dict[int, list[ParsedEpisode]] = field(default_factory=dict)
    nfo_state: str = "none"  # none | partial | complete | foreign | mixed | stale
    has_provenance: bool = False
    nfo_episode_count: int = 0
    episode_count: int = 0


def parse_folder_name(name: str) -> ParsedFolder:
    name = name.strip()
    # Remove any edition tags before parsing — they break the trailing
    # "{provider-id}" match that anchors the regex.
    cleaned = EDITION_TAG_RE.sub("", name).strip()
    m = FOLDER_RE.match(cleaned)
    if not m:
        return ParsedFolder(raw=name, title=name)
    title = m.group("title").strip()
    year = m.group("year")
    return ParsedFolder(
        raw=name,
        title=title,
        year=int(year) if year else None,
        provider=m.group("provider"),
        external_id=m.group("eid"),
    )


def _clean_episode_title(rest: str) -> Optional[str]:
    """Strip release-group / quality tags from the trailing portion of a
    Sonarr-style episode filename and return the human title, if any.
    """
    cleaned = BRACKETS_RE.sub("", rest).strip(" -._")
    # Remove a final "-GROUP" token (no trailing space) and anything after a
    # lone " -GROUP" boundary that some release groups use.
    cleaned = re.sub(r"\s+-[^\s][^\s]*$", "", cleaned).strip(" -._")
    cleaned = re.sub(r"-[^\s-]+$", "", cleaned).strip(" -._")
    if not cleaned:
        return None
    return cleaned.replace(".", " ").strip() or None


def parse_episode_filename(path: Path) -> Optional[ParsedEpisode]:
    """Parse a single video file into a ParsedEpisode.

    Returns ``None`` only when the file is *not* a video. For video files
    that don't match any known scheme we still return a ParsedEpisode with
    ``parsed=False`` and ``season=episode=0`` so the scanner can include it
    in counts. The builder will skip such files (no metadata available).
    """
    if path.suffix.lower() not in VIDEO_EXT:
        return None
    stem = path.stem

    # Standard SxxExx (covers Sonarr standard + anime episode formats).
    m = EP_RE.search(stem)
    if m:
        s = int(m.group("s"))
        e = int(m.group("e"))
        e2 = m.group("e2")
        title_part = _clean_episode_title(stem[m.end():])
        return ParsedEpisode(
            path=path,
            season=s,
            episode=e,
            end_episode=int(e2) if e2 else None,
            raw_title=title_part,
            extension=path.suffix.lower(),
            parsed=True,
        )

    # Sonarr daily format: Title (Year) - YYYY-MM-DD - Episode Title
    dm = DAILY_RE.search(stem)
    if dm:
        y, mo, d = int(dm.group("y")), int(dm.group("m")), int(dm.group("d"))
        try:
            air = date(y, mo, d).isoformat()
        except ValueError:
            air = None
        if air:
            title_part = _clean_episode_title(stem[dm.end():])
            return ParsedEpisode(
                path=path,
                season=0,           # builder fills the real season after lookup
                episode=0,          # ditto
                raw_title=title_part,
                extension=path.suffix.lower(),
                air_date=air,
                parsed=True,
            )

    # Last-resort: keep the file with parsed=False so it's counted but the
    # builder treats it as unmatched. This is the v0.9.0 "see all video
    # files even if they aren't named correctly" behaviour.
    return ParsedEpisode(
        path=path,
        season=0,
        episode=0,
        raw_title=None,
        extension=path.suffix.lower(),
        parsed=False,
    )


def parse_movie_filename(path: Path) -> ParsedMovie:
    stem = path.stem
    cleaned = BRACKETS_RE.sub("", stem).strip(" -")
    # Edition tag may appear between year and provider id; strip it before
    # the provider regex so the id tag still matches.
    cleaned = EDITION_TAG_RE.sub("", cleaned).strip(" -")
    m = ID_TAG_RE.search(cleaned)
    provider = m.group("provider") if m else None
    eid = m.group("eid") if m else None
    if m:
        cleaned = (cleaned[: m.start()] + cleaned[m.end():]).strip(" -")
    yr_match = re.search(r"\((\d{4})\)", cleaned)
    year = int(yr_match.group(1)) if yr_match else None
    if yr_match:
        cleaned = (cleaned[: yr_match.start()] + cleaned[yr_match.end():]).strip(" -")
    title = re.sub(r"-[^\s-]+$", "", cleaned).strip(" -.")
    return ParsedMovie(
        path=path,
        title=title or stem,
        year=year,
        provider=provider,
        external_id=eid,
    )


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXT


def list_season_episodes(season_dir: Path) -> list[ParsedEpisode]:
    """Return all video files under ``season_dir`` as ParsedEpisode objects.

    v0.9.0: video files that fail every parser are still returned with
    ``parsed=False`` so they appear in the UI — rather than being silently
    dropped — and so the user can see at a glance that the file exists.
    """
    eps: list[ParsedEpisode] = []
    if not season_dir.is_dir():
        return eps
    for f in sorted(season_dir.iterdir()):
        if f.is_file() and is_video(f):
            parsed = parse_episode_filename(f)
            if parsed:
                eps.append(parsed)
    return eps


def detect_season_dirs(series_dir: Path) -> list[Path]:
    """Return season-style subdirectories (Season XX, Specials, Season 0)."""
    if not series_dir.is_dir():
        return []
    out: list[Path] = []
    for d in sorted(series_dir.iterdir()):
        if not d.is_dir():
            continue
        n = d.name.lower()
        if (
            n.startswith("season")
            or n == "specials"
            or n == "extras"
        ):
            out.append(d)
    return out


def season_number_from_dir(name: str) -> int:
    n = name.lower()
    if n.startswith("specials") or n == "extras":
        return 0
    m = re.search(r"(\d+)", name)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# Folder-classification helpers (v0.9.0)
# ---------------------------------------------------------------------------


def folder_has_provider_tag(folder_name: str) -> tuple[Optional[str], Optional[str]]:
    """Return (provider, external_id) if the folder name has a Radarr/Sonarr
    style ``{provider-id}`` tag, else (None, None)."""
    pf = parse_folder_name(folder_name)
    return pf.provider, pf.external_id


def folder_root_videos(folder: Path) -> list[Path]:
    """Return video files directly inside ``folder`` (not recursing)."""
    if not folder.is_dir():
        return []
    return sorted(
        f for f in folder.iterdir() if f.is_file() and is_video(f)
    )


def folder_looks_like_movie(folder: Path) -> bool:
    """Heuristic: a folder is movie-like if there are no season subdirs but
    there is at least one video file directly inside the folder.

    This lets users keep Radarr movies in libraries that the app classifies
    as TV (anime libraries are commonly mixed) without manual configuration.
    """
    if detect_season_dirs(folder):
        return False
    return bool(folder_root_videos(folder))
