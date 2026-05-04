"""fanart.tv v3 async client.

Reference: https://fanarttv.docs.apiary.io/

fanart.tv keys metadata by TVDB id for series and TMDB/IMDB id for movies.
We expose a single fetch helper plus normalisers that turn raw responses into
the same {url, thumb, language, score} shape as the TVDB candidate list.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx
from loguru import logger

from ..config import effective_fanart_credentials, get_user_settings
from ..db import cache_get, cache_set

API_BASE = "https://webservice.fanart.tv/v3"

# Map a fanart.tv asset section to one of our internal slot names.
SERIES_SECTION_SLOT: dict[str, str] = {
    "tvposter": "poster",
    "showbackground": "background",
    "tvbanner": "banner",
    "hdtvlogo": "clearlogo",
    "clearlogo": "clearlogo",
    "hdclearart": "clearart",
    "clearart": "clearart",
}

MOVIE_SECTION_SLOT: dict[str, str] = {
    "movieposter": "poster",
    "moviebackground": "background",
    "moviebanner": "banner",
    "hdmovielogo": "clearlogo",
    "movielogo": "clearlogo",
    "hdmovieclearart": "clearart",
    "movieclearart": "clearart",
}


class FanartError(RuntimeError):
    pass


class FanartClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=30.0,
            headers={"Accept": "application/json", "User-Agent": "plex-nfo-builder/0.5"},
        )
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, *, ttl: int, force: bool = False) -> dict:
        api_key = effective_fanart_credentials()
        if not api_key:
            raise FanartError("fanart.tv API key is not configured. Set FANART_API_KEY or save it in Settings.")
        key = f"fanart:{path}"
        if not force and ttl != 0:
            cached = cache_get(key)
            if cached is not None:
                logger.debug("fanart cache hit: {}", key)
                return cached
        params = {"api_key": api_key}
        for attempt in range(3):
            try:
                r = await self._client.get(path, params=params)
            except httpx.HTTPError as e:
                logger.warning("fanart GET {} attempt {} failed: {}", path, attempt + 1, e)
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 404:
                # No artwork for this id — cache an empty response briefly so we
                # don't keep hammering.
                cache_set(key, {}, ttl=min(ttl, 3600))
                return {}
            if r.status_code == 429:
                wait = int(r.headers.get("retry-after", "5"))
                await asyncio.sleep(wait)
                continue
            if 500 <= r.status_code < 600:
                logger.warning("fanart {} {}: {}", r.status_code, path, r.text[:200])
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise FanartError(f"fanart GET {path} failed {r.status_code}: {r.text[:200]}")
            data = r.json()
            if ttl != 0:
                cache_set(key, data, ttl=ttl)
            return data
        raise FanartError(f"fanart GET {path} failed after retries")

    async def series(self, tvdb_id: int | str, *, force: bool = False) -> dict:
        return await self._get(f"/tv/{tvdb_id}", ttl=self._ttl(), force=force)

    async def movie(self, tmdb_or_imdb_id: int | str, *, force: bool = False) -> dict:
        return await self._get(f"/movies/{tmdb_or_imdb_id}", ttl=self._ttl(), force=force)

    @staticmethod
    def _ttl() -> int:
        s = get_user_settings()
        return int(s.cache_ttl_hours * 3600)


_singleton: Optional[FanartClient] = None


def get_client() -> FanartClient:
    global _singleton
    if _singleton is None:
        _singleton = FanartClient()
    return _singleton


# ---- Normalisers ----------------------------------------------------------

def _likes_score(likes: Any) -> int:
    """fanart.tv reports `likes` as a string-int. Higher = more votes."""
    try:
        return int(likes or 0)
    except Exception:
        return 0


def normalise_series_artwork(payload: dict) -> dict[str, list[dict]]:
    """Turn a fanart.tv `/tv/<id>` response into our candidate shape.

    Returns a dict keyed by our slot name (poster, background, banner, clearlogo,
    clearart, season-NN-poster, season-NN-banner) → list of candidates sorted
    best-first.
    """
    out: dict[str, list[dict]] = {}
    if not isinstance(payload, dict):
        return out
    for section, slot in SERIES_SECTION_SLOT.items():
        items = payload.get(section) or []
        for it in items:
            if not isinstance(it, dict):
                continue
            url = it.get("url")
            if not url:
                continue
            cand = {
                "id": None,
                "url": url,
                "thumb": (url.replace("/fanart/", "/preview/") if "/fanart/" in url else url),
                "language": (it.get("lang") or None) or None,
                "score": _likes_score(it.get("likes")),
                "type": None,
                "seasonNumber": None,
                "provider": "fanart",
            }
            out.setdefault(slot, []).append(cand)
    # Season posters / banners
    for section, slot_prefix in (("seasonposter", "poster"), ("seasonbanner", "banner")):
        items = payload.get(section) or []
        for it in items:
            if not isinstance(it, dict):
                continue
            url = it.get("url")
            if not url:
                continue
            try:
                sn = int(it.get("season") or 0)
            except Exception:
                continue
            cand = {
                "id": None,
                "url": url,
                "thumb": (url.replace("/fanart/", "/preview/") if "/fanart/" in url else url),
                "language": it.get("lang") or None,
                "score": _likes_score(it.get("likes")),
                "type": None,
                "seasonNumber": sn,
                "provider": "fanart",
            }
            slot_name = f"season-{sn:02d}-{slot_prefix}"
            out.setdefault(slot_name, []).append(cand)

    for k, lst in out.items():
        lst.sort(key=lambda c: -c["score"])
    return out


def normalise_movie_artwork(payload: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if not isinstance(payload, dict):
        return out
    for section, slot in MOVIE_SECTION_SLOT.items():
        items = payload.get(section) or []
        for it in items:
            if not isinstance(it, dict):
                continue
            url = it.get("url")
            if not url:
                continue
            cand = {
                "id": None,
                "url": url,
                "thumb": (url.replace("/fanart/", "/preview/") if "/fanart/" in url else url),
                "language": it.get("lang") or None,
                "score": _likes_score(it.get("likes")),
                "type": None,
                "seasonNumber": None,
                "provider": "fanart",
            }
            out.setdefault(slot, []).append(cand)
    for k, lst in out.items():
        lst.sort(key=lambda c: -c["score"])
    return out
