"""Artwork download.

v0.3.0+: artwork is downloaded directly to the show/movie folder root using
Plex's canonical filenames (poster.jpg, background.jpg, banner.jpg,
Season01-poster.jpg, etc.). The NFO files always reference the TVDB CDN URL
in their <thumb> tags so Plex can fall back to the network if a local file is
missing or unreadable. There is no .artwork/ subfolder and no symlinks — both
caused mount/permission issues in earlier versions.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

import httpx
from loguru import logger

from .. import db


# TVDB serves artwork from a CDN host that's separate from the API host.
# The /search and /artworks endpoints sometimes return absolute URLs and
# sometimes return paths like "/banners/v4/episode/.../screencap.jpg".
# We always normalise to an absolute URL before download or embedding in NFOs.
TVDB_IMAGE_BASE = "https://artworks.thetvdb.com"


def absolutize_tvdb_url(url: Optional[str]) -> Optional[str]:
    """Prepend the TVDB artwork CDN host to a relative path.

    Returns absolute http(s) URLs unchanged. Returns None for None/empty input.
    Path-only values ("/banners/...") and protocol-relative values
    ("//artworks.thetvdb.com/...") are both promoted to https://....
    """
    if not url:
        return None
    s = url.strip()
    if not s:
        return None
    low = s.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return s
    if s.startswith("//"):
        return "https:" + s
    if s.startswith("/"):
        return TVDB_IMAGE_BASE + s
    # Otherwise assume it's already a host-relative resource on the CDN.
    return f"{TVDB_IMAGE_BASE}/{s}"


# TVDB v4 artwork type IDs.
# Reference: GET /artwork/types
SERIES_POSTER = 2
SERIES_BACKGROUND = 3
SERIES_BANNER = 1
SERIES_CLEARLOGO = 23
SERIES_CLEARART = 22
SEASON_POSTER = 7
SEASON_BANNER = 8

MOVIE_POSTER = 14
MOVIE_BACKGROUND = 15
MOVIE_BANNER = 16
MOVIE_CLEARLOGO_TYPES = {25}
MOVIE_CLEARART_TYPES = {24}


def _rank_factory(prefer_languages: Optional[list[str]]):
    prefer_languages = prefer_languages or []

    def rank(a: dict) -> tuple:
        lang = a.get("language") or ""
        if lang in prefer_languages:
            lang_rank = prefer_languages.index(lang)
        elif not lang:
            lang_rank = len(prefer_languages)
        else:
            lang_rank = len(prefer_languages) + 1
        score = a.get("score") or 0
        return (lang_rank, -score)
    return rank


def list_candidates(artworks: Iterable[dict], type_id: int,
                    prefer_languages: Optional[list[str]] = None,
                    season_number: Optional[int] = None,
                    series: Optional[dict] = None) -> list[dict]:
    """Return candidate artworks of a type, sorted best-first.

    Each item is a dict with id/url/thumb/language/score (and seasonNumber for seasons).
    """
    out: list[dict] = []
    rank = _rank_factory(prefer_languages)
    for a in artworks or []:
        if not isinstance(a, dict):
            continue
        if a.get("type") != type_id:
            continue
        url = a.get("image") or a.get("url")
        if not url:
            continue
        if season_number is not None and series is not None:
            sn = _season_number_for_artwork(a, series)
            if sn != season_number:
                continue
        full_url = absolutize_tvdb_url(url)
        thumb = absolutize_tvdb_url(a.get("thumbnail")) or full_url
        if not full_url:
            continue
        out.append({
            "id": a.get("id"),
            "url": full_url,
            "thumb": thumb,
            "language": a.get("language"),
            "score": a.get("score") or 0,
            "type": a.get("type"),
            "seasonNumber": _season_number_for_artwork(a, series) if series else a.get("seasonNumber"),
        })
    out.sort(key=lambda d: rank({"language": d["language"], "score": d["score"]}))
    return out


def best_artwork_url(artworks: Iterable[dict], type_id: int,
                     prefer_languages: Optional[list[str]] = None) -> Optional[str]:
    """Pick the highest-scored artwork of `type_id`, optionally preferring a language."""
    candidates = [a for a in (artworks or []) if isinstance(a, dict) and a.get("type") == type_id and (a.get("image") or a.get("url"))]
    if not candidates:
        return None
    candidates.sort(key=_rank_factory(prefer_languages))
    return absolutize_tvdb_url(candidates[0].get("image") or candidates[0].get("url"))


async def _download(client: httpx.AsyncClient, url: str, dest: Path,
                    *, force: bool = False) -> bool:
    """Download `url` to `dest`. Atomic via .part rename. Returns True on success.

    As of v0.5.1 we always overwrite an existing file when this function runs —
    the build pipeline now treats every build as a refresh of the on-disk
    artwork. ``force`` is kept for API compatibility but is no longer required
    to replace a stale image.
    """
    abs_url = absolutize_tvdb_url(url)
    if not abs_url:
        logger.debug("Skipping artwork with empty URL -> {}", dest)
        return False
    # Note: ``force`` is intentionally ignored. Re-running a build always
    # replaces the on-disk artwork so users do not have to delete files first.
    _ = force  # silence unused-arg linter
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with client.stream("GET", abs_url, timeout=60.0) as r:
            if r.status_code != 200:
                logger.warning("Artwork {} -> HTTP {}", abs_url, r.status_code)
                return False
            tmp = dest.with_suffix(dest.suffix + ".part")
            with tmp.open("wb") as f:
                async for chunk in r.aiter_bytes():
                    f.write(chunk)
            tmp.replace(dest)
        return True
    except Exception as e:
        logger.warning("Artwork download failed for {}: {}", abs_url, e)
        return False


def _ext_from_url(url: str, default: str = ".jpg") -> str:
    suf = os.path.splitext(urlparse(url).path)[1].lower()
    if suf in (".jpg", ".jpeg", ".png", ".webp"):
        return ".jpg" if suf == ".jpeg" else suf
    return default


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def _safe_name(name: str) -> str:
    return _SAFE_NAME.sub("_", name)


# ---- Public API ------------------------------------------------------------

async def download_series_canonical(folder: Path, series: dict,
                                    artworks: Iterable[dict],
                                    episodes: Optional[Iterable[dict]] = None,
                                    *, prefer_languages: Optional[list[str]] = None,
                                    force: bool = False,
                                    preferred_overrides: Optional[dict[str, str]] = None) -> dict:
    """Download poster/background/banner/season-posters and episode thumbnails
    directly into `folder` using Plex canonical naming.

    Returns a manifest dict for logging.
    """
    artworks_list = list(artworks or [])
    manifest: dict[str, str] = {}
    async with httpx.AsyncClient(headers={"User-Agent": "plex-nfo-builder/0.3"}) as http:
        tasks: list[asyncio.Task] = []

        async def _grab(url: Optional[str], dest: Path, key: str) -> None:
            if not url:
                return
            ok = await _download(http, url, dest, force=force)
            if ok:
                manifest[key] = str(dest)

        # Consult per-folder selections so the user's picks win over defaults.
        selections = db.get_artwork_selections(str(folder))

        def _pick(slot: str, default_url: Optional[str]) -> Optional[str]:
            sel = selections.get(slot)
            if sel and sel.get("url"):
                return sel["url"]
            return default_url

        # Series poster
        poster_url = _pick(
            "poster",
            best_artwork_url(artworks_list, SERIES_POSTER, prefer_languages)
            or absolutize_tvdb_url(series.get("image")),
        )
        tasks.append(asyncio.create_task(_grab(poster_url, folder / "poster.jpg", "poster")))

        # Background / fanart
        bg_url = _pick(
            "background", best_artwork_url(artworks_list, SERIES_BACKGROUND, prefer_languages)
        )
        tasks.append(asyncio.create_task(_grab(bg_url, folder / "background.jpg", "background")))

        # Banner
        banner_url = _pick(
            "banner", best_artwork_url(artworks_list, SERIES_BANNER, prefer_languages)
        )
        tasks.append(asyncio.create_task(_grab(banner_url, folder / "banner.jpg", "banner")))

        # Clearlogo
        cl_url = _pick(
            "clearlogo", best_artwork_url(artworks_list, SERIES_CLEARLOGO, prefer_languages)
        )
        if cl_url:
            tasks.append(asyncio.create_task(_grab(cl_url, folder / "clearlogo.png", "clearlogo")))

        # Per-season posters: pick the highest-scored season poster per seasonId, or use override.
        season_posters: dict[int, str] = {}
        for a in artworks_list:
            if not isinstance(a, dict) or a.get("type") != SEASON_POSTER:
                continue
            sn = _season_number_for_artwork(a, series)
            if sn is None:
                continue
            url = absolutize_tvdb_url(a.get("image") or a.get("url"))
            if not url:
                continue
            existing_score = next(
                (x.get("score") or 0 for x in artworks_list
                 if isinstance(x, dict) and x.get("type") == SEASON_POSTER
                 and (x.get("image") or x.get("url")) == season_posters.get(sn)),
                -1,
            )
            if season_posters.get(sn) is None or (a.get("score") or 0) > existing_score:
                season_posters[sn] = url
        # Apply per-season selections on top of defaults.
        for slot, sel in selections.items():
            if not slot.startswith("season-") or not slot.endswith("-poster"):
                continue
            try:
                sn = int(slot.split("-")[1])
            except Exception:
                continue
            if sel.get("url"):
                season_posters[sn] = sel["url"]
        for sn, url in season_posters.items():
            dest = folder / f"Season{int(sn):02d}-poster.jpg"
            tasks.append(asyncio.create_task(_grab(url, dest, f"season{sn:02d}_poster")))

        # Episode thumbnails: write next to the episode .nfo (caller provides
        # mapping via episodes argument with `_local_path` set).
        for ep in episodes or []:
            if not isinstance(ep, dict):
                continue
            url = absolutize_tvdb_url(ep.get("image"))
            local = ep.get("_local_path")
            if not url or not local:
                continue
            local_path = Path(local)
            if not local_path.exists():
                continue
            dest = local_path.with_name(f"{local_path.stem}-thumb{_ext_from_url(url)}")
            tasks.append(asyncio.create_task(_grab(url, dest, f"thumb_{local_path.stem}")))

        if tasks:
            await asyncio.gather(*tasks)
    return manifest


def _season_number_for_artwork(art: dict, series: dict) -> Optional[int]:
    """Resolve seasonNumber for a season artwork by cross-referencing series.seasons."""
    sn = art.get("seasonNumber")
    if sn is not None:
        try:
            return int(sn)
        except Exception:
            pass
    sid = art.get("seasonId") or art.get("season")
    if sid is None:
        return None
    for s in (series.get("seasons") or []):
        if not isinstance(s, dict):
            continue
        if str(s.get("id")) == str(sid):
            try:
                return int(s.get("number"))
            except Exception:
                return None
    return None


async def download_movie_canonical(folder: Path, movie: dict,
                                   artworks: Iterable[dict],
                                   *, prefer_languages: Optional[list[str]] = None,
                                   force: bool = False,
                                   preferred_overrides: Optional[dict[str, str]] = None) -> dict:
    """Download movie poster/background/banner directly to `folder`.

    Plex movies use file-stem-anchored names too, but `poster.jpg` /
    `background.jpg` in the movie folder are also picked up.
    """
    artworks_list = list(artworks or [])
    manifest: dict[str, str] = {}
    async with httpx.AsyncClient(headers={"User-Agent": "plex-nfo-builder/0.3"}) as http:
        async def _grab(url: Optional[str], dest: Path, key: str) -> None:
            if not url:
                return
            if await _download(http, url, dest, force=force):
                manifest[key] = str(dest)

        selections = db.get_artwork_selections(str(folder))
        prefs = preferred_overrides or {}

        def _pick(slot: str, default_url: Optional[str]) -> Optional[str]:
            sel = selections.get(slot)
            if sel and sel.get("url"):
                return sel["url"]
            pv = prefs.get(slot)
            if pv:
                return pv
            return default_url

        poster = _pick(
            "poster",
            best_artwork_url(artworks_list, MOVIE_POSTER, prefer_languages)
            or absolutize_tvdb_url(movie.get("image")),
        )
        background = _pick(
            "background", best_artwork_url(artworks_list, MOVIE_BACKGROUND, prefer_languages)
        )
        banner = _pick(
            "banner", best_artwork_url(artworks_list, MOVIE_BANNER, prefer_languages)
        )
        tasks = [
            asyncio.create_task(_grab(poster, folder / "poster.jpg", "poster")),
            asyncio.create_task(_grab(background, folder / "background.jpg", "background")),
            asyncio.create_task(_grab(banner, folder / "banner.jpg", "banner")),
        ]
        await asyncio.gather(*tasks)
    return manifest


# ---- Best-URL helpers used by NFO builders ---------------------------------

def series_image_urls(series: dict, artworks: Iterable[dict],
                      prefer_languages: Optional[list[str]] = None,
                      folder_path: Optional[str] = None,
                      preferred_overrides: Optional[dict[str, str]] = None) -> dict[str, Optional[str]]:
    arts = list(artworks or [])
    selections = db.get_artwork_selections(folder_path) if folder_path else {}
    prefs = preferred_overrides or {}

    def _pick(slot: str, default: Optional[str]) -> Optional[str]:
        sel = selections.get(slot)
        if sel and sel.get("url"):
            return sel["url"]
        pv = prefs.get(slot)
        if pv:
            return pv
        return default

    return {
        "poster": _pick(
            "poster",
            best_artwork_url(arts, SERIES_POSTER, prefer_languages)
            or absolutize_tvdb_url(series.get("image")),
        ),
        "background": _pick("background", best_artwork_url(arts, SERIES_BACKGROUND, prefer_languages)),
        "banner": _pick("banner", best_artwork_url(arts, SERIES_BANNER, prefer_languages)),
        "clearlogo": _pick("clearlogo", best_artwork_url(arts, SERIES_CLEARLOGO, prefer_languages)),
        "clearart": best_artwork_url(arts, SERIES_CLEARART, prefer_languages),
    }


def movie_image_urls(movie: dict, artworks: Iterable[dict],
                     prefer_languages: Optional[list[str]] = None,
                     folder_path: Optional[str] = None,
                     preferred_overrides: Optional[dict[str, str]] = None) -> dict[str, Optional[str]]:
    arts = list(artworks or [])
    selections = db.get_artwork_selections(folder_path) if folder_path else {}
    prefs = preferred_overrides or {}

    def _pick(slot: str, default: Optional[str]) -> Optional[str]:
        sel = selections.get(slot)
        if sel and sel.get("url"):
            return sel["url"]
        pv = prefs.get(slot)
        if pv:
            return pv
        return default

    return {
        "poster": _pick(
            "poster",
            best_artwork_url(arts, MOVIE_POSTER, prefer_languages)
            or absolutize_tvdb_url(movie.get("image")),
        ),
        "background": _pick("background", best_artwork_url(arts, MOVIE_BACKGROUND, prefer_languages)),
        "banner": _pick("banner", best_artwork_url(arts, MOVIE_BANNER, prefer_languages)),
    }
