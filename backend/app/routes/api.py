"""HTTP API for the frontend."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from .. import __version__
from .. import db
from ..config import (
    CUSTOM_ARTWORK_DIR,
    LOG_DIR,
    MEDIA_ROOT,
    UserSettings,
    effective_fanart_credentials,
    effective_metadata_source,
    effective_tmdb_credentials,
    effective_tvdb_credentials,
    get_user_settings,
    save_user_settings,
)
from ..services import artwork as artwork_svc
from ..services import builder as build_svc
from ..services import cleaner as cleaner_svc
from ..services import fanart as fanart_svc
from ..services import matcher
from ..services import plex as plex_svc
from ..services import scanner
from ..services import renamer as renamer_svc
from ..services import sidecar as sidecar_svc
from ..services.scheduler import cron_matches as _cron_matches, scheduler as _scheduler
from ..services.parser import (
    detect_season_dirs,
    folder_looks_like_movie,
    list_season_episodes,
    parse_folder_name,
    season_number_from_dir,
)
from ..services.tmdb import get_client as get_tmdb_client, image_url as tmdb_image_url
from ..services.tvdb import get_client

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
    for key in ("tvdb_api_key", "tvdb_pin", "tmdb_api_key", "fanart_api_key", "plex_token"):
        had = bool(payload.get(key))
        payload.pop(key, None)
        payload[f"{key}_configured"] = had
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
    fanart_api_key: Optional[str] = None
    fanart_enabled: Optional[bool] = None
    tmdb_artwork_enabled: Optional[bool] = None
    preferred_artwork_source: Optional[str] = None
    # v0.6.0 Plex integration
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None
    plex_auto_refresh: Optional[bool] = None
    plex_refresh_delay_seconds: Optional[int] = None
    plex_path_mappings: Optional[list[dict]] = None
    # v0.10.0 file rename templates
    rename_episode_template: Optional[str] = None
    rename_movie_template: Optional[str] = None
    rename_enabled: Optional[bool] = None
    # v0.11.0 Sonarr/Radarr-compatible rename templates
    rename_daily_template: Optional[str] = None
    rename_anime_template: Optional[str] = None
    rename_series_folder_template: Optional[str] = None
    rename_season_folder_template: Optional[str] = None
    rename_movie_folder_template: Optional[str] = None


@router.post("/settings")
async def update_settings(payload: SettingsIn):
    s = get_user_settings()
    data = s.model_dump()
    for k, v in payload.model_dump(exclude_unset=True).items():
        # Treat empty-string secret fields as 'leave unchanged' rather than wiping.
        if k in ("tvdb_api_key", "tvdb_pin", "tmdb_api_key", "fanart_api_key", "plex_token") and v == "":
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
        data[k] = v
    if data.get("metadata_source") not in ("tvdb", "tmdb"):
        data["metadata_source"] = "tvdb"
    if data.get("preferred_artwork_source") not in ("auto", "tvdb", "tmdb"):
        data["preferred_artwork_source"] = "auto"
    new = UserSettings(**data)
    save_user_settings(new)
    return {"ok": True}


# ---- Browse ----------------------------------------------------------------

def _safe_under_root(path: str, must_exist: bool = True) -> Path:
    p = Path(path).resolve()
    root = MEDIA_ROOT.resolve()
    if not (p == root or root in p.parents):
        raise HTTPException(status_code=400, detail="Path outside MEDIA_ROOT")
    if must_exist and not p.exists():
        # Endpoints that read live files still raise; the new "forget" endpoints
        # opt out via must_exist=False.
        pass
    return p


@router.get("/browse")
async def browse(path: Optional[str] = None):
    p = _safe_under_root(path) if path else MEDIA_ROOT
    if not p.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    items = []
    if p.is_dir():
        for f in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if f.name.startswith("."):
                continue
            items.append({
                "name": f.name,
                "path": str(f),
                "is_dir": f.is_dir(),
                "size": f.stat().st_size if f.is_file() else None,
            })
    return {"path": str(p), "parent": str(p.parent) if p != MEDIA_ROOT else None, "items": items}


# ---- Libraries -------------------------------------------------------------

@router.post("/libraries/detect")
async def libraries_detect():
    libs = scanner.detect_libraries()
    return {"libraries": libs}


@router.get("/libraries")
async def libraries_list():
    libs: list[dict] = []
    for r in db.list_libraries():
        d = dict(r)
        # Surface the resolved source so the UI can show "default → TVDB" etc.
        d["effective_metadata_source"] = effective_metadata_source(d.get("name"))
        libs.append(d)
    return {"libraries": libs}


class LibraryUpdate(BaseModel):
    kind: Optional[str] = None
    enabled: Optional[bool] = None
    # v0.7.0: per-library metadata source override.
    # Pass "tvdb" or "tmdb" to override the global setting; pass "", "default",
    # or null to clear and inherit the global value.
    metadata_source: Optional[str] = None


@router.post("/libraries/{name}")
async def libraries_update(name: str, body: LibraryUpdate):
    payload = body.model_dump(exclude_unset=True)
    if "kind" in payload and payload["kind"]:
        db.set_library_kind(name, payload["kind"])
    if "enabled" in payload and payload["enabled"] is not None:
        db.set_library_enabled(name, bool(payload["enabled"]))
    if "metadata_source" in payload:
        db.set_library_metadata_source(name, payload["metadata_source"])
    row = db.get_library(name)
    return {
        "ok": True,
        "library": dict(row) if row else None,
        "effective_metadata_source": effective_metadata_source(name),
    }


@router.delete("/libraries/{name}")
async def libraries_delete(name: str):
    """Forget a library and every database row that belongs to it.

    Files on disk are never touched. Sidecar files survive, so re-detecting
    + re-scanning later restores everything from disk.
    """
    if not db.get_library(name):
        raise HTTPException(status_code=404, detail="library not found")
    summary = db.delete_library(name)
    return {"ok": True, **summary}


@router.post("/libraries/{name}/scan")
async def library_scan(name: str, background: BackgroundTasks):
    def _run():
        try:
            scanner.scan_library(name)
        except Exception as e:
            logger.exception("Library scan failed: {}", e)
    background.add_task(_run)
    return {"ok": True, "scheduled": True}


# ---- Items -----------------------------------------------------------------

@router.get("/items")
async def items_list(library: Optional[str] = None,
                     status: Optional[str] = None,
                     q: Optional[str] = None,
                     hide_organized: bool = False):
    """List items in a library.

    v0.11.4: the UI ships a 3-way "All / Needs work / Complete" pill that
    toggles ``status`` and ``hide_organized``. ``status`` accepts a
    comma-separated list of NFO state values (``none``, ``partial``,
    ``stale``, ``foreign``, ``mixed``, ``complete``) and is the canonical
    filter; ``hide_organized`` is the legacy boolean equivalent of
    ``status=none,partial,stale,foreign,mixed`` and remains for backwards
    compatibility.
    """
    statuses = status.split(",") if status else None
    rows = db.list_item_state(library=library, statuses=statuses, title_q=q)
    out = []
    for r in rows:
        d = dict(r)
        if hide_organized and d.get("nfo_status") == "complete":
            continue
        out.append(d)
    return {"items": out}


# ---- Custom tags (v0.8.0) --------------------------------------------------

class TagIn(BaseModel):
    folder_path: str
    tag: str


@router.post("/items/tags")
async def items_add_tag(payload: TagIn):
    p = _safe_under_root(payload.folder_path)
    inserted = db.add_custom_tag(str(p), payload.tag)
    # Persist to sidecar so the tag survives a wipe/restore.
    try:
        sidecar_svc.write_sidecar(p)
    except Exception:
        pass
    return {"ok": True, "added": inserted, "tags": db.list_custom_tags(str(p))}


@router.delete("/items/tags")
async def items_remove_tag(folder_path: str, tag: str):
    p = _safe_under_root(folder_path)
    removed = db.remove_custom_tag(str(p), tag)
    try:
        sidecar_svc.write_sidecar(p)
    except Exception:
        pass
    return {"ok": True, "removed": removed, "tags": db.list_custom_tags(str(p))}


class ItemRemoveIn(BaseModel):
    folder_path: str


class ItemCleanIn(BaseModel):
    folder_path: str
    dry_run: bool = False
    keep_sidecar: bool = True
    rescan: bool = True


@router.post("/items/clean")
async def items_clean(payload: ItemCleanIn):
    """Wipe generated NFOs and artwork from a folder.

    Leaves season folders and media files alone. The .plex-nfo-builder.json
    sidecar is preserved by default so the binding + overrides survive; pass
    `keep_sidecar=False` to delete it too.

    With `dry_run=true`, returns the list of files that would be deleted
    without modifying anything.
    """
    p = _safe_under_root(payload.folder_path)
    if payload.dry_run:
        return {"ok": True, "dry_run": True, "files": cleaner_svc.preview_clean(p)}
    summary = cleaner_svc.clean_folder(p, keep_sidecar=payload.keep_sidecar)
    if payload.rescan:
        # Refresh item_state immediately so the UI reflects the wipe.
        try:
            row = db.list_item_state()
            kind = next(
                (r["kind"] for r in row if r["folder_path"] == str(p)),
                None,
            )
            library = next(
                (r["library"] for r in row if r["folder_path"] == str(p)),
                "",
            )
            if kind == "movie":
                scanner.scan_movie_folder(p, library=library or "")
            else:
                scanner.scan_series_folder(p, library=library or "")
        except Exception as e:
            logger.warning("post-clean rescan failed for {}: {}", p, e)
    return {"ok": True, **summary}


@router.post("/items/remove")
async def items_remove(payload: ItemRemoveIn):
    """Forget an item from the database.

    Removes the row from item_state plus all related bindings, artwork
    selections, episode overrides, and legacy active_artwork entries. Files on
    disk are never touched. Use this for folders you have already deleted on
    disk so they stop appearing in the library.
    """
    p = _safe_under_root(payload.folder_path, must_exist=False)
    n = db.delete_item_state(str(p))
    return {"ok": True, "removed": n}


class ItemsPruneIn(BaseModel):
    library: Optional[str] = None
    dry_run: bool = False


@router.post("/items/prune")
async def items_prune(payload: ItemsPruneIn):
    """Find every tracked folder whose path no longer exists on disk and forget it.

    Pass `dry_run=true` to preview which folders would be removed.
    """
    rows = db.list_item_state(library=payload.library)
    missing: list[dict] = []
    for r in rows:
        d = dict(r)
        fp = d.get("folder_path")
        if not fp:
            continue
        if not Path(fp).exists():
            missing.append({
                "folder_path": fp,
                "library": d.get("library"),
                "title": d.get("title"),
            })
    removed = 0
    if not payload.dry_run:
        for m in missing:
            removed += db.delete_item_state(m["folder_path"])
    return {
        "ok": True,
        "checked": len(rows),
        "missing": len(missing),
        "removed": removed,
        "items": missing,
    }


class ItemsPruneEmptyIn(BaseModel):
    """Payload for /items/prune-empty.

    ``library`` scopes the scan; ``dry_run=True`` returns the candidate list
    without modifying anything; ``delete_files`` (off by default) wipes the
    generated NFOs + artwork from disk in addition to forgetting the row,
    leaving an empty folder behind for the user to remove.
    """
    library: Optional[str] = None
    dry_run: bool = False
    delete_files: bool = False


@router.post("/items/prune-empty")
async def items_prune_empty(payload: ItemsPruneEmptyIn):
    """Forget tracked folders that exist on disk but contain no media files.

    The classic case: a show folder with ``tvshow.nfo`` + posters but no
    actual video files (Plex skips it, the user wants it gone). Movies
    with the same shape are also caught.

    Safety:
      * Folders that are missing on disk are skipped entirely — use the
        ordinary ``/items/prune`` endpoint for those.
      * Every candidate is re-checked with :func:`folder_has_media`
        immediately before deletion so a video that landed between the
        dry-run preview and the user's confirmation cannot be pruned.
      * Errors / permission problems treat the folder as "has media" and
        leave it alone.
      * Files on disk are only touched when ``delete_files=True``; even
        then, the cleaner only removes recognised generated files (NFOs,
        posters, season-poster JPGs, thumbs) and never touches anything
        that looks like a video, audio, or subtitle file.
    """
    rows = db.list_item_state(library=payload.library)
    candidates: list[dict] = []
    for r in rows:
        d = dict(r)
        fp = d.get("folder_path")
        if not fp:
            continue
        p = Path(fp)
        if not p.exists() or not p.is_dir():
            continue  # /items/prune handles missing folders
        if scanner.folder_has_media(p):
            continue
        candidates.append({
            "folder_path": fp,
            "library": d.get("library"),
            "title": d.get("title"),
            "kind": d.get("kind"),
        })

    if payload.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "checked": len(rows),
            "candidates": len(candidates),
            "items": candidates,
        }

    removed = 0
    files_deleted = 0
    skipped: list[dict] = []
    for c in candidates:
        p = Path(c["folder_path"])
        # Re-check on the live filesystem right before deletion. Belt and
        # braces: a download could have landed between the dry-run preview
        # and the user pressing OK.
        if not p.exists() or not p.is_dir():
            skipped.append({**c, "reason": "folder no longer exists"})
            continue
        if scanner.folder_has_media(p):
            skipped.append({**c, "reason": "folder now contains media \u2014 not pruned"})
            continue
        if payload.delete_files:
            try:
                summary = cleaner_svc.clean_folder(p, keep_sidecar=False)
                files_deleted += int(summary.get("nfo_deleted", 0)) + int(
                    summary.get("artwork_deleted", 0)
                ) + int(summary.get("sidecar_deleted", 0))
            except Exception as e:
                logger.warning("prune-empty: clean_folder failed for {}: {}", p, e)
        removed += db.delete_item_state(c["folder_path"])
    return {
        "ok": True,
        "checked": len(rows),
        "candidates": len(candidates),
        "removed": removed,
        "files_deleted": files_deleted,
        "skipped": skipped,
        "items": candidates,
    }


# ---- Library-wide DANGER ZONE ----------------------------------------------
#
# These two endpoints power the "big yellow buttons" on the Library view.
# They iterate every folder tracked under a given library and either wipe
# generated NFOs/artwork (clean_folder) or delete the .plex-nfo-builder.json
# sidecar files. Both support dry-run mode so the UI can show a preview
# before the user confirms.


class LibraryWipeIn(BaseModel):
    library: str
    dry_run: bool = False
    keep_sidecar: bool = True       # ignored when wiping sidecars
    rescan: bool = True


@router.post("/libraries/{name}/wipe-nfo")
async def library_wipe_nfo(name: str, payload: LibraryWipeIn):
    """Wipe generated NFOs and artwork from EVERY tracked folder in ``name``.

    For each folder this is the same operation as `/items/clean`. Sidecar
    files are preserved by default so bindings + overrides survive. Pass
    ``dry_run=true`` to get a preview without touching disk.
    """
    if payload.library != name:
        raise HTTPException(400, "library mismatch")
    rows = [dict(r) for r in db.list_item_state(library=name)]
    folders: list[Path] = []
    for r in rows:
        fp = r.get("folder_path")
        if not fp:
            continue
        try:
            p = _safe_under_root(fp)
        except Exception:
            continue
        if p.is_dir():
            folders.append(p)

    if payload.dry_run:
        preview: list[dict] = []
        total = 0
        for p in folders:
            files = cleaner_svc.preview_clean(p)
            total += len(files)
            if files:
                preview.append({
                    "folder_path": str(p),
                    "file_count": len(files),
                    "files": files[:25],  # cap per-folder preview
                })
        return {
            "ok": True,
            "dry_run": True,
            "library": name,
            "folder_count": len(folders),
            "file_count": total,
            "folders": preview,
        }

    nfo_total = 0
    artwork_total = 0
    sidecar_total = 0
    cleaned_folders: list[dict] = []
    failed: list[dict] = []
    for p in folders:
        try:
            summary = cleaner_svc.clean_folder(p, keep_sidecar=payload.keep_sidecar)
            nfo_total += summary.get("nfo_deleted", 0)
            artwork_total += summary.get("artwork_deleted", 0)
            sidecar_total += summary.get("sidecar_deleted", 0)
            cleaned_folders.append({
                "folder_path": str(p),
                **{k: summary.get(k, 0) for k in ("nfo_deleted", "artwork_deleted", "sidecar_deleted")},
            })
        except Exception as e:  # noqa: BLE001
            failed.append({"folder_path": str(p), "reason": str(e)})
            logger.warning("library wipe-nfo: clean failed for {}: {}", p, e)
            continue
        if payload.rescan:
            try:
                kind = next(
                    (r.get("kind") for r in rows if r.get("folder_path") == str(p)),
                    None,
                )
                lib = next(
                    (r.get("library") for r in rows if r.get("folder_path") == str(p)),
                    name,
                )
                if kind == "movie":
                    scanner.scan_movie_folder(p, library=lib or name)
                else:
                    scanner.scan_series_folder(p, library=lib or name)
            except Exception as e:  # noqa: BLE001
                logger.warning("library wipe-nfo: rescan failed for {}: {}", p, e)

    return {
        "ok": True,
        "library": name,
        "folder_count": len(folders),
        "nfo_deleted": nfo_total,
        "artwork_deleted": artwork_total,
        "sidecar_deleted": sidecar_total,
        "folders": cleaned_folders,
        "failed": failed,
    }


@router.post("/libraries/{name}/wipe-sidecars")
async def library_wipe_sidecars(name: str, payload: LibraryWipeIn):
    """Delete every ``.plex-nfo-builder.json`` sidecar in ``name``.

    The sidecar is the only on-disk record of bindings + overrides, so this
    is destructive: after running, scanning the library re-discovers folders
    but they come back unmatched. Use this when sidecars from a previous
    install have gone bad and you want to start clean from the database.
    NFOs and artwork are NOT touched - run wipe-nfo for that.
    """
    if payload.library != name:
        raise HTTPException(400, "library mismatch")
    rows = [dict(r) for r in db.list_item_state(library=name)]
    targets: list[Path] = []
    for r in rows:
        fp = r.get("folder_path")
        if not fp:
            continue
        try:
            p = _safe_under_root(fp)
        except Exception:
            continue
        sidecar = p / sidecar_svc.SIDECAR_NAME
        if sidecar.is_file():
            targets.append(sidecar)

    if payload.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "library": name,
            "sidecar_count": len(targets),
            "files": [str(t) for t in targets],
        }

    deleted: list[str] = []
    failed: list[dict] = []
    for t in targets:
        try:
            t.unlink()
            deleted.append(str(t))
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            failed.append({"path": str(t), "reason": str(e)})
            logger.warning("library wipe-sidecars: delete failed for {}: {}", t, e)
    return {
        "ok": True,
        "library": name,
        "sidecar_count": len(targets),
        "deleted": deleted,
        "failed": failed,
    }


@router.get("/items/detail")
async def item_detail(path: str):
    p = _safe_under_root(path)
    binding = db.get_binding(str(p))
    state_rows = db.list_item_state()
    state = next((dict(r) for r in state_rows if r["folder_path"] == str(p)), None)
    overrides = db.get_nfo_overrides(str(p))
    # List the canonical artwork files Plex expects in the folder root.
    canonical_files = []
    for name in ("poster.jpg", "background.jpg", "banner.jpg", "clearlogo.png"):
        if (p / name).exists():
            canonical_files.append(str(p / name))
    for child in sorted(p.glob("Season*-poster.jpg")):
        canonical_files.append(str(child))

    # Provider-aware matched-episode count (best effort — never fails the request).
    provider_episode_count: Optional[int] = None
    provider_used: Optional[str] = None
    if binding and binding["kind"] == "series":
        try:
            settings = get_user_settings()
            lang = binding["language"] or settings.preferred_language
            provider = (binding["provider"] or "tvdb").lower()
            provider_used = provider
            # Build the set of (season, episode) pairs that exist locally.
            local_keys: set[tuple[int, int]] = set()
            for sd in detect_season_dirs(p):
                snum = season_number_from_dir(sd.name)
                for parsed in list_season_episodes(sd):
                    local_keys.add((int(snum), int(parsed.episode)))
            if not local_keys:
                provider_episode_count = 0
            elif provider == "tmdb":
                tmdb_c = get_tmdb_client()
                seasons_needed = sorted({s for s, _ in local_keys})
                matched = 0
                for snum in seasons_needed:
                    try:
                        sdata = await tmdb_c.tv_season(binding["external_id"], snum, language=lang)
                    except Exception as e:
                        logger.debug("item_detail tmdb tv_season {}/{}: {}",
                                     binding["external_id"], snum, e)
                        continue
                    nums = {
                        int(ep["episode_number"])
                        for ep in (sdata.get("episodes") or [])
                        if ep.get("episode_number") is not None
                    }
                    for ls, le in local_keys:
                        if ls == snum and le in nums:
                            matched += 1
                provider_episode_count = matched
            else:
                # TVDB
                tvdb_c = get_client()
                episodes = await tvdb_c.series_episodes(
                    binding["external_id"], season_type="default", language=lang
                )
                overrides_ep = db.get_episode_overrides(str(p))
                tvdb_keys: set[tuple[int, int]] = set()
                for ep in episodes:
                    sn = ep.get("seasonNumber")
                    en = ep.get("number")
                    if sn is None or en is None:
                        continue
                    tvdb_keys.add((int(sn), int(en)))
                tvdb_ids = {
                    str(ep["id"]) for ep in episodes if ep.get("id") is not None
                }
                matched = 0
                for key in local_keys:
                    override_id = overrides_ep.get(key)
                    if override_id and str(override_id) in tvdb_ids:
                        matched += 1
                    elif key in tvdb_keys:
                        matched += 1
                provider_episode_count = matched
        except Exception as e:
            logger.debug("item_detail provider episode count failed for {}: {}", p, e)

    # v0.8.0: tags from each metadata source plus user-added custom tags.
    tags_payload = await _gather_tags_for_detail(p, binding)

    # v0.11.8: surface the parent library's declared kind so the front-end can
    # default the manual-match dropdown correctly. A folder that was scanned
    # before any video was downloaded — or one that scanner.py mis-bucketed as
    # a movie because a single trailer file lives at the root — would
    # otherwise show "Movie" in the Match panel inside a TV library.
    library_kind: Optional[str] = None
    try:
        lib_name = p.parent.name
        lib_row = db.get_library(lib_name)
        if lib_row and lib_row["kind"]:
            library_kind = lib_row["kind"]
    except Exception:
        library_kind = None

    return {
        "path": str(p),
        "binding": dict(binding) if binding else None,
        "state": state,
        "artwork_files": canonical_files,
        "overrides": overrides,
        "provider_episode_count": provider_episode_count,
        "provider_used": provider_used,
        "tags": tags_payload,
        "library_kind": library_kind,
    }


@router.get("/items/nfo-explain")
async def items_nfo_explain(path: str):
    """Return a structured \"why does this folder have this status?\" payload.

    The library list only carries the bucketed status (none/partial/foreign/
    mixed/stale/complete) which doesn't tell the user *which* file is
    missing or what to do about it. The Detail page calls this on demand to
    render a per-season breakdown of NFO coverage.
    """
    p = _safe_under_root(path)
    binding = db.get_binding(str(p))
    # Default to series unless we know it's a movie (binding wins; otherwise
    # the folder shape decides). The explainer is tolerant of either.
    if binding and binding["kind"] == "movie":
        kind = "movie"
    elif binding and binding["kind"] == "series":
        kind = "series"
    else:
        kind = "movie" if scanner.folder_looks_like_movie(p) else "series"
    payload = scanner.explain_nfo_state(p, kind=kind)
    payload["path"] = str(p)
    return payload


async def _gather_tags_for_detail(folder: Path, binding) -> dict:
    """Collect TVDB genres, TMDB keywords, and custom tags for a folder.

    Best-effort: any provider that fails returns an empty list so the UI keeps
    rendering. The detail endpoint must never fail because tags can't be fetched.
    """
    out = {"tvdb": [], "tmdb": [], "custom": db.list_custom_tags(str(folder))}
    if not binding:
        return out
    kind = binding["kind"]
    provider = (binding["provider"] or "").lower()
    external_id = binding["external_id"]
    settings = get_user_settings()
    lang = binding["language"] or settings.preferred_language

    # ---- TVDB genres ------------------------------------------------------
    if provider == "tvdb":
        try:
            tvdb_c = get_client()
            if kind == "series":
                data = await tvdb_c.series_extended(external_id, force=False)
            else:
                data = await tvdb_c.movie_extended(external_id, force=False)
            out["tvdb"] = [
                g.get("name") for g in (data.get("genres") or [])
                if isinstance(g, dict) and g.get("name")
            ]
        except Exception as e:
            logger.debug("item_detail tvdb tags fetch failed for {}: {}", folder, e)

    # ---- TMDB keywords ----------------------------------------------------
    if provider == "tmdb":
        tmdb_id = external_id
    else:
        # If bound to TVDB, see if we can resolve a TMDB id via remoteIds for tag display.
        tmdb_id = None
        try:
            tvdb_c = get_client()
            if kind == "series":
                data = await tvdb_c.series_extended(external_id, force=False)
            else:
                data = await tvdb_c.movie_extended(external_id, force=False)
            for rm in data.get("remoteIds") or []:
                if not isinstance(rm, dict):
                    continue
                src = (rm.get("sourceName") or "").lower()
                if "tmdb" in src or "moviedb" in src or "movie database" in src:
                    tmdb_id = str(rm.get("id") or "") or None
                    break
        except Exception:
            tmdb_id = None
    if tmdb_id and effective_tmdb_credentials():
        try:
            tmdb_c = get_tmdb_client()
            if kind == "series":
                kw = await tmdb_c.tv_keywords(tmdb_id)
                names = [
                    k.get("name") for k in (kw.get("results") or [])
                    if isinstance(k, dict) and k.get("name")
                ]
            else:
                kw = await tmdb_c.movie_keywords(tmdb_id)
                names = [
                    k.get("name") for k in (kw.get("keywords") or [])
                    if isinstance(k, dict) and k.get("name")
                ]
            out["tmdb"] = names
            # If the item is bound to TMDB its `genres` array is also worth
            # surfacing as the canonical "tags" — prepend them ahead of keywords.
            if provider == "tmdb":
                try:
                    if kind == "series":
                        det = await tmdb_c.tv_details(tmdb_id, language=lang)
                    else:
                        det = await tmdb_c.movie_details(tmdb_id, language=lang)
                    genres = [
                        g.get("name") for g in (det.get("genres") or [])
                        if isinstance(g, dict) and g.get("name")
                    ]
                    seen = {(n or "").lower() for n in names}
                    merged = [g for g in genres if g and g.lower() not in seen] + names
                    out["tmdb"] = merged
                except Exception as e:
                    logger.debug("item_detail tmdb genres fetch failed for {}: {}", folder, e)
        except Exception as e:
            logger.debug("item_detail tmdb keywords fetch failed for {}: {}", folder, e)
    return out


# ---- Matching --------------------------------------------------------------

@router.get("/match/search")
async def match_search(q: str, type: str = "series", year: Optional[int] = None,
                        language: Optional[str] = None,
                        provider: Optional[str] = None,
                        library: Optional[str] = None):
    return {
        "results": await matcher.manual_search(q, type_=type, year=year, language=language,
                                                provider=provider, library=library),
        "provider": provider or effective_metadata_source(library),
    }


class BindIn(BaseModel):
    folder_path: str
    kind: str  # series | movie
    provider: str = "tvdb"
    external_id: str
    title: Optional[str] = None
    year: Optional[int] = None
    language: Optional[str] = None
    # When true (default for manual bindings) the binding is locked so
    # auto-match cannot silently replace it later.
    lock_source: Optional[bool] = True


@router.post("/match/bind")
async def match_bind(payload: BindIn):
    p = _safe_under_root(payload.folder_path)
    if payload.provider not in ("tvdb", "tmdb"):
        raise HTTPException(status_code=400, detail="provider must be 'tvdb' or 'tmdb'")
    db.upsert_binding(str(p), payload.kind, payload.provider, payload.external_id,
                      title=payload.title, year=payload.year, language=payload.language,
                      source_locked=bool(payload.lock_source))
    # Refresh item_state so the library view reflects the new binding immediately.
    try:
        if payload.kind == "movie":
            scanner.scan_movie_folder(p, library=p.parent.name)
        else:
            scanner.scan_series_folder(p, library=p.parent.name)
    except Exception as e:
        logger.warning("post-bind rescan of {} failed: {}", p, e)
    try:
        sidecar_svc.write_sidecar(p)
    except Exception as e:
        logger.warning("sidecar after bind {}: {}", p, e)
    return {"ok": True}


class SourceIn(BaseModel):
    folder_path: str
    provider: str             # 'tvdb' | 'tmdb'
    external_id: Optional[str] = None
    locked: bool = True
    kind: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None


@router.post("/match/source")
async def match_set_source(payload: SourceIn):
    """Switch the metadata provider for a single folder, optionally locking it.

    If `external_id` is omitted, the existing binding's id is reused (so the
    user can simply toggle the lock without re-searching).
    """
    if payload.provider not in ("tvdb", "tmdb"):
        raise HTTPException(status_code=400, detail="provider must be 'tvdb' or 'tmdb'")
    p = _safe_under_root(payload.folder_path)
    existing = db.get_binding(str(p))
    eid = payload.external_id or (existing["external_id"] if existing else None)
    if not eid:
        raise HTTPException(
            status_code=400,
            detail="external_id required (no existing binding to reuse)",
        )
    db.set_binding_provider(
        str(p), payload.provider, str(eid),
        locked=bool(payload.locked),
        kind=payload.kind,
        title=payload.title,
        year=payload.year,
    )
    try:
        kind = (payload.kind or (existing["kind"] if existing else "series"))
        if kind == "movie":
            scanner.scan_movie_folder(p, library=p.parent.name)
        else:
            scanner.scan_series_folder(p, library=p.parent.name)
    except Exception as e:
        logger.warning("post-source rescan failed for {}: {}", p, e)
    try:
        sidecar_svc.write_sidecar(p)
    except Exception as e:
        logger.warning("sidecar after source change {}: {}", p, e)
    return {"ok": True}


class SecondaryIn(BaseModel):
    folder_path: str
    # Pass provider+external_id to set; pass both as null/empty to clear.
    provider: Optional[str] = None    # 'tvdb' | 'tmdb'
    external_id: Optional[str] = None


@router.post("/match/secondary")
async def match_set_secondary(payload: SecondaryIn):
    """Attach (or clear) a manual secondary provider id on a folder.

    Use this when you've matched a folder to one provider (e.g. TVDB) but
    that record doesn't cross-reference the *other* provider. Setting a
    secondary id lets the artwork resolver pull cross-provider images, the
    fanart.tv resolver use the right key, and the NFO writer emit a second
    ``<uniqueid type="...">`` row — even when the providers don't link
    each other.
    """
    p = _safe_under_root(payload.folder_path)
    binding = db.get_binding(str(p))
    if not binding:
        raise HTTPException(
            status_code=400,
            detail="Folder must be matched to a primary provider before setting a secondary id",
        )
    prov_in = (payload.provider or "").strip().lower() or None
    eid_in = (payload.external_id or "").strip() or None
    if prov_in and prov_in not in ("tvdb", "tmdb"):
        raise HTTPException(status_code=400, detail="provider must be 'tvdb' or 'tmdb'")
    if prov_in and prov_in == (binding["provider"] or "").lower():
        raise HTTPException(
            status_code=400,
            detail="secondary provider must differ from the primary binding",
        )
    if prov_in and not eid_in:
        raise HTTPException(status_code=400, detail="external_id is required when provider is set")
    try:
        db.set_binding_secondary(str(p), prov_in, eid_in)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        sidecar_svc.write_sidecar(p)
    except Exception as e:
        logger.warning("sidecar after secondary id change {}: {}", p, e)
    return {"ok": True, "secondary_provider": prov_in, "secondary_external_id": eid_in}


@router.post("/match/unbind")
async def match_unbind(folder_path: str):
    p = _safe_under_root(folder_path)
    db.delete_binding(str(p))
    try:
        sidecar_svc.write_sidecar(p)
    except Exception as e:
        logger.warning("sidecar after unbind {}: {}", p, e)
    return {"ok": True}


# ---- NFO field overrides (v0.5.3) ------------------------------------------

_ALLOWED_OVR_FIELDS = {"title", "sorttitle", "plot", "tagline", "originaltitle"}
_OVR_SCOPE_RE = re.compile(r"^(series|movie|season-\d{2}|episode-[A-Za-z0-9_\-]+)$")


@router.get("/overrides")
async def overrides_get(path: str):
    p = _safe_under_root(path)
    return {"path": str(p), "overrides": db.get_nfo_overrides(str(p))}


class OverrideIn(BaseModel):
    folder_path: str
    scope: str               # 'series' | 'movie' | 'season-NN' | 'episode-<id>'
    field: str               # 'title' | 'sorttitle' | 'plot' | 'tagline' | 'originaltitle'
    value: Optional[str] = None  # null/"" clears


@router.post("/overrides")
async def overrides_set(payload: OverrideIn):
    p = _safe_under_root(payload.folder_path)
    if payload.field not in _ALLOWED_OVR_FIELDS:
        raise HTTPException(status_code=400, detail=f"field must be one of {sorted(_ALLOWED_OVR_FIELDS)}")
    if not _OVR_SCOPE_RE.match(payload.scope or ""):
        raise HTTPException(status_code=400, detail="invalid scope")
    db.set_nfo_override(str(p), payload.scope, payload.field, payload.value)
    # v0.11.4: keep item_state.sort_title in sync when the user edits the
    # series/movie sorttitle override. Without this the library list keeps
    # using the previous order until the next scan.
    if payload.field == "sorttitle" and payload.scope in ("series", "movie"):
        try:
            row = db.conn().execute(
                "SELECT title FROM item_state WHERE folder_path = ?", (str(p),)
            ).fetchone()
            if row:
                ovr_value = payload.value if (payload.value or "").strip() else None
                db.upsert_item_state(
                    str(p),
                    sort_title=db.compute_sort_title(row["title"], ovr_value),
                )
        except Exception as e:
            logger.warning("sort_title refresh after override {}: {}", p, e)
    try:
        sidecar_svc.write_sidecar(p)
    except Exception as e:
        logger.warning("sidecar after override {}: {}", p, e)
    return {"ok": True}


class OverrideClearIn(BaseModel):
    folder_path: str
    scope: Optional[str] = None
    field: Optional[str] = None


@router.post("/overrides/clear")
async def overrides_clear(payload: OverrideClearIn):
    p = _safe_under_root(payload.folder_path)
    n = db.clear_nfo_override(str(p), scope=payload.scope, field=payload.field)
    # v0.11.4: if the cleared override was a sorttitle, fall back to the
    # auto-derived sort title so the library list re-orders right away.
    cleared_sort = payload.field in (None, "sorttitle") and payload.scope in (
        None, "series", "movie",
    )
    if cleared_sort:
        try:
            row = db.conn().execute(
                "SELECT title FROM item_state WHERE folder_path = ?", (str(p),)
            ).fetchone()
            if row:
                db.upsert_item_state(
                    str(p),
                    sort_title=db.compute_sort_title(row["title"], None),
                )
        except Exception as e:
            logger.warning("sort_title refresh after override clear {}: {}", p, e)
    try:
        sidecar_svc.write_sidecar(p)
    except Exception as e:
        logger.warning("sidecar after clear {}: {}", p, e)
    return {"ok": True, "cleared": n}


# ---- Build -----------------------------------------------------------------

class BuildIn(BaseModel):
    folder_path: str
    kind: Optional[str] = None  # series | movie (autodetected if omitted)
    force: bool = False
    language: Optional[str] = None


def _detect_kind(p: Path) -> str:
    """Decide whether a folder is a series or a movie.

    v0.9.0: per-folder content trumps the library declaration. Anime
    libraries commonly mix Radarr movies and Sonarr series; we shouldn't
    mis-route a movie folder as a series just because the library was
    classified as TV (or vice versa).
    """
    try:
        if detect_season_dirs(p):
            return "series"
        if folder_looks_like_movie(p):
            return "movie"
    except Exception:
        pass
    lib_name = p.parent.name
    rows = db.list_libraries()
    lib_kind = next((r["kind"] for r in rows if r["name"] == lib_name), "tv")
    return "movie" if lib_kind == "movies" else "series"


@router.post("/build")
async def build_endpoint(payload: BuildIn):
    p = _safe_under_root(payload.folder_path)
    kind = payload.kind or _detect_kind(p)
    job_id = build_svc.start_build(p, kind, force=payload.force, language=payload.language)
    return {"ok": True, "job": job_id}


# ---- Bulk ------------------------------------------------------------------

class BulkIn(BaseModel):
    folder_paths: Optional[list[str]] = None
    library: Optional[str] = None
    only_unmatched: bool = False
    only_unbuilt: bool = False
    force: bool = False
    language: Optional[str] = None


def _filter_locked(paths: list[Path]) -> list[Path]:
    """Drop folders whose binding is source_locked=1 (auto-match must skip them)."""
    out: list[Path] = []
    for p in paths:
        b = db.get_binding(str(p))
        if b and int(b["source_locked"] or 0) == 1:
            logger.info("auto-match skipping {} (source_locked)", p)
            continue
        out.append(p)
    return out


def _resolve_bulk_paths(payload: BulkIn, *, default_only_unmatched: bool = False) -> list[Path]:
    """Resolve the list of folders for a bulk action.

    v0.8.0: when caller passes only ``library`` (no folder_paths) the auto-match
    flow now defaults to processing every item in the library so "Auto-match
    all" works whether or not items are already bound. To opt back into the
    legacy unmatched-only behaviour pass ``only_unmatched=true``.
    """
    paths: list[Path] = []
    if payload.folder_paths:
        for fp in payload.folder_paths:
            paths.append(_safe_under_root(fp))
    elif payload.library:
        rows = db.list_item_state(library=payload.library)
        for r in rows:
            d = dict(r)
            if payload.only_unmatched and d.get("external_id"):
                continue
            if payload.only_unbuilt and d.get("nfo_status") == "complete":
                continue
            try:
                paths.append(_safe_under_root(d["folder_path"]))
            except HTTPException:
                continue
    else:
        raise HTTPException(status_code=400, detail="Provide folder_paths or library")
    return paths


@router.post("/match/auto-bulk")
async def match_auto_bulk(payload: BulkIn):
    paths = _filter_locked(_resolve_bulk_paths(payload))
    settings = get_user_settings()
    lang = payload.language or settings.preferred_language

    async def _run_one(p: Path) -> dict:
        kind = _detect_kind(p)
        # v0.7.0: pick the metadata source per the folder's library, falling
        # back to the global setting when the library has no override.
        source = effective_metadata_source(p.parent.name)
        try:
            if source == "tmdb":
                if kind == "series":
                    data = await matcher.auto_match_series_tmdb(
                        p, language=lang, threshold=settings.auto_match_threshold)
                else:
                    data = await matcher.auto_match_movie_tmdb(
                        p, language=lang, threshold=settings.auto_match_threshold)
            else:
                if kind == "series":
                    data = await matcher.auto_match_series(
                        p, language=lang, threshold=settings.auto_match_threshold)
                else:
                    data = await matcher.auto_match_movie(
                        p, language=lang, threshold=settings.auto_match_threshold)
            # After a successful match the binding is written by the matcher.
            # Re-scan the folder so item_state.external_id / provider / nfo_status
            # reflect the new binding and the UI stops showing "unmatched".
            if data:
                try:
                    if kind == "series":
                        scanner.scan_series_folder(p, library=p.parent.name)
                    else:
                        scanner.scan_movie_folder(p, library=p.parent.name)
                except Exception as se:
                    logger.warning("post-match rescan of {} failed: {}", p, se)
                try:
                    sidecar_svc.write_sidecar(p)
                except Exception as se:
                    logger.warning("sidecar after auto-match {}: {}", p, se)
            name = data.get("name") or data.get("title") if data else None
            return {
                "folder_path": str(p), "kind": kind,
                "matched": bool(data),
                "provider": source if data else None,
                "external_id": str(data.get("id")) if data else None,
                "title": name,
            }
        except Exception as e:
            logger.exception("auto-match {} failed: {}", p, e)
            return {"folder_path": str(p), "kind": kind, "matched": False, "error": str(e)}

    # Limit concurrency so we do not pummel TVDB.
    sem = asyncio.Semaphore(4)

    async def _bounded(p: Path) -> dict:
        async with sem:
            return await _run_one(p)

    results = await asyncio.gather(*[_bounded(p) for p in paths])
    matched = sum(1 for r in results if r.get("matched"))
    return {"ok": True, "total": len(results), "matched": matched, "results": results}


@router.post("/build/bulk")
async def build_bulk(payload: BulkIn):
    paths = _resolve_bulk_paths(payload)
    jobs: list[dict] = []
    for p in paths:
        kind = _detect_kind(p)
        jid = build_svc.start_build(p, kind, force=payload.force, language=payload.language)
        jobs.append({"folder_path": str(p), "kind": kind, "job": jid})
    return {"ok": True, "queued": len(jobs), "jobs": jobs}


@router.get("/jobs")
async def jobs_list():
    return {"jobs": build_svc.list_jobs()}


@router.get("/jobs/{job_id}")
async def jobs_get(job_id: str):
    j = build_svc.get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Job not found")
    return j


@router.get("/jobs/{job_id}/log")
async def jobs_log(job_id: str):
    p = LOG_DIR / "jobs" / f"{job_id}.log"
    if not p.exists():
        raise HTTPException(status_code=404, detail="No log")
    return FileResponse(p, media_type="text/plain")


# ---- Artwork ---------------------------------------------------------------

@router.get("/artwork/file")
async def artwork_file(path: str):
    p = _safe_under_root(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(p)


# ---- Artwork picker (v0.4.0) -----------------------------------------------

def _tag_provider(items: list[dict], provider: str) -> list[dict]:
    out = []
    for it in items:
        d = dict(it)
        d.setdefault("provider", provider)
        out.append(d)
    return out


def _custom_candidates(folder_path: str) -> list[dict]:
    rows = db.list_custom_artwork(folder_path)
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        if d["source"] == "upload":
            url = f"/api/artwork/custom/{d['id']}"
        else:
            url = d["file_path"]  # remote URL
        out.append({
            "id": d["id"],
            "url": url,
            "thumb": url,
            "language": None,
            "score": 0,
            "type": None,
            "seasonNumber": None,
            "provider": "custom",
            "origin": d.get("origin"),
            "slot": d.get("slot"),
            "source": d["source"],
        })
    return out


@router.get("/artwork/candidates")
async def artwork_candidates(path: str, kind: str = "series"):
    """Aggregate artwork candidates across TVDB, TMDB, fanart.tv, and the user's
    own custom uploads/URLs, grouped by slot.

    Each candidate carries a `provider` field so the UI can filter.
    """
    p = _safe_under_root(path)
    binding = db.get_binding(str(p))
    if not binding:
        raise HTTPException(status_code=400, detail="Folder is not bound to a metadata entity yet")
    settings = get_user_settings()
    prefer = [settings.preferred_language, *settings.fallback_languages]
    selections = db.get_artwork_selections(str(p))
    slots: dict[str, list[dict]] = {}

    def _extend(slot: str, items: list[dict]) -> None:
        if not items:
            return
        slots.setdefault(slot, []).extend(items)

    tvdb_id_for_fanart: Optional[str] = None
    tmdb_id_for_fanart: Optional[str] = None
    imdb_id_for_fanart: Optional[str] = None

    # ---- Primary provider --------------------------------------------------
    if binding["provider"] == "tvdb":
        client = get_client()
        tvdb_id_for_fanart = str(binding["external_id"])
        if kind == "movie":
            data = await client.movie_extended(binding["external_id"])
            artworks = data.get("artworks") or []
            _extend("poster", _tag_provider(artwork_svc.list_candidates(artworks, artwork_svc.MOVIE_POSTER, prefer), "tvdb"))
            _extend("background", _tag_provider(artwork_svc.list_candidates(artworks, artwork_svc.MOVIE_BACKGROUND, prefer), "tvdb"))
            _extend("banner", _tag_provider(artwork_svc.list_candidates(artworks, artwork_svc.MOVIE_BANNER, prefer), "tvdb"))
            for rm in (data.get("remoteIds") or []):
                if isinstance(rm, dict):
                    src = (rm.get("sourceName") or "").lower()
                    if "tmdb" in src or "moviedb" in src:
                        tmdb_id_for_fanart = str(rm.get("id") or "")
                    elif "imdb" in src:
                        imdb_id_for_fanart = str(rm.get("id") or "")
        else:
            data = await client.series_extended(binding["external_id"])
            artworks = data.get("artworks") or []
            _extend("poster", _tag_provider(artwork_svc.list_candidates(artworks, artwork_svc.SERIES_POSTER, prefer), "tvdb"))
            _extend("background", _tag_provider(artwork_svc.list_candidates(artworks, artwork_svc.SERIES_BACKGROUND, prefer), "tvdb"))
            _extend("banner", _tag_provider(artwork_svc.list_candidates(artworks, artwork_svc.SERIES_BANNER, prefer), "tvdb"))
            _extend("clearlogo", _tag_provider(artwork_svc.list_candidates(artworks, artwork_svc.SERIES_CLEARLOGO, prefer), "tvdb"))
            for rm in (data.get("remoteIds") or []):
                if isinstance(rm, dict):
                    src = (rm.get("sourceName") or "").lower()
                    if "tmdb" in src or "moviedb" in src:
                        tmdb_id_for_fanart = str(rm.get("id") or "")
                    elif "imdb" in src:
                        imdb_id_for_fanart = str(rm.get("id") or "")
            seasons = data.get("seasons") or []
            season_numbers: list[int] = []
            for s in seasons:
                if isinstance(s, dict) and s.get("number") is not None:
                    try:
                        season_numbers.append(int(s["number"]))
                    except Exception:
                        pass
            for sn in sorted(set(n for n in season_numbers if n >= 0)):
                cands = artwork_svc.list_candidates(
                    artworks, artwork_svc.SEASON_POSTER, prefer,
                    season_number=sn, series=data,
                )
                if cands:
                    _extend(f"season-{sn:02d}-poster", _tag_provider(cands, "tvdb"))
    else:  # tmdb-bound
        # v0.11.6: this is the *manual* artwork picker — the user is
        # browsing every uploaded image to choose one. Pass
        # include_all_languages=True so TMDB returns posters tagged with
        # any language, not just null/en. Without this, anime / K-drama /
        # foreign-language shows show no posters at all even though
        # themoviedb.org has dozens.
        if kind == "movie":
            tmdb_id_for_fanart = str(binding["external_id"])
            try:
                tc = get_tmdb_client()
                imgs = await tc.movie_images(
                    binding["external_id"], include_all_languages=True
                )
                details = await tc.movie_details(binding["external_id"])
                if isinstance(details, dict) and details.get("imdb_id"):
                    imdb_id_for_fanart = details["imdb_id"]
                _extend("poster", _tag_provider(_tmdb_to_candidates(imgs.get("posters") or []), "tmdb"))
                _extend("background", _tag_provider(_tmdb_to_candidates(imgs.get("backdrops") or []), "tmdb"))
                _extend("clearlogo", _tag_provider(_tmdb_to_candidates(imgs.get("logos") or []), "tmdb"))
            except Exception as e:
                logger.warning("TMDB images failed: {}", e)
        else:
            try:
                tc = get_tmdb_client()
                details = await tc.tv_details(binding["external_id"])
                imgs = await tc.tv_images(
                    binding["external_id"], include_all_languages=True
                )
                ext = details.get("external_ids") or {}
                tvdb_id_for_fanart = str(ext.get("tvdb_id") or "") or None
                imdb_id_for_fanart = str(ext.get("imdb_id") or "") or None
                _extend("poster", _tag_provider(_tmdb_to_candidates(imgs.get("posters") or []), "tmdb"))
                _extend("background", _tag_provider(_tmdb_to_candidates(imgs.get("backdrops") or []), "tmdb"))
                _extend("clearlogo", _tag_provider(_tmdb_to_candidates(imgs.get("logos") or []), "tmdb"))
                seasons = details.get("seasons") or []
                for s in seasons:
                    if not isinstance(s, dict):
                        continue
                    sn = s.get("season_number")
                    if sn is None or int(sn) < 0:
                        continue
                    try:
                        season_imgs = await tc.tv_season_images(
                            binding["external_id"], int(sn),
                            include_all_languages=True,
                        )
                        cands = _tmdb_to_candidates(season_imgs.get("posters") or [], season_number=int(sn))
                        if cands:
                            _extend(f"season-{int(sn):02d}-poster", _tag_provider(cands, "tmdb"))
                    except Exception:
                        continue
            except Exception as e:
                logger.warning("TMDB tv images failed: {}", e)

    # v0.11.3: a manual secondary id on the binding wins over whatever the
    # primary provider's record happened to cross-reference (or fills the gap
    # when nothing was cross-referenced at all). The user has explicitly told
    # us "this folder is also that record", so trust it.
    sec_p = (binding["secondary_provider"] or "").lower() if "secondary_provider" in binding.keys() else ""
    sec_eid = binding["secondary_external_id"] if "secondary_external_id" in binding.keys() else None
    if sec_p == "tmdb" and sec_eid:
        tmdb_id_for_fanart = str(sec_eid)
    elif sec_p == "tvdb" and sec_eid:
        tvdb_id_for_fanart = str(sec_eid)

    # ---- TMDB as supplement when TVDB is primary ---------------------------
    if (binding["provider"] == "tvdb" and settings.tmdb_artwork_enabled
            and effective_tmdb_credentials() and tmdb_id_for_fanart):
        try:
            tc = get_tmdb_client()
            if kind == "movie":
                # Manual picker — show every uploaded poster regardless of
                # language flag (v0.11.6).
                imgs = await tc.movie_images(
                    tmdb_id_for_fanart, include_all_languages=True
                )
                _extend("poster", _tag_provider(_tmdb_to_candidates(imgs.get("posters") or []), "tmdb"))
                _extend("background", _tag_provider(_tmdb_to_candidates(imgs.get("backdrops") or []), "tmdb"))
                _extend("clearlogo", _tag_provider(_tmdb_to_candidates(imgs.get("logos") or []), "tmdb"))
            else:
                imgs = await tc.tv_images(
                    tmdb_id_for_fanart, include_all_languages=True
                )
                _extend("poster", _tag_provider(_tmdb_to_candidates(imgs.get("posters") or []), "tmdb"))
                _extend("background", _tag_provider(_tmdb_to_candidates(imgs.get("backdrops") or []), "tmdb"))
                _extend("clearlogo", _tag_provider(_tmdb_to_candidates(imgs.get("logos") or []), "tmdb"))
                # Per-season posters from TMDB (supplement TVDB primary)
                try:
                    tmdb_details = await tc.tv_details(tmdb_id_for_fanart)
                    tmdb_seasons = tmdb_details.get("seasons") or []
                except Exception as e:
                    logger.debug("TMDB tv_details for season posters failed: {}", e)
                    tmdb_seasons = []
                for s in tmdb_seasons:
                    if not isinstance(s, dict):
                        continue
                    sn = s.get("season_number")
                    if sn is None:
                        continue
                    try:
                        sn_int = int(sn)
                    except Exception:
                        continue
                    if sn_int < 0:
                        continue
                    try:
                        season_imgs = await tc.tv_season_images(
                            tmdb_id_for_fanart, sn_int,
                            include_all_languages=True,
                        )
                        cands = _tmdb_to_candidates(season_imgs.get("posters") or [], season_number=sn_int)
                        if cands:
                            _extend(f"season-{sn_int:02d}-poster", _tag_provider(cands, "tmdb"))
                    except Exception as e:
                        logger.debug("TMDB season {} images failed: {}", sn_int, e)
                        continue
        except Exception as e:
            logger.debug("TMDB supplement failed: {}", e)

    # ---- fanart.tv ---------------------------------------------------------
    if settings.fanart_enabled and effective_fanart_credentials():
        try:
            fc = fanart_svc.get_client()
            if kind == "movie":
                ident = tmdb_id_for_fanart or imdb_id_for_fanart
                if ident:
                    fa = await fc.movie(ident)
                    norm = fanart_svc.normalise_movie_artwork(fa)
                    for slot, items in norm.items():
                        _extend(slot, items)
            else:
                if tvdb_id_for_fanart:
                    fa = await fc.series(tvdb_id_for_fanart)
                    norm = fanart_svc.normalise_series_artwork(fa)
                    for slot, items in norm.items():
                        _extend(slot, items)
        except Exception as e:
            logger.debug("fanart.tv lookup failed: {}", e)

    # ---- Custom (user uploads + URLs) -------------------------------------
    custom = _custom_candidates(str(p))
    if custom:
        # Custom candidates apply to every slot the user might want — duplicate
        # them into the standard slots and into per-season-poster slots when
        # the user tagged a slot.
        all_slot_names = set(slots.keys()) | {"poster", "background", "banner", "clearlogo", "clearart"}
        for c in custom:
            target_slots: list[str]
            if c.get("slot"):
                target_slots = [c["slot"]]
            else:
                target_slots = list(all_slot_names)
            for s in target_slots:
                _extend(s, [c])

    # Sort each slot: prefer-language first, then score, with provider as a
    # mild secondary preference. The ranking honours the user's
    # `preferred_artwork_source` setting so manual picker views match what
    # the build pipeline will write to disk by default.
    pref_source = (settings.preferred_artwork_source or "auto").lower()
    if pref_source == "tmdb":
        provider_rank = {"custom": 0, "tmdb": 1, "tvdb": 2, "fanart": 3}
    elif pref_source == "tvdb":
        provider_rank = {"custom": 0, "tvdb": 1, "tmdb": 2, "fanart": 3}
    else:
        # "auto": tie TVDB and TMDB; the binding-primary provider already
        # comes first in the slot since it was extended first.
        provider_rank = {"custom": 0, "tvdb": 1, "tmdb": 1, "fanart": 2}
    for slot, items in slots.items():
        items.sort(key=lambda c: (
            provider_rank.get(c.get("provider", ""), 9),
            -(c.get("score") or 0),
            (0 if (c.get("language") in (settings.preferred_language, *settings.fallback_languages)) else 1),
        ))

    return {
        "path": str(p),
        "kind": kind,
        "slots": slots,
        "selections": selections,
        "binding_provider": binding["provider"],
    }


def _tmdb_to_candidates(images: list[dict], season_number: Optional[int] = None) -> list[dict]:
    out: list[dict] = []
    for im in images or []:
        if not isinstance(im, dict):
            continue
        fp = im.get("file_path")
        if not fp:
            continue
        url = tmdb_image_url(fp, "original")
        thumb = tmdb_image_url(fp, "w342")
        out.append({
            "id": None,
            "url": url,
            "thumb": thumb or url,
            "language": im.get("iso_639_1") or None,
            "score": int(round((im.get("vote_average") or 0) * 10)),
            "type": None,
            "seasonNumber": season_number,
            "provider": "tmdb",
        })
    return out


class ArtworkSelectIn(BaseModel):
    folder_path: str
    slot: str
    url: str
    language: Optional[str] = None
    score: Optional[int] = None


@router.post("/artwork/select")
async def artwork_select(payload: ArtworkSelectIn):
    p = _safe_under_root(payload.folder_path)
    db.set_artwork_selection(str(p), payload.slot, payload.url,
                             language=payload.language, score=payload.score)
    return {"ok": True}


class ArtworkClearIn(BaseModel):
    folder_path: str
    slot: Optional[str] = None


@router.post("/artwork/clear")
async def artwork_clear(payload: ArtworkClearIn):
    p = _safe_under_root(payload.folder_path)
    n = db.clear_artwork_selection(str(p), slot=payload.slot)
    return {"ok": True, "cleared": n}


# ---- Custom artwork (uploads + remote URLs) --------------------------------

_ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}
_ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp", "image/tiff",
}


def _ext_from_content_type(ct: Optional[str]) -> str:
    m = {
        "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
        "image/webp": ".webp", "image/gif": ".gif", "image/bmp": ".bmp",
        "image/tiff": ".tiff",
    }
    return m.get((ct or "").lower(), ".jpg")


@router.post("/artwork/upload")
async def artwork_upload(
    folder_path: str = Form(...),
    slot: Optional[str] = Form(None),
    file: UploadFile = File(...),
):
    """Upload an image file as custom artwork for the given folder."""
    p = _safe_under_root(folder_path)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(raw) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 50 MB)")
    ct = (file.content_type or "").lower()
    name = (file.filename or "upload").strip()
    ext = Path(name).suffix.lower()
    if ct and ct not in _ALLOWED_IMAGE_TYPES and ext not in _ALLOWED_IMAGE_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {ct or ext}")
    if not ext or ext not in _ALLOWED_IMAGE_EXTS:
        ext = _ext_from_content_type(ct)
    art_id = hashlib.sha1(raw + str(p).encode("utf-8")).hexdigest()
    CUSTOM_ARTWORK_DIR.mkdir(parents=True, exist_ok=True)
    dest = CUSTOM_ARTWORK_DIR / f"{art_id}{ext}"
    try:
        dest.write_bytes(raw)
    except Exception as e:
        logger.exception("Failed to write custom artwork: {}", e)
        raise HTTPException(status_code=500, detail="Failed to save upload")
    db.add_custom_artwork(
        art_id,
        folder_path=str(p),
        slot=slot or None,
        source="upload",
        origin=name,
        file_path=str(dest),
        content_type=ct or None,
        size=len(raw),
    )
    return {
        "ok": True,
        "id": art_id,
        "url": f"/api/artwork/custom/{art_id}",
        "slot": slot,
        "origin": name,
        "size": len(raw),
    }


class ArtworkUrlIn(BaseModel):
    folder_path: str
    url: str
    slot: Optional[str] = None


@router.post("/artwork/custom-url")
async def artwork_custom_url(payload: ArtworkUrlIn):
    """Register a remote image URL as a custom artwork candidate."""
    p = _safe_under_root(payload.folder_path)
    url = (payload.url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="URL must be http(s)")
    art_id = hashlib.sha1(f"{p}|{url}".encode("utf-8")).hexdigest()
    db.add_custom_artwork(
        art_id,
        folder_path=str(p),
        slot=payload.slot or None,
        source="url",
        origin=url,
        file_path=url,
        content_type=None,
        size=None,
    )
    return {
        "ok": True,
        "id": art_id,
        "url": url,
        "slot": payload.slot,
    }


@router.get("/artwork/custom/{art_id}")
async def artwork_custom_get(art_id: str):
    row = db.get_custom_artwork(art_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    d = dict(row)
    if d.get("source") != "upload":
        raise HTTPException(status_code=400, detail="Not an uploaded asset; URL is already public")
    fp = Path(d.get("file_path") or "")
    if not fp.exists() or not fp.is_file():
        raise HTTPException(status_code=404, detail="File missing on disk")
    return FileResponse(fp, media_type=d.get("content_type") or "image/jpeg")


@router.delete("/artwork/custom/{art_id}")
async def artwork_custom_delete(art_id: str):
    row = db.get_custom_artwork(art_id)
    if not row:
        return {"ok": True, "deleted": 0}
    d = dict(row)
    if d.get("source") == "upload":
        fp = Path(d.get("file_path") or "")
        if fp.exists() and fp.is_file():
            try:
                fp.unlink()
            except Exception as e:
                logger.warning("Could not delete custom artwork file {}: {}", fp, e)
    n = db.delete_custom_artwork(art_id)
    return {"ok": True, "deleted": n}


@router.get("/artwork/custom")
async def artwork_custom_list(folder_path: str):
    p = _safe_under_root(folder_path)
    rows = db.list_custom_artwork(str(p))
    return {"items": [dict(r) for r in rows]}


# ---- Episode mapper (v0.4.0) -----------------------------------------------

@router.get("/episodes")
async def episodes_list(path: str):
    """Return local episode files alongside their current provider match
    (with overrides applied) plus the full provider episode list so the UI
    can populate dropdowns.

    v0.9.1: works for TMDB-bound series too. Previously this endpoint
    blindly used the TVDB client even when the binding pointed at TMDB,
    which 500'd on series like ``Love Me - Kaede to Suzu the Animation``
    that we deliberately bind via ``{tmdb-...}``.
    """
    p = _safe_under_root(path)
    binding = db.get_binding(str(p))
    if not binding or binding["kind"] != "series":
        raise HTTPException(status_code=400, detail="Folder is not bound to a series")
    settings = get_user_settings()
    lang = settings.preferred_language
    provider = (binding["provider"] or "tvdb").lower()

    # Pull a normalised episode list ({id, seasonNumber, number, name,
    #   aired, image}) from whichever provider the binding points at.
    episodes: list[dict] = []
    if provider == "tmdb":
        tmdb_c = get_tmdb_client()
        try:
            details = await tmdb_c.tv_details(binding["external_id"], language=lang)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"TMDB tv_details failed: {e}")
        seasons = details.get("seasons") or []
        for s in seasons:
            sn = s.get("season_number")
            if sn is None:
                continue
            try:
                sdata = await tmdb_c.tv_season(binding["external_id"], int(sn), language=lang)
            except Exception as se:
                logger.warning("tmdb tv_season {} s{} failed: {}", binding["external_id"], sn, se)
                continue
            for ep in (sdata.get("episodes") or []):
                still = ep.get("still_path")
                episodes.append({
                    "id": ep.get("id"),
                    "seasonNumber": ep.get("season_number"),
                    "number": ep.get("episode_number"),
                    "name": ep.get("name"),
                    "aired": ep.get("air_date"),
                    "image": tmdb_image_url(still, "w300") if still else None,
                })
    else:
        client = get_client()
        try:
            episodes = await client.series_episodes(
                binding["external_id"], season_type="default", language=lang
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"TVDB series_episodes failed: {e}")

    by_se: dict[tuple[int, int], dict] = {}
    by_id: dict[str, dict] = {}
    for ep in episodes:
        if ep.get("id") is not None:
            by_id[str(ep["id"])] = ep
        sn = ep.get("seasonNumber")
        en = ep.get("number")
        if sn is None or en is None:
            continue
        by_se[(int(sn), int(en))] = ep
    legacy_overrides = db.get_episode_overrides(str(p))           # {(s,e): id}
    file_overrides = db.get_episode_file_overrides(str(p))         # {file_path: {...}}

    def _local_thumb_for(video_path: Path) -> Optional[str]:
        """Return the existing on-disk ``<stem>-thumb.{jpg,jpeg,png}`` for a
        given video file, or ``None`` if no thumbnail has been generated yet.

        The Overrides tab renders these next to each matched episode so the
        user can sanity-check at a glance whether the correct still is being
        written — separate from the full Artwork picker grid, which would be
        far too cluttered for per-episode verification. (v0.11.8)
        """
        for ext in (".jpg", ".jpeg", ".png"):
            c = video_path.with_name(f"{video_path.stem}-thumb{ext}")
            if c.exists() and c.is_file():
                return str(c)
        return None

    def _row_for(parsed_path: Path, parsed_season: int, parsed_episode: int,
                 unparsed: bool) -> dict:
        """Build one row for the Episodes endpoint, applying overrides.

        Order of precedence for the effective season/episode:
          1. v0.10.0 per-file override (most specific).
          2. The parsed value from the filename.
        For the matched provider episode:
          1. file override's external_id, if present.
          2. legacy (s, e)-keyed override (back-compat).
          3. by-(season,episode) lookup against the provider list.
        """
        ovr = file_overrides.get(str(parsed_path)) or {}
        effective_season = (
            ovr.get("season") if ovr.get("season") is not None else parsed_season
        )
        effective_episode = (
            ovr.get("episode") if ovr.get("episode") is not None else parsed_episode
        )
        external_id = ovr.get("external_id")
        legacy_id = legacy_overrides.get(
            (int(effective_season or 0), int(effective_episode or 0))
        )
        matched_id = external_id or legacy_id
        matched = None
        if matched_id and str(matched_id) in by_id:
            matched = by_id[str(matched_id)]
        elif (
            effective_season is not None
            and effective_episode is not None
            and (int(effective_season), int(effective_episode)) in by_se
        ):
            matched = by_se[(int(effective_season), int(effective_episode))]
        return {
            "file_path": str(parsed_path),
            "file_name": parsed_path.name,
            "parsed_season": int(parsed_season) if parsed_season is not None else 0,
            "parsed_episode": int(parsed_episode) if parsed_episode is not None else 0,
            "effective_season": (
                int(effective_season) if effective_season is not None else None
            ),
            "effective_episode": (
                int(effective_episode) if effective_episode is not None else None
            ),
            "override_episode_id": str(matched_id) if matched_id else None,
            "matched_episode_id": str(matched["id"]) if matched else None,
            "matched_season": matched.get("seasonNumber") if matched else None,
            "matched_number": matched.get("number") if matched else None,
            "matched_title": matched.get("name") if matched else None,
            # v0.11.8: remote episode still (if the provider has one) and the
            # local ``<stem>-thumb.{jpg,jpeg,png}`` already on disk. The
            # Overrides tab shows both so the user can confirm at a glance
            # that the generated thumbnail matches the matched episode.
            #
            # TVDB episode ``image`` values are relative artwork paths and
            # must be absolutized. TMDB values are already full URLs because
            # we normalised them at the top of this endpoint.
            "matched_image": (
                artwork_svc.absolutize_tvdb_url(matched.get("image"))
                if matched and provider != "tmdb"
                else (matched.get("image") if matched else None)
            ),
            "local_thumb": _local_thumb_for(parsed_path),
            "unparsed": bool(unparsed),
            "has_file_override": bool(ovr),
        }

    locals_out: list[dict] = []
    # v0.9.0: include both season-subdir episodes and any video files
    # dropped at the series root (counted as season 0).
    season_dirs = detect_season_dirs(p)
    for sd in season_dirs:
        snum = season_number_from_dir(sd.name)
        for parsed in list_season_episodes(sd):
            if not getattr(parsed, "parsed", True):
                locals_out.append(_row_for(parsed.path, snum, None, unparsed=True))
                continue
            locals_out.append(
                _row_for(parsed.path, snum, int(parsed.episode), unparsed=False)
            )
    # Loose video files at the series root — use the parser's own season.
    for parsed in list_season_episodes(p):
        if not getattr(parsed, "parsed", True):
            locals_out.append(_row_for(parsed.path, None, None, unparsed=True))
            continue
        locals_out.append(
            _row_for(
                parsed.path,
                int(parsed.season) if parsed.season is not None else 1,
                int(parsed.episode),
                unparsed=False,
            )
        )

    tvdb_eps = [
        {
            "id": str(ep.get("id")),
            "season": ep.get("seasonNumber"),
            "number": ep.get("number"),
            "name": ep.get("name"),
            "aired": ep.get("aired"),
            "image": ep.get("image"),
        }
        for ep in episodes
        if ep.get("id") is not None
    ]
    tvdb_eps.sort(key=lambda e: ((e["season"] if e["season"] is not None else 99), (e["number"] or 0)))
    return {
        "path": str(p),
        "provider": provider,
        "locals": locals_out,
        "tvdb_episodes": tvdb_eps,
    }


class EpisodeOverrideIn(BaseModel):
    folder_path: str
    season: int
    episode: int
    tvdb_episode_id: Optional[str] = None  # null clears


@router.post("/episodes/override")
async def episodes_override(payload: EpisodeOverrideIn):
    p = _safe_under_root(payload.folder_path)
    if payload.tvdb_episode_id:
        db.set_episode_override(str(p), payload.season, payload.episode, payload.tvdb_episode_id)
    else:
        db.clear_episode_override(str(p), payload.season, payload.episode)
    sidecar_svc.sync_sidecar_from_db(p)
    return {"ok": True}


# ---- Per-file episode override (v0.10.0) -----------------------------------
#
# Anchors a (season, episode, external_id) selection to the actual file path.
# This replaces the v0.4 (folder, season, episode)-keyed table for new
# selections so multiple unparsed files in the same folder can each have
# their own mapping. The legacy table is still honoured by the read path.


class EpisodeFileOverrideIn(BaseModel):
    folder_path: str
    file_path: str
    season: Optional[int] = None
    episode: Optional[int] = None
    external_id: Optional[str] = None  # provider episode id (TVDB or TMDB)
    clear: bool = False


@router.post("/episodes/override-file")
async def episodes_override_file(payload: EpisodeFileOverrideIn):
    p = _safe_under_root(payload.folder_path)
    fp = _safe_under_root(payload.file_path)
    if not str(fp).startswith(str(p)):
        raise HTTPException(
            status_code=400, detail="file_path must live under folder_path"
        )
    if payload.clear:
        db.clear_episode_file_override(str(p), str(fp))
    else:
        db.set_episode_file_override(
            str(p),
            str(fp),
            payload.season,
            payload.episode,
            payload.external_id,
        )
    sidecar_svc.sync_sidecar_from_db(p)
    return {"ok": True}


# ---- Per-episode thumbnail picker (v0.11.9) -------------------------------
#
# TMDB ships multiple stills per episode and the Overrides tab lets the user
# pick which one. Selections are stored in the existing ``artwork_selections``
# table under slot ``episode-thumb-{external_id}`` so they:
#
#   * survive a renamer pass (selection is keyed by provider id, not file path),
#   * round-trip through the sidecar (sidecar serializes the whole table),
#   * cost nothing in schema migrations.
#
# TVDB v4's episode record only exposes a single ``image`` per episode, so for
# TVDB-bound series the picker degrades to a one-tile grid plus a hint.


@router.get("/episodes/thumb-candidates")
async def episodes_thumb_candidates(path: str, season: int, episode: int):
    """Return every still candidate for a single episode.

    For TMDB-bound shows this hits ``/tv/{id}/season/{n}/episode/{e}/images``
    and returns a grid the user can pick from. For TVDB-bound shows it
    returns the single ``image`` field as a one-element list with a hint
    so the UI can show a friendly "only one available" message.
    """
    p = _safe_under_root(path)
    binding = db.get_binding(str(p))
    if not binding or binding["kind"] != "series":
        raise HTTPException(status_code=400, detail="Folder is not bound to a series")
    provider = (binding["provider"] or "tvdb").lower()
    settings = get_user_settings()
    lang = settings.preferred_language

    candidates: list[dict] = []
    external_id: Optional[str] = None
    note: Optional[str] = None

    if provider == "tmdb":
        tmdb_c = get_tmdb_client()
        try:
            sdata = await tmdb_c.tv_season(
                binding["external_id"], int(season), language=lang
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"TMDB tv_season failed: {e}")
        ep_match = None
        for ep in (sdata.get("episodes") or []):
            if int(ep.get("episode_number") or -1) == int(episode):
                ep_match = ep
                break
        if ep_match and ep_match.get("id") is not None:
            external_id = str(ep_match["id"])
        try:
            data = await tmdb_c.tv_episode_images(
                binding["external_id"], int(season), int(episode)
            )
        except Exception as e:
            raise HTTPException(
                status_code=502, detail=f"TMDB tv_episode_images failed: {e}"
            )
        # Default still (when no images endpoint result, fall back to season list).
        default_path = (ep_match or {}).get("still_path")
        for s in (data.get("stills") or []):
            full = tmdb_image_url(s.get("file_path"), "original")
            thumb = tmdb_image_url(s.get("file_path"), "w300")
            if not full:
                continue
            candidates.append({
                "url": full,
                "thumb": thumb,
                "width": s.get("width"),
                "height": s.get("height"),
                "language": s.get("iso_639_1"),
                "vote_average": s.get("vote_average"),
                "is_default": (
                    bool(default_path) and s.get("file_path") == default_path
                ),
            })
        if not candidates and default_path:
            # Fallback: at least show the default still.
            full = tmdb_image_url(default_path, "original")
            thumb = tmdb_image_url(default_path, "w300")
            if full:
                candidates.append({
                    "url": full, "thumb": thumb,
                    "width": None, "height": None,
                    "language": None, "vote_average": None,
                    "is_default": True,
                })
    else:
        client = get_client()
        try:
            episodes = await client.series_episodes(
                binding["external_id"], season_type="default", language=lang
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"TVDB series_episodes failed: {e}")
        ep_match = None
        for ep in episodes:
            if (
                int(ep.get("seasonNumber") or -1) == int(season)
                and int(ep.get("number") or -1) == int(episode)
            ):
                ep_match = ep
                break
        if ep_match:
            if ep_match.get("id") is not None:
                external_id = str(ep_match["id"])
            img = ep_match.get("image")
            if img:
                full = artwork_svc.absolutize_tvdb_url(img)
                candidates.append({
                    "url": full,
                    "thumb": full,
                    "width": None, "height": None,
                    "language": None, "vote_average": None,
                    "is_default": True,
                })
        note = "TVDB only ships one still per episode \u2014 switch the source to TMDB if you want to pick from multiple."

    selection_url: Optional[str] = None
    if external_id is not None:
        sels = db.get_artwork_selections(str(p))
        sel = sels.get(f"episode-thumb-{external_id}")
        if sel and sel.get("url"):
            selection_url = sel["url"]

    # Annotate which candidate (if any) is currently selected.
    for c in candidates:
        c["selected"] = bool(selection_url and c["url"] == selection_url)

    return {
        "path": str(p),
        "provider": provider,
        "season": int(season),
        "episode": int(episode),
        "external_id": external_id,
        "current_selection": selection_url,
        "candidates": candidates,
        "note": note,
    }


class EpisodeThumbSelectIn(BaseModel):
    folder_path: str
    external_id: str            # provider episode id
    url: Optional[str] = None    # null clears the override


@router.post("/episodes/thumb-select")
async def episodes_thumb_select(payload: EpisodeThumbSelectIn):
    p = _safe_under_root(payload.folder_path)
    binding = db.get_binding(str(p))
    if not binding or binding["kind"] != "series":
        raise HTTPException(status_code=400, detail="Folder is not bound to a series")
    slot = f"episode-thumb-{payload.external_id}"
    if payload.url:
        db.set_artwork_selection(str(p), slot, payload.url, language=None, score=None)
    else:
        db.clear_artwork_selection(str(p), slot)
    sidecar_svc.sync_sidecar_from_db(p)
    return {"ok": True}


# ---- File rename (v0.10.0) -------------------------------------------------

class RenamePreviewIn(BaseModel):
    folder_path: str
    # Optional ad-hoc template overrides. When omitted, the UserSettings
    # default for the chosen series_type (or movie) is used.
    template: Optional[str] = None              # standard episode / movie
    daily_template: Optional[str] = None
    anime_template: Optional[str] = None
    # "auto" | "standard" | "daily" | "anime" — ignored for movies.
    series_type: str = "auto"
    # v0.11.7: manual release-group override applied to every plan item.
    # Mainly useful for anime where the fansub group is laid out in a
    # bracket pattern the auto-detector can't safely guess (e.g.
    # ``[Group A][Group B]Title``). Empty / None means "auto-detect as before".
    release_group: Optional[str] = None


async def _resolve_localized_title(
    binding: dict, lang: Optional[str], fallbacks: list[str]
) -> tuple[Optional[str], Optional[int]]:
    """Return ``(title, year)`` for ``binding`` in the user's preferred language.

    The bound ``title`` row is whatever the provider returned at match time -
    that's usually English, but for non-English originals (anime, foreign
    films) it can be the original-language name. The renamer needs the
    *user's* preferred language, so we re-fetch the title here:

    * TVDB:  ``/series|movies/{id}/translations/{lang}`` with fallbacks.
    * TMDB:  ``tv_details(language=lang)`` -> ``name`` (TMDB resolves the
      language server-side and falls back to the show's default).

    On any error we fall back to the bound title so the rename still works.
    """
    provider = (binding.get("provider") or "").lower()
    bound_title = binding.get("title")
    bound_year = binding.get("year")
    ext_id = binding.get("external_id")
    if not provider or not ext_id:
        return bound_title, bound_year
    try:
        if provider == "tvdb":
            kind = "series" if binding.get("kind") == "series" else "movies"
            client = get_client()
            tr = await client.best_translation(kind, ext_id, lang or "", fallbacks)
            if tr and tr.get("name"):
                return tr["name"], bound_year
        elif provider == "tmdb":
            tmdb_c = get_tmdb_client()
            if binding.get("kind") == "series":
                d = await tmdb_c.tv_details(ext_id, language=lang)
                title = d.get("name") or d.get("original_name")
            else:
                d = await tmdb_c.movie_details(ext_id, language=lang)
                title = d.get("title") or d.get("original_title")
            if title:
                return title, bound_year
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "localized title lookup failed for {} {}: {}",
            provider, ext_id, e,
        )
    return bound_title, bound_year


async def _build_episodes_index(binding: dict, lang: Optional[str]) -> dict[tuple[int, int], dict]:
    """Pull the provider episode list for ``binding`` and key it by (s, e)."""
    provider = (binding.get("provider") or "tvdb").lower()
    episodes: list[dict] = []
    if provider == "tmdb":
        tmdb_c = get_tmdb_client()
        details = await tmdb_c.tv_details(binding["external_id"], language=lang)
        for s in details.get("seasons") or []:
            sn = s.get("season_number")
            if sn is None:
                continue
            try:
                sdata = await tmdb_c.tv_season(binding["external_id"], int(sn), language=lang)
            except Exception:
                continue
            for ep in (sdata.get("episodes") or []):
                episodes.append({
                    "id": ep.get("id"),
                    "seasonNumber": ep.get("season_number"),
                    "number": ep.get("episode_number"),
                    "name": ep.get("name"),
                })
    else:
        client = get_client()
        episodes = await client.series_episodes(
            binding["external_id"], season_type="default", language=lang
        )
    by_se: dict[tuple[int, int], dict] = {}
    for ep in episodes:
        sn = ep.get("seasonNumber")
        en = ep.get("number")
        if sn is None or en is None:
            continue
        by_se[(int(sn), int(en))] = ep
    return by_se


@router.post("/episodes/rename/preview")
async def episodes_rename_preview(payload: RenamePreviewIn):
    """Return a dry-run rename plan. The filesystem is not touched."""
    p = _safe_under_root(payload.folder_path)
    binding = db.get_binding(str(p))
    if not binding:
        raise HTTPException(
            status_code=400,
            detail="Folder must be matched to a series or movie before renaming",
        )
    settings = get_user_settings()
    if not settings.rename_enabled:
        raise HTTPException(status_code=400, detail="Renaming is disabled in Settings")
    template = (payload.template or "").strip()
    lang = settings.preferred_language
    fallbacks = list(settings.fallback_languages or [])
    if binding["kind"] == "series":
        template = template or settings.rename_episode_template
        try:
            episodes_idx = await _build_episodes_index(
                dict(binding), lang
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"provider lookup failed: {e}")
        provider = (binding["provider"] or "").lower()
        ext_id = str(binding["external_id"]) if binding["external_id"] is not None else None
        tvdb_id = ext_id if provider == "tvdb" else None
        tmdb_id = ext_id if provider == "tmdb" else None
        daily_t = (payload.daily_template or "").strip() or settings.rename_daily_template
        anime_t = (payload.anime_template or "").strip() or settings.rename_anime_template
        localized_title, _ = await _resolve_localized_title(dict(binding), lang, fallbacks)
        plan = renamer_svc.plan_series_rename(
            p,
            standard_template=template,
            daily_template=daily_t,
            anime_template=anime_t,
            series_type=payload.series_type or "auto",
            title=(localized_title or binding["title"] or Path(str(p)).name),
            year=binding["year"],
            tvdb_id=tvdb_id,
            tmdb_id=tmdb_id,
            episodes_by_se=episodes_idx,
            overrides_by_file=db.get_episode_file_overrides(str(p)),
            release_group_override=payload.release_group,
        )
    else:
        template = template or settings.rename_movie_template
        provider = (binding["provider"] or "").lower()
        ext_id = str(binding["external_id"]) if binding["external_id"] is not None else None
        tmdb_id = ext_id if provider == "tmdb" else None
        tvdb_id = ext_id if provider == "tvdb" else None
        localized_title, _ = await _resolve_localized_title(dict(binding), lang, fallbacks)
        plan = renamer_svc.plan_movie_rename(
            p,
            template=template,
            title=(localized_title or binding["title"] or Path(str(p)).name),
            year=binding["year"],
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            release_group_override=payload.release_group,
        )
    return {
        "folder_path": str(p),
        "template": template,
        "items": [
            {
                "src": item.src,
                "dst": item.dst,
                "src_name": Path(item.src).name,
                "dst_name": Path(item.dst).name,
                "season": item.season,
                "episode": item.episode,
                "matched_title": item.matched_title,
                "conflict": item.conflict,
                "unchanged": item.src == item.dst,
            }
            for item in plan
        ],
    }


class RenameApplyIn(BaseModel):
    folder_path: str
    template: Optional[str] = None
    daily_template: Optional[str] = None
    anime_template: Optional[str] = None
    series_type: str = "auto"
    # Restrict the apply to a subset of source paths (per-row checkbox UI).
    # If empty/null, every plan item that's safe to rename is applied.
    only_src: Optional[list[str]] = None
    # v0.11.7: same release-group override the preview accepted.
    release_group: Optional[str] = None


@router.post("/episodes/rename/apply")
async def episodes_rename_apply(payload: RenameApplyIn):
    p = _safe_under_root(payload.folder_path)
    binding = db.get_binding(str(p))
    if not binding:
        raise HTTPException(status_code=400, detail="Folder is not matched")
    settings = get_user_settings()
    if not settings.rename_enabled:
        raise HTTPException(status_code=400, detail="Renaming is disabled in Settings")
    template = (payload.template or "").strip()
    lang = settings.preferred_language
    fallbacks = list(settings.fallback_languages or [])
    if binding["kind"] == "series":
        template = template or settings.rename_episode_template
        episodes_idx = await _build_episodes_index(
            dict(binding), lang
        )
        provider = (binding["provider"] or "").lower()
        ext_id = str(binding["external_id"]) if binding["external_id"] is not None else None
        tvdb_id = ext_id if provider == "tvdb" else None
        tmdb_id = ext_id if provider == "tmdb" else None
        daily_t = (payload.daily_template or "").strip() or settings.rename_daily_template
        anime_t = (payload.anime_template or "").strip() or settings.rename_anime_template
        localized_title, _ = await _resolve_localized_title(dict(binding), lang, fallbacks)
        plan = renamer_svc.plan_series_rename(
            p,
            standard_template=template,
            daily_template=daily_t,
            anime_template=anime_t,
            series_type=payload.series_type or "auto",
            title=(localized_title or binding["title"] or Path(str(p)).name),
            year=binding["year"],
            tvdb_id=tvdb_id,
            tmdb_id=tmdb_id,
            episodes_by_se=episodes_idx,
            overrides_by_file=db.get_episode_file_overrides(str(p)),
            release_group_override=payload.release_group,
        )
    else:
        template = template or settings.rename_movie_template
        provider = (binding["provider"] or "").lower()
        ext_id = str(binding["external_id"]) if binding["external_id"] is not None else None
        tmdb_id = ext_id if provider == "tmdb" else None
        tvdb_id = ext_id if provider == "tvdb" else None
        localized_title, _ = await _resolve_localized_title(dict(binding), lang, fallbacks)
        plan = renamer_svc.plan_movie_rename(
            p,
            template=template,
            title=(localized_title or binding["title"] or Path(str(p)).name),
            year=binding["year"],
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            release_group_override=payload.release_group,
        )
    if payload.only_src:
        wanted = set(payload.only_src)
        plan = [it for it in plan if it.src in wanted]
    summary = renamer_svc.apply_rename_plan(plan)
    if summary["renamed"]:
        sidecar_svc.sync_sidecar_from_db(p)
    return {"ok": True, **summary}


# ---- Logs ------------------------------------------------------------------

@router.get("/logs/app")
async def logs_app(tail: int = 500):
    p = LOG_DIR / "app.log"
    if not p.exists():
        return {"lines": []}
    lines = p.read_text(errors="ignore").splitlines()[-tail:]
    return {"lines": lines}


# ---- TVDB metadata helpers (used by Grid view to fetch posters) ------------

@router.get("/tvdb/series/{series_id}")
async def tvdb_series(series_id: str):
    client = get_client()
    return await client.series_extended(series_id)


@router.get("/tvdb/movie/{movie_id}")
async def tvdb_movie(movie_id: str):
    client = get_client()
    return await client.movie_extended(movie_id)


@router.post("/tvdb/cache/clear")
async def tvdb_cache_clear():
    n = db.cache_clear()
    return {"cleared": n}


# ---- Plex integration (v0.6.0) ---------------------------------------------

class PlexRefreshIn(BaseModel):
    path: str
    delay_seconds: Optional[int] = 0


@router.get("/plex/test")
async def plex_test():
    """Validate the configured Plex URL+token and return identity + sections."""
    return await plex_svc.test_connection()


@router.get("/plex/sections")
async def plex_sections():
    """List Plex library sections with their on-disk locations."""
    s = get_user_settings()
    if not (s.plex_url and s.plex_token):
        raise HTTPException(status_code=400, detail="Plex is not configured")
    try:
        client = plex_svc.PlexClient(s.plex_url, s.plex_token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    try:
        sections = await client.list_sections()
    except plex_svc.PlexError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await client.aclose()
    return {"sections": sections}


@router.post("/plex/refresh")
async def plex_refresh(body: PlexRefreshIn):
    """Manually trigger a Plex partial-rescan for ``path``.

    Mirrors what the auto-refresh post-build hook does, but exposed for the
    DetailView "Refresh in Plex" button. Returns the same summary dict
    ``refresh_for_folder`` produces — never raises, surfaces errors in JSON.
    """
    if not body.path or not body.path.strip():
        raise HTTPException(status_code=400, detail="path is required")
    delay = max(0, min(600, int(body.delay_seconds or 0)))
    summary = await plex_svc.refresh_for_folder(body.path, delay_seconds=delay)
    return summary


# ---- Schedules (v0.8.0) -----------------------------------------------------

_VALID_SCHEDULE_ACTIONS = {
    "scan_only",
    "match_only",
    "build_only",
    "match_and_build",
    "full",
}


class ScheduleIn(BaseModel):
    library: Optional[str] = None  # None = all libraries
    cron: str
    action: str
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    library: Optional[str] = None
    cron: Optional[str] = None
    action: Optional[str] = None
    enabled: Optional[bool] = None


def _validate_schedule(cron: Optional[str], action: Optional[str]) -> None:
    if action is not None and action not in _VALID_SCHEDULE_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"action must be one of {sorted(_VALID_SCHEDULE_ACTIONS)}",
        )
    if cron is not None:
        try:
            # Use a dummy datetime — we just want syntax validation.
            from datetime import datetime, timezone
            _cron_matches(cron, datetime.now(timezone.utc))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"invalid cron: {e}")


@router.get("/schedules")
async def schedules_list():
    return {"schedules": [dict(r) for r in db.list_schedules()]}


@router.post("/schedules")
async def schedules_create(payload: ScheduleIn):
    _validate_schedule(payload.cron, payload.action)
    library = payload.library or None
    sched_id = db.insert_schedule(
        library=library,
        cron=payload.cron.strip(),
        action=payload.action,
        enabled=payload.enabled,
    )
    row = db.get_schedule(sched_id)
    return {"ok": True, "schedule": dict(row) if row else None}


@router.patch("/schedules/{sched_id}")
async def schedules_update(sched_id: int, payload: ScheduleUpdate):
    row = db.get_schedule(sched_id)
    if not row:
        raise HTTPException(status_code=404, detail="schedule not found")
    _validate_schedule(payload.cron, payload.action)
    db.update_schedule(
        sched_id,
        library=payload.library if payload.library is not None else None,
        cron=payload.cron,
        action=payload.action,
        enabled=payload.enabled,
    )
    row = db.get_schedule(sched_id)
    return {"ok": True, "schedule": dict(row) if row else None}


@router.delete("/schedules/{sched_id}")
async def schedules_delete(sched_id: int):
    n = db.delete_schedule(sched_id)
    if not n:
        raise HTTPException(status_code=404, detail="schedule not found")
    return {"ok": True, "deleted": n}


@router.post("/schedules/{sched_id}/run")
async def schedules_run(sched_id: int):
    if not _scheduler.run_now(sched_id):
        raise HTTPException(status_code=404, detail="schedule not found")
    return {"ok": True, "started": True}
