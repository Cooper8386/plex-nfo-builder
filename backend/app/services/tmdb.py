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

# Tiny ISO 639-3 → ISO 639-1 map for the languages we plausibly need to
# pass to TMDB's image endpoints. Anything we don't recognise we leave
# alone — if it's already 2-letter the call still works.
_ISO_639_3_TO_1: dict[str, str] = {
    "eng": "en",
    "jpn": "ja",
    "kor": "ko",
    "zho": "zh",
    "chi": "zh",
    "spa": "es",
    "fra": "fr",
    "fre": "fr",
    "deu": "de",
    "ger": "de",
    "ita": "it",
    "por": "pt",
    "rus": "ru",
    "ara": "ar",
    "hin": "hi",
    "tha": "th",
    "vie": "vi",
    "tur": "tr",
    "pol": "pl",
    "nld": "nl",
    "dut": "nl",
    "swe": "sv",
    "nor": "no",
    "dan": "da",
    "fin": "fi",
    "ces": "cs",
    "cze": "cs",
    "ell": "el",
    "gre": "el",
    "heb": "he",
    "hun": "hu",
    "ind": "id",
    "may": "ms",
    "msa": "ms",
    "ron": "ro",
    "rum": "ro",
    "ukr": "uk",
    "tgl": "tl",
    "fil": "tl",
}


class TMDBError(RuntimeError):
    pass


def tmdb_artwork_language_filter() -> tuple[Optional[set[str]], bool]:
    """Return the user's TMDB artwork language whitelist.

    Mirrors :func:`backend.app.services.artwork._tvdb_language_filter`
    but reads the TMDB-side fields. Returns ``(allowed_codes, allow_null)``
    where ``allowed_codes`` is ``None`` when no whitelist is configured.
    Codes are lowercase 2-letter ISO 639-1.
    """
    try:
        s = get_user_settings()
    except Exception:
        return None, True
    langs = getattr(s, "tmdb_artwork_languages", None) or []
    allow_null = bool(getattr(s, "tmdb_artwork_allow_null_language", True))
    if not langs:
        return None, allow_null
    out: set[str] = set()
    for x in langs:
        if not x:
            continue
        code = str(x).strip().lower()
        if not code:
            continue
        # Be tolerant of users pasting 3-letter codes — map down where we know.
        code = _ISO_639_3_TO_1.get(code, code)
        out.add(code)
    return out or None, allow_null


def apply_tmdb_image_language_filter(images: Optional[list]) -> list:
    """Drop TMDB image entries whose ``iso_639_1`` is disallowed by Settings.

    Falls back to the unfiltered list when every entry is rejected so the
    builder still has something to pick from. Used as a post-filter on
    each ``posters`` / ``backdrops`` / ``logos`` array returned by
    ``tv_images`` / ``movie_images`` / ``tv_season_images``.
    """
    if not isinstance(images, list) or not images:
        return list(images or [])
    allowed, allow_null = tmdb_artwork_language_filter()
    if allowed is None and allow_null:
        return list(images)
    kept: list = []
    for im in images:
        if not isinstance(im, dict):
            continue
        raw = im.get("iso_639_1")
        lang = (raw or "").strip().lower() if isinstance(raw, str) else ""
        if not lang:
            if allow_null:
                kept.append(im)
            continue
        if allowed is None or lang in allowed:
            kept.append(im)
    if not kept:
        # Don't strand the builder — unfiltered fallback wins over no art.
        return [im for im in images if isinstance(im, dict)]
    return kept


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

    # ---- Images -------------------------------------------------------------
    #
    # v0.11.6: TMDB pre-filters /images results by ``include_image_language``.
    # If you don't pass the show's *original* language, anything tagged with
    # that language (which is most of the uploaded posters for non-English
    # shows — anime, K-dramas, foreign films) gets dropped on the server
    # side and the API never returns them, even though they're public on
    # themoviedb.org.
    #
    # The fix is to always ask for: ``null`` (no-flag), ``en``, and the
    # show's original language. Callers that have the language at hand pass
    # it via ``languages=``; callers that don't can still call without it
    # (the default ``null,en`` is preserved for back-compat). The flag
    # ``include_all_languages=True`` switches to ``null`` only, which TMDB
    # treats as "give me everything regardless of language tag" — useful
    # for the manual artwork picker where the user is browsing and
    # explicitly choosing.

    @staticmethod
    def _images_lang_param(
        languages: Optional[list[str]] = None,
        *,
        include_all: bool = False,
    ) -> Optional[str]:
        """Build the ``include_image_language`` query value.

        Returns ``None`` when the caller wants TMDB to return *all*
        public images regardless of language flag — in that case the
        helper above omits ``include_image_language`` from the query
        string entirely, which is TMDB's documented "no language filter"
        behaviour. This is what the manual artwork picker uses so a
        non-English show's natively-tagged posters (e.g. ``ja`` on
        Japanese anime) appear alongside ``null`` and ``en`` ones.

        Otherwise the value always includes ``null`` (untagged uploads)
        plus ``en`` plus any languages requested by the caller — the show's
        ``original_language`` in particular, so anime / foreign-language
        shows are auto-resolved correctly without any user action.
        """
        if include_all:
            return None
        wanted: list[str] = ["null", "en"]
        if languages:
            for lang in languages:
                if not lang:
                    continue
                code = lang.strip().lower()
                # TMDB image flags are 2-letter ISO 639-1; map our 3-letter
                # config values down. ``null`` and unknown tags are dropped.
                code = _ISO_639_3_TO_1.get(code, code)
                if not code or code == "null":
                    continue
                if code not in wanted:
                    wanted.append(code)
        return ",".join(wanted)

    async def tv_images(
        self,
        tv_id: int | str,
        *,
        languages: Optional[list[str]] = None,
        include_all_languages: bool = False,
        force: bool = False,
    ) -> dict:
        params: dict[str, Any] = {}
        lang = self._images_lang_param(languages, include_all=include_all_languages)
        if lang is not None:
            params["include_image_language"] = lang
        return await self._get(
            f"/tv/{tv_id}/images", params=params, ttl=self._ttl(), force=force
        )

    async def tv_season_images(
        self,
        tv_id: int | str,
        season: int,
        *,
        languages: Optional[list[str]] = None,
        include_all_languages: bool = False,
        force: bool = False,
    ) -> dict:
        params: dict[str, Any] = {}
        lang = self._images_lang_param(languages, include_all=include_all_languages)
        if lang is not None:
            params["include_image_language"] = lang
        return await self._get(
            f"/tv/{tv_id}/season/{season}/images",
            params=params, ttl=self._ttl(), force=force,
        )

    async def tv_episode_images(
        self,
        tv_id: int | str,
        season: int,
        episode: int,
        *,
        languages: Optional[list[str]] = None,
        include_all_languages: bool = True,
        force: bool = False,
    ) -> dict:
        """Fetch every uploaded still for a single episode.

        TMDB tags most stills with ``null`` (no language) but localized
        promotional stills do exist. ``include_all_languages`` defaults
        to True here because the picker is an explicit user choice — we
        want every option visible.
        """
        params: dict[str, Any] = {}
        lang = self._images_lang_param(languages, include_all=include_all_languages)
        if lang is not None:
            params["include_image_language"] = lang
        return await self._get(
            f"/tv/{tv_id}/season/{season}/episode/{episode}/images",
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

    async def movie_images(
        self,
        movie_id: int | str,
        *,
        languages: Optional[list[str]] = None,
        include_all_languages: bool = False,
        force: bool = False,
    ) -> dict:
        params: dict[str, Any] = {}
        lang = self._images_lang_param(languages, include_all=include_all_languages)
        if lang is not None:
            params["include_image_language"] = lang
        return await self._get(
            f"/movie/{movie_id}/images", params=params, ttl=self._ttl(), force=force
        )

    async def languages(self, *, force: bool = False) -> list[dict]:
        """Return TMDB's catalogue of supported languages.

        Each entry is ``{iso_639_1, english_name, name}`` — ``iso_639_1``
        is the 2-letter code TMDB tags images with (also what we pass in
        ``include_image_language``). Cached for a week.
        """
        try:
            data = await self._get(
                "/configuration/languages", params=None,
                ttl=7 * 24 * 3600, force=force,
            )
        except TMDBError as e:
            logger.debug("TMDB /configuration/languages failed: {}", e)
            return []
        # This endpoint returns a bare list at the top level.
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            return data["results"]
        return []

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
