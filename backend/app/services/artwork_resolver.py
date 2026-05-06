"""Cross-provider artwork resolver (v0.5.8).

When the user sets ``preferred_artwork_source`` to something other than
``auto``, we look up the *other* provider's images and return a slot→URL
map that the builder treats as a higher-priority default than the
metadata provider's built-in picks. User-uploaded selections still
override everything.

This lets a TVDB-bound show use TMDB artwork (or vice versa) without
rebinding the show to the other metadata source.
"""
from __future__ import annotations

from typing import Optional

from loguru import logger

from ..config import (
    UserSettings,
    effective_tmdb_credentials,
    effective_tvdb_credentials,
)
from .artwork import (
    SEASON_POSTER,
    SERIES_BACKGROUND,
    SERIES_BANNER,
    SERIES_CLEARLOGO,
    SERIES_POSTER,
    MOVIE_BACKGROUND,
    MOVIE_BANNER,
    MOVIE_POSTER,
    absolutize_tvdb_url,
    best_artwork_url,
    list_candidates,
)
from .tmdb import get_client as get_tmdb_client, image_url as tmdb_image_url
from .tvdb import get_client as get_tvdb_client


async def resolve_preferred_artwork_series(
    *,
    settings: UserSettings,
    bound_provider: str,
    tvdb_data: Optional[dict] = None,
    tmdb_tv: Optional[dict] = None,
    local_season_numbers: Optional[list[int]] = None,
    prefer_languages: Optional[list[str]] = None,
    force: bool = False,
    manual_secondary_id: Optional[str] = None,
) -> dict[str, str]:
    """Return a slot→URL dict of artwork URLs that should win over the
    metadata provider's natural defaults.

    Only returns entries when ``preferred_artwork_source`` differs from the
    bound provider AND the preferred provider is reachable. An empty dict
    is returned for ``auto`` or when the preferred provider has no usable
    match / no creds.

    Parameters
    ----------
    bound_provider: "tvdb" | "tmdb" — the provider the show is bound to.
    tvdb_data: the series_extended payload (required when bound_provider="tvdb").
    tmdb_tv:   the tv_details payload (required when bound_provider="tmdb").
    local_season_numbers: restrict per-season poster lookups to these.
    """
    pref = (settings.preferred_artwork_source or "auto").lower()
    if pref == "auto" or pref == bound_provider:
        return {}
    out: dict[str, str] = {}
    langs = prefer_languages or [settings.preferred_language, *settings.fallback_languages]

    if pref == "tmdb":
        # Need a TMDB id. From TVDB, look at remoteIds.
        if not effective_tmdb_credentials():
            return {}
        tmdb_id: Optional[str] = manual_secondary_id or None
        if not tmdb_id and bound_provider == "tvdb" and isinstance(tvdb_data, dict):
            for rm in (tvdb_data.get("remoteIds") or []):
                if not isinstance(rm, dict):
                    continue
                src = (rm.get("sourceName") or "").lower()
                if "tmdb" in src or "moviedb" in src:
                    tmdb_id = str(rm.get("id") or "") or None
                    break
        if not tmdb_id:
            logger.debug("resolve_preferred_artwork_series: no TMDB id available for series")
            return {}
        try:
            tc = get_tmdb_client()
            imgs = await tc.tv_images(tmdb_id, force=force)
        except Exception as e:
            logger.debug("resolve_preferred_artwork_series: tv_images failed: {}", e)
            return {}
        poster = _first_tmdb_path(imgs.get("posters"))
        backdrop = _first_tmdb_path(imgs.get("backdrops"))
        logo = _first_tmdb_path(imgs.get("logos"))
        if poster:
            out["poster"] = tmdb_image_url(poster, "original") or ""
        if backdrop:
            out["background"] = tmdb_image_url(backdrop, "original") or ""
        if logo:
            out["clearlogo"] = tmdb_image_url(logo, "original") or ""
        # Per-season posters
        if local_season_numbers:
            for sn in sorted(set(int(n) for n in local_season_numbers if int(n) >= 0)):
                try:
                    simg = await tc.tv_season_images(tmdb_id, sn, force=force)
                except Exception:
                    continue
                fp = _first_tmdb_path(simg.get("posters"))
                if fp:
                    url = tmdb_image_url(fp, "original")
                    if url:
                        out[f"season-{sn:02d}-poster"] = url
        # Prune empties
        out = {k: v for k, v in out.items() if v}
        return out

    if pref == "tvdb":
        # Need a TVDB id. From TMDB, look at external_ids.
        api_key, _pin = effective_tvdb_credentials()
        if not api_key:
            return {}
        tvdb_id: Optional[str] = manual_secondary_id or None
        if not tvdb_id and bound_provider == "tmdb" and isinstance(tmdb_tv, dict):
            ext = tmdb_tv.get("external_ids") or {}
            tvdb_id = str(ext.get("tvdb_id") or "") or None
        if not tvdb_id:
            logger.debug("resolve_preferred_artwork_series: no TVDB id available for series")
            return {}
        try:
            client = get_tvdb_client()
            data = await client.series_extended(tvdb_id, force=force)
        except Exception as e:
            logger.debug("resolve_preferred_artwork_series: series_extended failed: {}", e)
            return {}
        artworks = (data or {}).get("artworks") or []
        p = best_artwork_url(artworks, SERIES_POSTER, langs)
        b = best_artwork_url(artworks, SERIES_BACKGROUND, langs)
        bn = best_artwork_url(artworks, SERIES_BANNER, langs)
        cl = best_artwork_url(artworks, SERIES_CLEARLOGO, langs)
        if p:
            out["poster"] = p
        if b:
            out["background"] = b
        if bn:
            out["banner"] = bn
        if cl:
            out["clearlogo"] = cl
        # Per-season posters
        for sn in sorted(set(int(n) for n in (local_season_numbers or []) if int(n) >= 0)):
            cands = list_candidates(
                artworks, SEASON_POSTER, langs,
                season_number=sn, series=data,
            )
            if cands:
                out[f"season-{sn:02d}-poster"] = cands[0]["url"]
        return out

    return {}


async def resolve_preferred_artwork_movie(
    *,
    settings: UserSettings,
    bound_provider: str,
    tvdb_data: Optional[dict] = None,
    tmdb_mv: Optional[dict] = None,
    prefer_languages: Optional[list[str]] = None,
    force: bool = False,
    manual_secondary_id: Optional[str] = None,
) -> dict[str, str]:
    pref = (settings.preferred_artwork_source or "auto").lower()
    if pref == "auto" or pref == bound_provider:
        return {}
    out: dict[str, str] = {}
    langs = prefer_languages or [settings.preferred_language, *settings.fallback_languages]

    if pref == "tmdb":
        if not effective_tmdb_credentials():
            return {}
        tmdb_id: Optional[str] = manual_secondary_id or None
        if not tmdb_id and bound_provider == "tvdb" and isinstance(tvdb_data, dict):
            for rm in (tvdb_data.get("remoteIds") or []):
                if not isinstance(rm, dict):
                    continue
                src = (rm.get("sourceName") or "").lower()
                if "tmdb" in src or "moviedb" in src:
                    tmdb_id = str(rm.get("id") or "") or None
                    break
        if not tmdb_id:
            return {}
        try:
            tc = get_tmdb_client()
            imgs = await tc.movie_images(tmdb_id, force=force)
        except Exception as e:
            logger.debug("resolve_preferred_artwork_movie: movie_images failed: {}", e)
            return {}
        poster = _first_tmdb_path(imgs.get("posters"))
        backdrop = _first_tmdb_path(imgs.get("backdrops"))
        logo = _first_tmdb_path(imgs.get("logos"))
        if poster:
            out["poster"] = tmdb_image_url(poster, "original") or ""
        if backdrop:
            out["background"] = tmdb_image_url(backdrop, "original") or ""
        if logo:
            out["clearlogo"] = tmdb_image_url(logo, "original") or ""
        return {k: v for k, v in out.items() if v}

    if pref == "tvdb":
        api_key, _pin = effective_tvdb_credentials()
        if not api_key:
            return {}
        tvdb_id: Optional[str] = manual_secondary_id or None
        if not tvdb_id and bound_provider == "tmdb" and isinstance(tmdb_mv, dict):
            ext = tmdb_mv.get("external_ids") or {}
            tvdb_id = str(ext.get("tvdb_id") or "") or None
        if not tvdb_id:
            return {}
        try:
            client = get_tvdb_client()
            data = await client.movie_extended(tvdb_id, force=force)
        except Exception as e:
            logger.debug("resolve_preferred_artwork_movie: movie_extended failed: {}", e)
            return {}
        artworks = (data or {}).get("artworks") or []
        p = best_artwork_url(artworks, MOVIE_POSTER, langs)
        b = best_artwork_url(artworks, MOVIE_BACKGROUND, langs)
        bn = best_artwork_url(artworks, MOVIE_BANNER, langs)
        if p:
            out["poster"] = p
        if b:
            out["background"] = b
        if bn:
            out["banner"] = bn
        return out

    return {}


def _first_tmdb_path(images: Optional[list]) -> Optional[str]:
    """Return the ``file_path`` of the first usable TMDB image entry."""
    if not isinstance(images, list):
        return None
    for im in images:
        if isinstance(im, dict) and im.get("file_path"):
            return im["file_path"]
    return None
