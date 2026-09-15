"""Cross-provider artwork resolver (v0.5.8).

The resolver returns per-slot provider picks that the builder treats as
higher-priority defaults than metadata-provider artwork. User-uploaded
selections still override everything.

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
    SERIES_CLEARLOGO,
    SERIES_POSTER,
    MOVIE_BACKGROUND,
    MOVIE_CLEARLOGO_TYPES,
    MOVIE_POSTER,
    best_artwork_url,
    list_candidates,
)
from .tmdb import (
    apply_tmdb_image_language_filter,
    get_client as get_tmdb_client,
    image_url as tmdb_image_url,
)
from .tvdb import get_client as get_tvdb_client


def _source(settings: UserSettings, slot: str) -> str:
    field = {
        "poster": "preferred_poster_source",
        "background": "preferred_background_source",
        "clearlogo": "preferred_clearlogo_source",
        "season": "preferred_season_source",
    }[slot]
    value = str(getattr(settings, field, "auto") or "auto").lower()
    return value if value in {"auto", "tvdb", "tmdb"} else "auto"


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
    """Return selected-provider artwork URLs for each configured series slot."""
    slot_sources = {
        "poster": _source(settings, "poster"),
        "background": _source(settings, "background"),
        "clearlogo": _source(settings, "clearlogo"),
        "season": _source(settings, "season"),
    }
    tmdb_clearlogo = slot_sources["clearlogo"] == "tmdb" or (
        slot_sources["clearlogo"] == "auto" and bound_provider == "tmdb"
    )
    out: dict[str, str] = {}
    langs = prefer_languages or [settings.preferred_language, *settings.fallback_languages]

    if ("tmdb" in slot_sources.values() or tmdb_clearlogo) and effective_tmdb_credentials():
        tmdb_id: Optional[str] = None
        if bound_provider == "tmdb" and isinstance(tmdb_tv, dict):
            tmdb_id = str(tmdb_tv.get("id") or "") or None
        elif bound_provider == "tvdb" and isinstance(tvdb_data, dict):
            tmdb_id = manual_secondary_id or None
        if not tmdb_id and bound_provider == "tvdb" and isinstance(tvdb_data, dict):
            for rm in (tvdb_data.get("remoteIds") or []):
                if not isinstance(rm, dict):
                    continue
                src = (rm.get("sourceName") or "").lower()
                if "tmdb" in src or "moviedb" in src:
                    tmdb_id = str(rm.get("id") or "") or None
                    break
        if tmdb_id:
            tmdb_languages: list[str] = []
            try:
                tc = get_tmdb_client()
                details = tmdb_tv if bound_provider == "tmdb" and isinstance(tmdb_tv, dict) else await tc.tv_details(tmdb_id, force=force)
                original_language = (details or {}).get("original_language")
                if isinstance(original_language, str) and original_language:
                    tmdb_languages.append(original_language)
                imgs = await tc.tv_images(tmdb_id, languages=tmdb_languages, force=force)
                for slot, key in (("poster", "posters"), ("background", "backdrops"), ("clearlogo", "logos")):
                    if slot_sources[slot] == "tmdb" or (slot == "clearlogo" and tmdb_clearlogo):
                        path = _first_tmdb_path(apply_tmdb_image_language_filter(imgs.get(key)))
                        if path and (url := tmdb_image_url(path, "original")):
                            out[slot] = url
                if slot_sources["season"] == "tmdb":
                    for sn in sorted({int(n) for n in local_season_numbers or [] if int(n) >= 0}):
                        try:
                            images = await tc.tv_season_images(tmdb_id, sn, languages=tmdb_languages, force=force)
                        except Exception:
                            continue
                        path = _first_tmdb_path(apply_tmdb_image_language_filter(images.get("posters")))
                        if path and (url := tmdb_image_url(path, "original")):
                            out[f"season-{sn:02d}-poster"] = url
            except Exception as error:
                logger.debug("resolve_preferred_artwork_series: TMDB lookup failed: {}", error)

    if "tvdb" in slot_sources.values() and effective_tvdb_credentials()[0]:
        tvdb_id: Optional[str] = None
        if bound_provider == "tvdb" and isinstance(tvdb_data, dict):
            tvdb_id = str(tvdb_data.get("id") or "") or None
            data = tvdb_data
        elif bound_provider == "tmdb" and isinstance(tmdb_tv, dict):
            tvdb_id = manual_secondary_id or None
            ext = tmdb_tv.get("external_ids") or {}
            tvdb_id = tvdb_id or str(ext.get("tvdb_id") or "") or None
            data = None
        else:
            data = None
        if tvdb_id:
            try:
                if data is None:
                    data = await get_tvdb_client().series_extended(tvdb_id, force=force)
                artworks = (data or {}).get("artworks") or []
                for slot, kind in (("poster", SERIES_POSTER), ("background", SERIES_BACKGROUND), ("clearlogo", SERIES_CLEARLOGO)):
                    if slot_sources[slot] == "tvdb" and (url := best_artwork_url(artworks, kind, langs)):
                        out[slot] = url
                if slot_sources["season"] == "tvdb":
                    for sn in sorted({int(n) for n in local_season_numbers or [] if int(n) >= 0}):
                        candidates = list_candidates(artworks, SEASON_POSTER, langs, season_number=sn, series=data)
                        if candidates:
                            out[f"season-{sn:02d}-poster"] = candidates[0]["url"]
            except Exception as error:
                logger.debug("resolve_preferred_artwork_series: TVDB lookup failed: {}", error)

    return out


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
    slot_sources = {slot: _source(settings, slot) for slot in ("poster", "background", "clearlogo")}
    tmdb_clearlogo = slot_sources["clearlogo"] == "tmdb" or (
        slot_sources["clearlogo"] == "auto" and bound_provider == "tmdb"
    )
    out: dict[str, str] = {}
    langs = prefer_languages or [settings.preferred_language, *settings.fallback_languages]

    if ("tmdb" in slot_sources.values() or tmdb_clearlogo) and effective_tmdb_credentials():
        tmdb_id = str(tmdb_mv.get("id") or "") if bound_provider == "tmdb" and isinstance(tmdb_mv, dict) else manual_secondary_id or None
        if not tmdb_id and bound_provider == "tvdb" and isinstance(tvdb_data, dict):
            for rm in (tvdb_data.get("remoteIds") or []):
                if not isinstance(rm, dict):
                    continue
                src = (rm.get("sourceName") or "").lower()
                if "tmdb" in src or "moviedb" in src:
                    tmdb_id = str(rm.get("id") or "") or None
                    break
        if tmdb_id:
            try:
                tc = get_tmdb_client()
                details = tmdb_mv if bound_provider == "tmdb" and isinstance(tmdb_mv, dict) else await tc.movie_details(tmdb_id, force=force)
                original_language = (details or {}).get("original_language")
                languages = [original_language] if isinstance(original_language, str) and original_language else []
                images = await tc.movie_images(tmdb_id, languages=languages, force=force)
                for slot, key in (("poster", "posters"), ("background", "backdrops"), ("clearlogo", "logos")):
                    if slot_sources[slot] == "tmdb" or (slot == "clearlogo" and tmdb_clearlogo):
                        path = _first_tmdb_path(apply_tmdb_image_language_filter(images.get(key)))
                        if path and (url := tmdb_image_url(path, "original")):
                            out[slot] = url
            except Exception as error:
                logger.debug("resolve_preferred_artwork_movie: TMDB lookup failed: {}", error)

    if "tvdb" in slot_sources.values() and effective_tvdb_credentials()[0]:
        tvdb_id: Optional[str] = None
        if bound_provider == "tvdb" and isinstance(tvdb_data, dict):
            tvdb_id = str(tvdb_data.get("id") or "") or None
            data = tvdb_data
        elif bound_provider == "tmdb" and isinstance(tmdb_mv, dict):
            tvdb_id = manual_secondary_id or None
            ext = tmdb_mv.get("external_ids") or {}
            tvdb_id = tvdb_id or str(ext.get("tvdb_id") or "") or None
            data = None
        else:
            data = None
        if tvdb_id:
            try:
                if data is None:
                    data = await get_tvdb_client().movie_extended(tvdb_id, force=force)
                artworks = (data or {}).get("artworks") or []
                for slot, kind in (("poster", MOVIE_POSTER), ("background", MOVIE_BACKGROUND)):
                    if slot_sources[slot] == "tvdb" and (url := best_artwork_url(artworks, kind, langs)):
                        out[slot] = url
                if slot_sources["clearlogo"] == "tvdb":
                    for kind in MOVIE_CLEARLOGO_TYPES:
                        if url := best_artwork_url(artworks, kind, langs):
                            out["clearlogo"] = url
                            break
            except Exception as error:
                logger.debug("resolve_preferred_artwork_movie: TVDB lookup failed: {}", error)

    return out


def _first_tmdb_path(images: Optional[list]) -> Optional[str]:
    """Return the ``file_path`` of the first usable TMDB image entry."""
    if not isinstance(images, list):
        return None
    for im in images:
        if isinstance(im, dict) and im.get("file_path"):
            return im["file_path"]
    return None
