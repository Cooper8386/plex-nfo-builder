"""TMDB v3 async client with built-in SQLite cache.

Reference: https://developer.themoviedb.org/reference/intro/getting-started

We reuse the `tvdb_cache` table (it's a generic key/value cache) but namespace
keys with `tmdb:` so they don't collide with TVDB entries.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx
from loguru import logger

from ..config import effective_tmdb_credentials, get_user_settings
from ..db import cache_get, cache_set

API_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p"


class TMDBError(RuntimeError):
    pass


def image_url(path: Optional[str], size: str = "original") -> Optional[str]:
    """Build a full TMDB image URL from a relative `file_path`."""
    if not path:
        return None
    if path.startswith("http"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return f"{IMG_BASE}/{size}{path}"


class TMDBClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=30.0,
            headers={"Accept": "application/json", "User-Agent": "plex-nfo-builder/0.5"},
        )
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- Core request -------------------------------------------------------

    async def _get(self, path: str, params: Optional[dict] = None,
                   *, ttl: int = 0, force: bool = False) -> dict:
        api_key = effective_tmdb_credentials()
        if not api_key:
            raise TMDBError("TMDB API key is not configured. Set TMDB_API_KEY or save it in Settings.")
        full_params = dict(params or {})
        # Don't include api_key in cache key — switching keys shouldn't invalidate cache.
        cache_params = {k: v for k, v in full_params.items() if k != "api_key"}
        key = self._cache_key(path, cache_params)
        if not force and ttl != 0:
            cached = cache_get(key)
            if cached is not None:
                logger.debug("TMDB cache hit: {}", key)
                return cached
        full_params["api_key"] = api_key
        for attempt in range(3):
            try:
                r = await self._client.get(path, params=full_params)
            except httpx.HTTPError as e:
                logger.warning("TMDB GET {} attempt {} failed: {}", path, attempt + 1, e)
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 429:
                wait = int(r.headers.get("retry-after", "5"))
                logger.warning("TMDB rate-limited; sleeping {}s", wait)
                await asyncio.sleep(wait)
                continue
            if 500 <= r.status_code < 600:
                logger.warning("TMDB {} {}: {}", r.status_code, path, r.text[:200])
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 401:
                raise TMDBError("TMDB rejected the API key (401). Check Settings.")
            if r.status_code != 200:
                raise TMDBError(f"TMDB GET {path} failed {r.status_code}: {r.text[:300]}")
            data = r.json()
            if ttl != 0:
                cache_set(key, data, ttl=ttl)
            return data
        raise TMDBError(f"TMDB GET {path} failed after retries")

    @staticmethod
    def _cache_key(path: str, params: Optional[dict]) -> str:
        if not params:
            return f"tmdb:{path}"
        items = sorted(params.items())
        qs = "&".join(f"{k}={v}" for k, v in items)
        return f"tmdb:{path}?{qs}"

    # ---- Convenience endpoints ---------------------------------------------

    @staticmethod
    def _lang_param(language: Optional[str]) -> str:
        """Convert 3-letter (TVDB-style) to 2-letter where possible for TMDB."""
        if not language:
            return "en-US"
        m = {
            "eng": "en-US", "en": "en-US",
            "spa": "es-ES", "es": "es-ES",
            "fra": "fr-FR", "fr": "fr-FR",
            "deu": "de-DE", "ger": "de-DE", "de": "de-DE",
            "ita": "it-IT", "it": "it-IT",
            "jpn": "ja-JP", "ja": "ja-JP",
            "kor": "ko-KR", "ko": "ko-KR",
            "rus": "ru-RU", "ru": "ru-RU",
            "por": "pt-PT", "pt": "pt-PT",
            "zho": "zh-CN", "chi": "zh-CN", "zh": "zh-CN",
        }
        return m.get(language.lower(), "en-US")

    async def search(self, query: str, type_: str = "tv", year: Optional[int] = None,
                     language: Optional[str] = None, limit: int = 20,
                     include_adult: bool = True) -> list[dict]:
        """Search TMDB.

        v0.9.3: ``include_adult`` defaults to True so adult-themed anime
        and other shows TMDB flags as adult (e.g. Shishunki no Obenkyou,
        id 153655) actually appear. Without this, the user sees
        \u201cNo results yet\u201d for a show that very obviously exists on
        themoviedb.org. The flag also covers movies.

        We additionally retry the search with ``language=en-US`` if a
        non-default language returns nothing \u2014 niche anime is often
        only indexed under English/romaji titles in TMDB.
        """
        path = "/search/tv" if type_ == "tv" or type_ == "series" else "/search/movie"
        primary_lang = self._lang_param(language)
        ttl = self._ttl()

        async def _do(lang_code: str, with_year: bool) -> list[dict]:
            params: dict[str, Any] = {
                "query": query,
                "language": lang_code,
                "include_adult": "true" if include_adult else "false",
            }
            if with_year and year:
                params["year" if type_ == "movie" else "first_air_date_year"] = year
            data = await self._get(path, params=params, ttl=ttl)
            return data.get("results") or []

        results = await _do(primary_lang, with_year=True)
        # Retry without year if year-filtered returns nothing (off-by-one).
        if not results and year:
            results = await _do(primary_lang, with_year=False)
        # Retry in English if a non-en language returned nothing \u2014 TMDB's
        # title index is best in English/romaji for niche anime.
        if not results and primary_lang != "en-US":
            results = await _do("en-US", with_year=bool(year))
            if not results and year:
                results = await _do("en-US", with_year=False)
        return results[:limit]

    async def tv_details(self, tv_id: int | str, *, language: Optional[str] = None,
                         force: bool = False) -> dict:
        params = {
            "language": self._lang_param(language),
            "append_to_response": "external_ids,credits,content_ratings,keywords",
        }
        return await self._get(f"/tv/{tv_id}", params=params, ttl=self._ttl(), force=force)

    async def tv_keywords(self, tv_id: int | str, *, force: bool = False) -> dict:
        """Fetch TV keywords. Returns {results: [{id, name}, ...]}."""
        return await self._get(
            f"/tv/{tv_id}/keywords", params=None, ttl=self._ttl(), force=force
        )

    async def tv_season(self, tv_id: int | str, season: int, *,
                        language: Optional[str] = None, force: bool = False) -> dict:
        params = {"language": self._lang_param(language)}
        return await self._get(
            f"/tv/{tv_id}/season/{season}", params=params, ttl=self._ttl(), force=force
        )

    async def tv_images(self, tv_id: int | str, *, force: bool = False) -> dict:
        # include_image_language=null,en,xx returns posters with no language flag too
        params = {"include_image_language": "null,en"}
        return await self._get(
            f"/tv/{tv_id}/images", params=params, ttl=self._ttl(), force=force
        )

    async def tv_season_images(self, tv_id: int | str, season: int,
                               *, force: bool = False) -> dict:
        params = {"include_image_language": "null,en"}
        return await self._get(
            f"/tv/{tv_id}/season/{season}/images",
            params=params, ttl=self._ttl(), force=force,
        )

    async def movie_details(self, movie_id: int | str, *, language: Optional[str] = None,
                            force: bool = False) -> dict:
        params = {
            "language": self._lang_param(language),
            "append_to_response": "external_ids,credits,release_dates,keywords",
        }
        return await self._get(f"/movie/{movie_id}", params=params, ttl=self._ttl(), force=force)

    async def movie_keywords(self, movie_id: int | str, *, force: bool = False) -> dict:
        """Fetch movie keywords. Returns {keywords: [{id, name}, ...]}."""
        return await self._get(
            f"/movie/{movie_id}/keywords", params=None, ttl=self._ttl(), force=force
        )

    async def movie_images(self, movie_id: int | str, *, force: bool = False) -> dict:
        params = {"include_image_language": "null,en"}
        return await self._get(
            f"/movie/{movie_id}/images", params=params, ttl=self._ttl(), force=force
        )

    @staticmethod
    def _ttl() -> int:
        s = get_user_settings()
        return int(s.cache_ttl_hours * 3600)


_singleton: Optional[TMDBClient] = None


def get_client() -> TMDBClient:
    global _singleton
    if _singleton is None:
        _singleton = TMDBClient()
    return _singleton
