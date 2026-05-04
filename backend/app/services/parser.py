"""Folder & filename parsing for the user's media layout."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
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

# Strip release/quality tags like [WEBDL-1080p][8bit][x264][AAC 2.0][JA] -GROUP
BRACKETS_RE = re.compile(r"\[[^\]]*\]")

# Movie tag in filename: {tmdb-12345} {imdb-tt12345} {tvdb-…}
ID_TAG_RE = re.compile(r"\{(?P<provider>tvdb|tmdb|imdb)-(?P<eid>[^}]+)\}")


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
    m = FOLDER_RE.match(name)
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


def parse_episode_filename(path: Path) -> Optional[ParsedEpisode]:
    stem = path.stem
    m = EP_RE.search(stem)
    if not m:
        return None
    s = int(m.group("s"))
    e = int(m.group("e"))
    e2 = m.group("e2")
    cleaned = BRACKETS_RE.sub("", stem).strip(" -")
    # try to extract human title between SxxExx and the first bracket / dash-group
    title_part: Optional[str] = None
    after = cleaned[m.end() :].strip(" -._")
    # cut release group: anything after the FINAL space-dash boundary
    # e.g. 'The Alchemist -Tsundere-Raws' -> 'The Alchemist'
    after = re.sub(r"\s+-[^\s][^\s]*$", "", after).strip(" -._")
    # also strip trailing -GROUP without leading space
    after = re.sub(r"-[^\s-]+$", "", after).strip(" -._")
    if after:
        title_part = after.replace(".", " ").strip()
    return ParsedEpisode(
        path=path,
        season=s,
        episode=e,
        end_episode=int(e2) if e2 else None,
        raw_title=title_part or None,
        extension=path.suffix.lower(),
    )


def parse_movie_filename(path: Path) -> ParsedMovie:
    stem = path.stem
    cleaned = BRACKETS_RE.sub("", stem).strip(" -")
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
    return ParsedMovie(path=path, title=title or stem, year=year, provider=provider, external_id=eid)


VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".webm"}


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXT


def list_season_episodes(season_dir: Path) -> list[ParsedEpisode]:
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
    if not series_dir.is_dir():
        return []
    out: list[Path] = []
    for d in sorted(series_dir.iterdir()):
        if d.is_dir() and (d.name.lower().startswith("season") or d.name.lower() == "specials"):
            out.append(d)
    return out


def season_number_from_dir(name: str) -> int:
    n = name.lower()
    if n.startswith("specials"):
        return 0
    m = re.search(r"(\d+)", name)
    return int(m.group(1)) if m else 0
