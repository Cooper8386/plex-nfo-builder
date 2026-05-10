"""TVDB v4 async client with built-in SQLite cache.

Reference: https://thetvdb.github.io/v4-api/
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx
from loguru import logger

from ..config import effective_tvdb_credentials, get_user_settings
from ..db import cache_get, cache_set

API_BASE = "https://api4.thetvdb.com/v4"


class TVDBError(RuntimeError):
    pass


class TVDBClient:
    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._token_expires: float = 0
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=30.0,
            headers={"Accept": "application/json", "User-Agent": "plex-nfo-builder/0.1"},
        )
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- Auth ---------------------------------------------------------------

    async def _login(self) -> str:
        api_key, pin = effective_tvdb_credentials()
        if not api_key:
            raise TVDBError("TVDB API key is not configured. Set TVDB_API_KEY env or in settings.")
        body: dict[str, Any] = {"apikey": api_key}
        if pin:
            body["pin"] = pin
        logger.info("Logging in to TVDB v4 (pin={})", "yes" if pin else "no")
        r = await self._client.post("/login", json=body)
        if r.status_code != 200:
            raise TVDBError(f"TVDB login failed: {r.status_code} {r.text[:200]}")
        token = r.json().get("data", {}).get("token")
        if not token:
            raise TVDBError("TVDB login returned no token")
        self._token = token
        # Tokens are valid for ~30 days but we re-login proactively after 24h.
        self._token_expires = time.time() + 23 * 3600
        return token

    async def _ensure_token(self) -> str:
        async with self._lock:
            if not self._token or time.time() > self._token_expires:
                await self._login()
            return self._token  # type: ignore[return-value]

    # ---- Core request -------------------------------------------------------

    async def _get(self, path: str, params: Optional[dict] = None,
                   *, ttl: int = 0, cache_key: Optional[str] = None,
                   force: bool = False) -> dict:
        key = cache_key or self._cache_key(path, params)
        if not force and ttl != 0:
            cached = cache_get(key)
            if cached is not None:
                logger.debug("TVDB cache hit: {}", key)
                return cached
        token = await self._ensure_token()
        for attempt in range(3):
            try:
                r = await self._client.get(
                    path, params=params, headers={"Authorization": f"Bearer {token}"}
                )
            except httpx.HTTPError as e:
                logger.warning("TVDB GET {} attempt {} failed: {}", path, attempt + 1, e)
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 401:
                logger.info("TVDB token expired; re-logging in")
                self._token = None
                token = await self._ensure_token()
                continue
            if r.status_code == 429:
                wait = int(r.headers.get("retry-after", "5"))
                logger.warning("TVDB rate-limited; sleeping {}s", wait)
                await asyncio.sleep(wait)
                continue
            if 500 <= r.status_code < 600:
                logger.warning("TVDB {} {}: {}", r.status_code, path, r.text[:200])
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise TVDBError(f"TVDB GET {path} failed {r.status_code}: {r.text[:300]}")
            data = r.json()
            if ttl != 0:
                cache_set(key, data, ttl=ttl)
            return data
        raise TVDBError(f"TVDB GET {path} failed after retries")

    @staticmethod
    def _cache_key(path: str, params: Optional[dict]) -> str:
        if not params:
            return f"tvdb:{path}"
        items = sorted(params.items())
        qs = "&".join(f"{k}={v}" for k, v in items)
        return f"tvdb:{path}?{qs}"

    # ---- Convenience endpoints ---------------------------------------------

    async def search(self, query: str, type_: str = "series", year: Optional[int] = None,
                     language: Optional[str] = None, limit: int = 20) -> list[dict]:
        params: dict[str, Any] = {"query": query, "type": type_, "limit": limit}
        if year:
            params["year"] = year
        if language:
            params["language"] = language
        ttl = self._ttl()
        data = await self._get("/search", params=params, ttl=ttl)
        return data.get("data", []) or []

    async def series_extended(self, series_id: int | str, *, force: bool = False) -> dict:
        ttl = self._ttl()
        data = await self._get(
            f"/series/{series_id}/extended",
            params={"meta": "translations,episodes"},
            ttl=ttl,
            force=force,
        )
        return data.get("data", {}) or {}

    async def series_episodes(self, series_id: int | str, season_type: str = "default",
                              language: Optional[str] = None, *, force: bool = False) -> list[dict]:
        params: dict[str, Any] = {"page": 0}
        path = f"/series/{series_id}/episodes/{season_type}"
        if language:
            path = f"/series/{series_id}/episodes/{season_type}/{language}"
        episodes: list[dict] = []
        page = 0
        while True:
            params["page"] = page
            data = await self._get(path, params=params, ttl=self._ttl(), force=force)
            chunk = (data.get("data", {}) or {}).get("episodes", []) or []
            episodes.extend(chunk)
            links = data.get("links", {}) or {}
            if not links.get("next") or len(chunk) == 0:
                break
            page += 1
            if page > 50:
                break
        return episodes

    async def episode_extended(self, episode_id: int | str, *, force: bool = False) -> dict:
        data = await self._get(
            f"/episodes/{episode_id}/extended",
            params={"meta": "translations"},
            ttl=self._ttl(),
            force=force,
        )
        return data.get("data", {}) or {}

    async def series_artworks(self, series_id: int | str, *, force: bool = False) -> list[dict]:
        data = await self._get(
            f"/series/{series_id}/artworks", ttl=self._ttl(), force=force
        )
        d = data.get("data", {}) or {}
        # endpoint returns object with artworks array
        return d.get("artworks") if isinstance(d, dict) and "artworks" in d else d if isinstance(d, list) else []

    async def person_image(self, people_id: int | str, *, force: bool = False) -> Optional[str]:
        """Return the actor's default headshot URL (the `image` field on
        the People record), or None if missing.

        Used as a fallback for character entries on /series/.../extended
        whose `personImgURL` is empty: TVDB renders those by looking at
        the underlying person record, and so should we. Cached on the
        normal TTL because headshots almost never change.
        """
        try:
            data = await self._get(
                f"/people/{people_id}",
                ttl=self._ttl(),
                force=force,
            )
        except TVDBError as e:
            logger.debug("TVDB /people/{} failed: {}", people_id, e)
            return None
        d = data.get("data") or {}
        if not isinstance(d, dict):
            return None
        img = d.get("image") or None
        return img if isinstance(img, str) and img else None

    async def movie_extended(self, movie_id: int | str, *, force: bool = False) -> dict:
        data = await self._get(
            f"/movies/{movie_id}/extended",
            params={"meta": "translations"},
            ttl=self._ttl(),
            force=force,
        )
        return data.get("data", {}) or {}

    async def get_translation(self, kind: str, ent_id: int | str, language: str,
                              *, force: bool = False) -> Optional[dict]:
        """kind = series | movies | episodes | seasons | people | lists"""
        try:
            data = await self._get(
                f"/{kind}/{ent_id}/translations/{language}",
                ttl=self._ttl(), force=force,
            )
            return data.get("data") or None
        except TVDBError as e:
            logger.debug("No {} translation for {} ({}): {}", language, kind, ent_id, e)
            return None

    async def best_translation(self, kind: str, ent_id: int | str,
                               language: str, fallbacks: list[str],
                               *, force: bool = False) -> Optional[dict]:
        """Try preferred language, then fallbacks. Returns the first non-empty."""
        order: list[str] = []
        for lang in [language, *fallbacks]:
            if lang and lang not in order:
                order.append(lang)
        for lang in order:
            t = await self.get_translation(kind, ent_id, lang, force=force)
            if t and (t.get("name") or t.get("overview")):
                t["_resolved_language"] = lang
                return t
        return None

    async def languages(self, *, force: bool = False) -> list[dict]:
        """Return the catalogue of TVDB-supported languages.

        Each entry has the shape
        ``{id: "eng", name: "English", nativeName: "English"}`` — ``id`` is
        the 3-letter ISO 639-2 code TVDB tags artwork with. We cache for a
        full week regardless of the user's normal cache TTL because the
        list almost never changes.
        """
        try:
            data = await self._get(
                "/languages", ttl=7 * 24 * 3600, force=force
            )
        except TVDBError as e:
            logger.debug("TVDB /languages failed: {}", e)
            return []
        out = data.get("data") or []
        return out if isinstance(out, list) else []

    @staticmethod
    def _ttl() -> int:
        s = get_user_settings()
        return int(s.cache_ttl_hours * 3600)


_singleton: Optional[TVDBClient] = None


def get_client() -> TVDBClient:
    global _singleton
    if _singleton is None:
        _singleton = TVDBClient()
    return _singleton
