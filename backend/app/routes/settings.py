"""Health and persisted application settings endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, ValidationError

from .. import __version__
from ..config import (
    MEDIA_ROOT, UserSettings, effective_fanart_credentials,
    effective_tmdb_credentials, effective_tvdb_credentials, effective_omdb_credentials,
    get_user_settings, save_user_settings,
)
from ..services.watcher import watcher as _watcher

router = APIRouter(prefix="/api")

# ---- Settings & health -----------------------------------------------------

@router.get("/health")
async def health():
    api_key, _ = effective_tvdb_credentials()
    s = get_user_settings()
    return {
        "ok": True,
        "version": __version__,
        "media_root": str(MEDIA_ROOT),
        "tvdb_configured": bool(api_key),
        "tmdb_configured": bool(effective_tmdb_credentials()),
        "fanart_configured": bool(effective_fanart_credentials()),
        "metadata_source": (s.metadata_source or "tvdb"),
        "plex_configured": bool(s.plex_url and s.plex_token),
        "plex_auto_refresh": bool(s.plex_auto_refresh),
    }


@router.get("/version")
async def version():
    """Return the running app version.

    Useful when you're pinning the Docker image to ``:latest`` and want the
    UI to surface exactly which release is currently in flight. Cheap call;
    safe to poll.
    """
    return {
        "version": __version__,
        "name": "plex-nfo-builder",
        "repo": "https://github.com/Cooper8386/plex-nfo-builder",
    }


@router.get("/settings")
async def get_settings():
    s = get_user_settings()
    payload = s.model_dump()
    # Never echo secret values back to the UI; surface a hint instead.
    for key in ("tvdb_api_key", "tvdb_pin", "tmdb_api_key", "omdb_api_key", "fanart_api_key", "plex_token"):
        had = bool(payload.get(key))
        payload.pop(key, None)
        payload[f"{key}_configured"] = had
    tvdb_key, tvdb_pin = effective_tvdb_credentials()
    payload.update(
        tvdb_api_key_configured=bool(tvdb_key),
        tvdb_pin_configured=bool(tvdb_pin),
        tmdb_api_key_configured=bool(effective_tmdb_credentials()),
        omdb_api_key_configured=bool(effective_omdb_credentials()),
        fanart_api_key_configured=bool(effective_fanart_credentials()),
    )
    return payload


class SettingsIn(BaseModel):
    preferred_language: Optional[str] = None
    fallback_languages: Optional[list[str]] = None
    include_original_title: Optional[bool] = None
    cache_ttl_hours: Optional[int] = None
    overwrite_foreign_nfo: Optional[bool] = None
    tvdb_api_key: Optional[str] = None
    tvdb_pin: Optional[str] = None
    auto_match_threshold: Optional[int] = None
    metadata_source: Optional[str] = None
    tmdb_api_key: Optional[str] = None
    omdb_api_key: Optional[str] = None
    fanart_api_key: Optional[str] = None
    fanart_enabled: Optional[bool] = None
    tmdb_artwork_enabled: Optional[bool] = None
    preferred_artwork_source: Optional[str] = None
    # v0.6.0 Plex integration
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None
    plex_auto_refresh: Optional[bool] = None
    plex_refresh_delay_seconds: Optional[int] = None
    plex_path_mappings: Optional[list[dict[str, str]]] = None
    # v0.11.10 orphan-companion sweeper
    auto_sweep_orphans: Optional[bool] = None
    # v0.11.12 artwork language filtering (per provider)
    tvdb_artwork_languages: Optional[list[str]] = None
    tvdb_artwork_allow_null_language: Optional[bool] = None
    tmdb_artwork_languages: Optional[list[str]] = None
    tmdb_artwork_allow_null_language: Optional[bool] = None
    # v0.12.0 filesystem watcher
    watcher_enabled: Optional[bool] = None
    watcher_debounce_seconds: Optional[int] = None


@router.post("/settings")
async def update_settings(payload: SettingsIn):
    s = get_user_settings()
    data = s.model_dump()
    for k, v in payload.model_dump(exclude_unset=True).items():
        # Treat empty-string secret fields as 'leave unchanged' rather than wiping.
        if k in ("tvdb_api_key", "tvdb_pin", "tmdb_api_key", "omdb_api_key", "fanart_api_key", "plex_token") and v == "":
            continue
        if k == "plex_path_mappings" and v is not None:
            cleaned = []
            for m in v:
                if not isinstance(m, dict):
                    continue
                src = (m.get("from") or "").strip()
                dst = (m.get("to") or "").strip()
                if not src and not dst:
                    continue
                cleaned.append({"from": src, "to": dst})
            v = cleaned
        if k == "plex_url" and isinstance(v, str):
            v = v.strip().rstrip("/") or None
        if k == "plex_refresh_delay_seconds" and v is not None:
            try:
                v = max(0, min(600, int(v)))
            except (TypeError, ValueError):
                v = 5
        if k in ("tvdb_artwork_languages", "tmdb_artwork_languages") and v is not None:
            # Normalise to a deduped, lowercase list of non-empty codes.
            seen: set[str] = set()
            cleaned_codes: list[str] = []
            for code in v if isinstance(v, list) else []:
                if not isinstance(code, str):
                    continue
                c = code.strip().lower()
                if not c or c in seen:
                    continue
                seen.add(c)
                cleaned_codes.append(c)
            v = cleaned_codes
        data[k] = v
    if data.get("metadata_source") not in ("tvdb", "tmdb"):
        data["metadata_source"] = "tvdb"
    if data.get("preferred_artwork_source") not in ("auto", "tvdb", "tmdb"):
        data["preferred_artwork_source"] = "auto"
    try:
        new = UserSettings(**data)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors(include_input=False, include_context=False)) from error
    save_user_settings(new)
    # v0.12.0: if anything touched the watcher knobs, ask the watcher to
    # re-evaluate immediately so the user doesn't have to restart the app.
    try:
        touched = payload.model_dump(exclude_unset=True)
        if "watcher_enabled" in touched or "watcher_debounce_seconds" in touched:
            _watcher.reload()
    except Exception as e:
        logger.warning("watcher reload after settings update failed: {}", e)
    return {"ok": True}
