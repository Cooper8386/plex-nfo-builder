"""End-to-end build: match → fetch metadata → write NFOs → download artwork."""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from loguru import logger

from .. import db
from ..config import effective_metadata_source, get_user_settings
from ..logging_setup import job_logger, close_job_logger
from .artwork import absolutize_tvdb_url, download_movie_canonical, download_series_canonical, season_poster_filename
from .artwork_resolver import (
    resolve_preferred_artwork_movie,
    resolve_preferred_artwork_series,
)
from .plex import refresh_for_folder as _plex_refresh_for_folder
from .matcher import (
    auto_match_movie,
    auto_match_movie_tmdb,
    auto_match_series,
    auto_match_series_tmdb,
)
from .orphans import sweep_movie_orphans, sweep_series_orphans
from .nfo import (
    build_episode_nfo,
    build_episode_nfo_tmdb,
    build_movie_nfo,
    build_movie_nfo_tmdb,
    build_season_nfo,
    build_series_nfo,
    build_series_nfo_tmdb,
)
from .parser import (
    detect_season_dirs,
    is_video,
    list_season_episodes,
    parse_folder_name,
    season_number_from_dir,
)
from .scanner import scan_movie_folder, scan_series_folder
from .sidecar import write_sidecar
from .tmdb import get_client as get_tmdb_client, image_url as tmdb_image_url
from .tvdb import get_client


def _manual_secondary_id(binding, want_provider: str) -> Optional[str]:
    """Return the binding's manually-attached id for ``want_provider`` if any.

    Used to feed the cross-provider artwork resolver when the user has
    pinned a TMDB id on a TVDB-bound show (or vice versa) and the
    metadata record itself doesn't cross-reference the other source.
    """
    if not binding:
        return None
    try:
        keys = binding.keys() if hasattr(binding, "keys") else []
    except Exception:
        keys = []
    if "secondary_provider" not in keys:
        return None
    sp = (binding["secondary_provider"] or "").lower()
    if sp != want_provider.lower():
        return None
    sid = binding["secondary_external_id"] if "secondary_external_id" in keys else None
    return str(sid) if sid else None


def _manual_secondary_tuple(binding) -> Optional[tuple[str, str]]:
    """Return ``(provider, id)`` for the manually-pinned secondary, if any.

    Used to feed the NFO builders so they can emit a ``<uniqueid type=...>``
    tag for the user-supplied cross-source id when the metadata record
    itself doesn't already include it.
    """
    if not binding:
        return None
    try:
        keys = binding.keys() if hasattr(binding, "keys") else []
    except Exception:
        keys = []
    if "secondary_provider" not in keys or "secondary_external_id" not in keys:
        return None
    sp = (binding["secondary_provider"] or "").strip().lower()
    sid = binding["secondary_external_id"]
    sid = str(sid).strip() if sid is not None else ""
    if not sp or not sid:
        return None
    return (sp, sid)


async def _hydrate_tvdb_character_thumbs(
    characters: Optional[list],
    *,
    log,
    force: bool = False,  # noqa: ARG001 - kept for call-site compatibility
    limit: int = 60,
) -> None:
    """Backfill missing actor portraits on TVDB character records.

    TVDB v4 character objects expose two image fields:

    * ``image`` — role-specific art for the character (often null)
    * ``personImgURL`` — a copy of the actor's headshot

    On many shows ``personImgURL`` is *also* null on the character
    record even though the underlying People record carries the
    actor's headshot. TVDB's own site falls back to the person
    record in that case; v0.11.14 only used the two fields already on
    the character object, so cast members like Miles Luna / Shannon
    McCormick / Michael Jones / Kerry Shawcross on RWBY still rendered
    as initials in Plex.

    This helper mutates the characters list in-place: for any entry
    that has neither ``image`` nor ``personImgURL`` but does carry a
    ``peopleId``, fetch ``/people/{peopleId}`` and copy its ``image``
    onto the character as ``personImgURL`` so the existing fallback
    logic in ``build_*_nfo`` picks it up.

    Cache discipline (v0.11.16): we deliberately ignore the build's
    ``force`` flag for these lookups. People records change at glacial
    speed and a Force rebuild that issues dozens of fresh /people/{id}
    calls per show will trip TVDB's per-token rate limit, at which
    point the request raises and we silently fall back to no portrait.
    A user reported v0.11.15 fixing RWBY then immediately regressing
    after Force-rebuilding RWBY Chibi back-to-back — the second build
    burned through retries against a 429-throttled token. Cache-first
    keeps subsequent builds free.

    The fetch is concurrent (one task per missing portrait, capped at
    ``limit`` so a poorly-curated series with hundreds of recurring
    bit-parts can't tie up the build). Failures are logged at warning
    level (with the peopleId) so users diagnosing missing portraits
    can see them in the job log.
    """
    if not isinstance(characters, list) or not characters:
        return
    needs: list[dict] = []
    for c in characters:
        if not isinstance(c, dict):
            continue
        if c.get("image") or c.get("personImgURL"):
            continue
        if not c.get("peopleId"):
            continue
        needs.append(c)
        if len(needs) >= limit:
            break
    if not needs:
        return
    client = get_client()
    failed: list[int] = []

    async def _one(ch: dict) -> None:
        try:
            # NEVER force the cache here — see docstring.
            url = await client.person_image(ch["peopleId"], force=False)
        except Exception as e:
            log.warning(
                "person_image lookup failed for peopleId={} ({}): {}",
                ch.get("peopleId"),
                ch.get("personName") or "?",
                e,
            )
            failed.append(int(ch["peopleId"]))
            return
        if url:
            ch["personImgURL"] = url

    await asyncio.gather(*(_one(c) for c in needs), return_exceptions=True)
    filled = sum(1 for c in needs if c.get("personImgURL"))
    log.info(
        "Hydrated {}/{} cast portraits from TVDB people records"
        + (f" (failed: {failed})" if failed else ""),
        filled,
        len(needs),
    )


_ACTOR_NAME_SANITIZE_RE = re.compile(r'[<>:"|?*/\\\x00-\x1f]+')


def _sanitize_actor_filename(name: str) -> str:
    """Strip filesystem-illegal characters from an actor name so it can
    be safely used as ``.actors/{name}.jpg``. Plex matches by the literal
    filename stem against the ``<name>`` field in the NFO, so we keep
    spaces and unicode intact and only replace the small set of bytes
    that Windows / Linux / macOS forbid in filenames.
    """
    cleaned = _ACTOR_NAME_SANITIZE_RE.sub("_", name or "").strip().strip(".")
    return cleaned or "unknown"


async def _download_actor_portraits_tvdb(
    folder: Path,
    characters: Optional[list],
    *,
    log,
    force: bool,
    limit: int = 60,
) -> None:
    """Save actor headshots to ``{folder}/.actors/{Actor Name}.jpg``.

    Background
    ----------
    Even when ``tvshow.nfo`` carries a correct ``<thumb>`` URL for every
    cast member, Plex's online TV agent will *overwrite* those portraits
    seconds later by re-scraping TVDB directly. For shows like RWBY where
    the four lead voice actors (Miles Luna, Shannon McCormick, Michael
    Jones, Kerry Shawcross) have a null ``image`` field on their TVDB
    People record, the online agent's overwrite turns the portrait blank
    — even though our hydrator successfully filled it from the per-show
    character entry. v0.11.14–0.11.16 chased that bug from the wrong end.

    The fix is the Kodi / Jellyfin / Plex convention of writing actor
    portraits as local files in ``.actors/{Actor Name}.jpg`` next to
    ``tvshow.nfo``. Plex's Local Media Assets agent prefers those local
    files over anything an online agent later fetches, so the portraits
    survive every subsequent scrape.
    """
    if not isinstance(characters, list) or not characters:
        return
    seen: set[str] = set()
    targets: list[tuple[Path, str]] = []
    for c in characters:
        if not isinstance(c, dict):
            continue
        name = (c.get("personName") or "").strip()
        if not name:
            continue
        url = c.get("image") or c.get("personImgURL")
        url = absolutize_tvdb_url(url) if url else None
        if not url:
            continue
        fname = _sanitize_actor_filename(name)
        if fname in seen:
            continue
        seen.add(fname)
        dest = folder / ".actors" / f"{fname}.jpg"
        targets.append((dest, url))
        if len(targets) >= limit:
            break
    if not targets:
        return
    written = 0
    sem = asyncio.Semaphore(8)

    async def _one(dest: Path, url: str) -> bool:
        async with sem:
            return await _download_url(url, dest, force=force)

    results = await asyncio.gather(
        *(_one(d, u) for d, u in targets), return_exceptions=True
    )
    for r in results:
        if r is True:
            written += 1
    log.info(
        "Wrote {}/{} actor portraits to .actors/ (TVDB)",
        written, len(targets),
    )


async def _download_actor_portraits_tmdb(
    folder: Path,
    cast: Optional[list],
    *,
    log,
    force: bool,
    limit: int = 60,
) -> None:
    """TMDB variant of :func:`_download_actor_portraits_tvdb`.

    Consumes the ``credits.cast`` shape from TMDB v3 and downloads each
    actor's ``profile_path`` (rendered at w185) into ``.actors/``.
    """
    if not isinstance(cast, list) or not cast:
        return
    seen: set[str] = set()
    targets: list[tuple[Path, str]] = []
    for c in cast:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        if not name:
            continue
        path = c.get("profile_path")
        if not path:
            continue
        url = tmdb_image_url(path, "w185")
        if not url:
            continue
        fname = _sanitize_actor_filename(name)
        if fname in seen:
            continue
        seen.add(fname)
        dest = folder / ".actors" / f"{fname}.jpg"
        targets.append((dest, url))
        if len(targets) >= limit:
            break
    if not targets:
        return
    written = 0
    sem = asyncio.Semaphore(8)

    async def _one(dest: Path, url: str) -> bool:
        async with sem:
            return await _download_url(url, dest, force=force)

    results = await asyncio.gather(
        *(_one(d, u) for d, u in targets), return_exceptions=True
    )
    for r in results:
        if r is True:
            written += 1
    log.info(
        "Wrote {}/{} actor portraits to .actors/ (TMDB)",
        written, len(targets),
    )


_jobs: dict[str, dict] = {}


def start_build(folder: Path, kind: str, *, force: bool = False,
                language: Optional[str] = None) -> str:
    """Create a job synchronously, schedule the build coroutine, return the job id.

    The actual build runs in the background on the current event loop. Callers
    can poll /api/jobs/{id} for status. Useful for bulk operations.
    """
    jid = _new_job(kind, str(folder))
    if kind == "series":
        coro = build_series(folder, force=force, language=language, _jid=jid)
    else:
        coro = build_movie(folder, force=force, language=language, _jid=jid)
    asyncio.create_task(coro)
    return jid


def _new_job(kind: str, folder: str) -> str:
    jid = uuid.uuid4().hex[:12]
    _jobs[jid] = {
        "id": jid,
        "kind": kind,
        "folder": folder,
        "status": "running",
        "progress": 0,
        "total": 0,
        "started_at": int(time.time()),
        "finished_at": None,
        "messages": [],
    }
    return jid


def get_job(jid: str) -> Optional[dict]:
    return _jobs.get(jid)


def list_jobs() -> list[dict]:
    return sorted(_jobs.values(), key=lambda j: j["started_at"], reverse=True)[:200]


# ---- Series ----------------------------------------------------------------

async def build_series(folder: Path, *, force: bool = False,
                       language: Optional[str] = None,
                       _jid: Optional[str] = None) -> str:
    settings = get_user_settings()
    lang = language or settings.preferred_language
    fallbacks = settings.fallback_languages or [lang]
    jid = _jid or _new_job("series", str(folder))
    log = job_logger(jid)
    job = _jobs[jid]
    log.info("Starting series build for {}", folder)
    binding = db.get_binding(str(folder))
    library_name = folder.parent.name
    lib_source = effective_metadata_source(library_name)
    # TMDB-bound series take a separate path because the data shape differs.
    if binding and binding["provider"] == "tmdb":
        return await _build_series_tmdb(folder, binding, settings, lang, fallbacks,
                                        force=force, jid=jid, log=log, job=job)
    if not binding and lib_source == "tmdb":
        log.info("Library {!r} resolved to TMDB metadata source", library_name)
        return await _build_series_tmdb(folder, None, settings, lang, fallbacks,
                                        force=force, jid=jid, log=log, job=job)
    try:
        if binding and binding["provider"] == "tvdb" and not force:
            client = get_client()
            data = await client.series_extended(binding["external_id"], force=False)
        else:
            data = await auto_match_series(folder, language=lang,
                                            threshold=settings.auto_match_threshold)
        if not data:
            job["status"] = "failed"
            job["messages"].append("No TVDB match. Use manual matching.")
            log.error("No match for {}", folder.name)
            return jid

        client = get_client()
        episodes = await client.series_episodes(data["id"], season_type="default", language=lang, force=force)  # type: ignore[arg-type]
        log.info("Fetched {} episodes from TVDB", len(episodes))
        job["total"] = 1 + len(episodes)

        # Series NFO — translation must be fetched from /series/{id}/translations/{lang}
        series_translation = await client.best_translation(
            "series", data["id"], lang, fallbacks, force=force
        )
        if series_translation:
            log.info("Series translation resolved to language={}", series_translation.get("_resolved_language"))
        else:
            log.warning("No translation in {}/{} for series {} — using TVDB default name",
                        lang, fallbacks, data.get("id"))
        nfo_overrides = db.get_nfo_overrides(str(folder))
        # Resolve preferred-artwork-source overrides (e.g. user prefers TMDB
        # artwork while bound to TVDB metadata).
        local_season_nums: list[int] = []
        for sd in detect_season_dirs(folder):
            local_season_nums.append(season_number_from_dir(sd.name))
        preferred_overrides = await resolve_preferred_artwork_series(
            settings=settings,
            bound_provider="tvdb",
            tvdb_data=data,
            local_season_numbers=local_season_nums,
            prefer_languages=[lang, *fallbacks],
            force=force,
            manual_secondary_id=_manual_secondary_id(binding, "tmdb"),
        )
        if preferred_overrides:
            log.info("Preferred artwork source override applied for {} slot(s): {}",
                     len(preferred_overrides), sorted(preferred_overrides.keys()))
        # v0.11.15: backfill missing actor portraits from /people/{id}
        # before NFO is built. TVDB's character payload doesn't always
        # carry personImgURL even when the actor has a headshot on file.
        await _hydrate_tvdb_character_thumbs(
            data.get("characters"), log=log, force=force
        )
        nfo_text = build_series_nfo(
            data, language=lang, fallbacks=fallbacks,
            translation=series_translation,
            folder_path=str(folder),
            overrides=nfo_overrides,
            preferred_overrides=preferred_overrides,
            manual_secondary=_manual_secondary_tuple(binding),
        )
        (folder / "tvshow.nfo").write_text(nfo_text, encoding="utf-8")
        job["progress"] += 1
        log.info("Wrote tvshow.nfo")

        # Per-season season.nfo files (TVDB seasons list lives on the
        # extended payload; we use the base title/plot if the API returned one
        # and let overrides take priority). Skip season 0 (specials) unless the
        # user explicitly overrode it.
        tvdb_seasons_by_num: dict[int, dict] = {}
        for sd in (data.get("seasons") or []):
            if isinstance(sd, dict) and sd.get("number") is not None:
                try:
                    tvdb_seasons_by_num[int(sd["number"])] = sd
                except Exception:
                    pass

        # Build episode lookup: TVDB
        tvdb_index: dict[tuple[int, int], dict] = {}
        tvdb_by_id: dict[str, dict] = {}
        for ep in episodes:
            sn = ep.get("seasonNumber")
            en = ep.get("number")
            if ep.get("id") is not None:
                tvdb_by_id[str(ep["id"])] = ep
            if sn is None or en is None:
                continue
            tvdb_index[(int(sn), int(en))] = ep

        # Per-folder episode overrides: maps (season, episode) -> tvdb episode id.
        overrides = db.get_episode_overrides(str(folder))

        # Local episode files by season — collect mapping for artwork pipeline.
        unmatched: list[str] = []
        episode_local_map: dict[int, dict] = {}  # tvdb episode id -> ep with _local_path
        for sd in detect_season_dirs(folder):
            snum = season_number_from_dir(sd.name)
            # Write a season.nfo if there's an override for it or TVDB returned
            # season metadata. Always overwrite.
            try:
                tvs = tvdb_seasons_by_num.get(int(snum)) or {}
                season_scope_key = f"season-{int(snum):02d}"
                if (snum >= 0) and (nfo_overrides.get(season_scope_key) or tvs):
                    season_nfo = build_season_nfo(
                        int(snum),
                        base_title=tvs.get("name"),
                        base_plot=tvs.get("overview"),
                        overrides=nfo_overrides,
                        external_id=str(tvs.get("id")) if tvs.get("id") else None,
                        provider="tvdb",
                    )
                    (sd / "season.nfo").write_text(season_nfo, encoding="utf-8")
            except Exception as se:
                log.warning("season.nfo write for s{:02d} failed: {}", snum, se)
            for parsed in list_season_episodes(sd):
                # v0.9.0: list_season_episodes can return placeholder entries
                # for video files we couldn't parse. Skip them in the builder
                # so we don't try to look up bogus s00e00 metadata.
                if not getattr(parsed, "parsed", True):
                    unmatched.append(parsed.path.name)
                    job["progress"] += 1
                    continue
                key = (snum, parsed.episode)
                ep = None
                if key in overrides:
                    ep = tvdb_by_id.get(str(overrides[key]))
                    if ep:
                        log.info("Override applied for s{:02d}e{:02d} -> tvdb ep {}",
                                 snum, parsed.episode, ep.get("id"))
                if not ep:
                    ep = tvdb_index.get(key)
                if not ep:
                    unmatched.append(parsed.path.name)
                    job["progress"] += 1
                    continue
                # episode extended record
                try:
                    full_ep = await client.episode_extended(ep["id"], force=force)
                    if not full_ep:
                        full_ep = ep
                except Exception as e:
                    log.warning("episode_extended {} failed: {}", ep.get("id"), e)
                    full_ep = ep
                # episode translation must be fetched separately
                ep_translation = await client.best_translation(
                    "episodes", ep["id"], lang, fallbacks, force=force
                )
                # v0.11.15: backfill cast portraits from /people/{id}
                # for guest stars / supporting cast on the episode too.
                await _hydrate_tvdb_character_thumbs(
                    full_ep.get("characters") if isinstance(full_ep, dict) else None,
                    log=log,
                    force=force,
                )
                ep_text = build_episode_nfo(
                    full_ep, language=lang, fallbacks=fallbacks,
                    translation=ep_translation,
                    overrides=nfo_overrides,
                )
                nfo_path = parsed.path.with_suffix(".nfo")
                nfo_path.write_text(ep_text, encoding="utf-8")
                # remember local path for thumbnail download
                marker = dict(ep)
                marker["_local_path"] = str(parsed.path)
                episode_local_map[int(ep["id"])] = marker
                job["progress"] += 1
        if unmatched:
            log.warning("{} unmatched episode files: {}", len(unmatched), unmatched[:5])
            job["messages"].append(f"{len(unmatched)} episode file(s) had no TVDB match")

        # Artwork — direct canonical files in the show folder, no .artwork/ subfolder.
        artworks = data.get("artworks") or []
        await download_series_canonical(
            folder, data, artworks,
            episodes=list(episode_local_map.values()),
            prefer_languages=[lang, *fallbacks],
            force=force,
            preferred_overrides=preferred_overrides,
        )
        log.info("Artwork download complete")
        # v0.11.17: write actor portraits as local files in `.actors/`.
        # Plex's online TV agent re-scrapes cast from TVDB after we
        # write the NFO, and on shows like RWBY (where the lead VAs
        # have null `image` on their TVDB People record) that scrape
        # overwrites our correct <thumb> URLs with nothing. Local
        # `.actors/{Actor Name}.jpg` files are read by Plex's Local
        # Media Assets agent and survive subsequent online overwrites.
        await _download_actor_portraits_tvdb(
            folder, data.get("characters"), log=log, force=force,
        )
        # v0.11.10: sweep orphaned NFO/thumb companions before the rescan so
        # the DB's nfo-state counts reflect the post-sweep reality. See
        # services/orphans.py for the full rationale on the Sonarr upgrade bug.
        _maybe_sweep_orphans(folder, "series", settings, job, log)
        # rescan state
        scan_series_folder(folder, library=folder.parent.name)
        db.upsert_item_state(str(folder), last_built=int(time.time()))
        try:
            write_sidecar(folder)
        except Exception as se:
            log.warning("sidecar write failed: {}", se)
        job["status"] = "completed"
        log.info("Series build done for {}", folder.name)
        _maybe_schedule_plex_refresh(folder, settings, job, log)
    except Exception as e:
        job["status"] = "failed"
        job["messages"].append(str(e))
        log.exception("Build failed: {}", e)
    finally:
        job["finished_at"] = int(time.time())
        sink = close_job_logger(log)
        if sink:
            job["log_file"] = str(sink)
    return jid


def _maybe_sweep_orphans(folder: Path, kind: str, settings, job: dict, log) -> None:
    """Sweep orphaned NFO/thumb companions left behind by Sonarr/Radarr file
    upgrades. Runs at the tail of every successful build when the user has
    ``auto_sweep_orphans`` enabled (default on). Never raises into the build
    pipeline — a sweep failure is logged and recorded on the job, but the
    build itself stays "completed".

    The sweep is video-driven: it only deletes ``<stem>.nfo`` and
    ``<stem>-thumb.{jpg,jpeg,png}`` files whose stem doesn't pair with a
    live video file. Show-level artwork, ``tvshow.nfo``, ``season.nfo`` and
    every video / subtitle / audio file are always preserved.
    """
    if not getattr(settings, "auto_sweep_orphans", True):
        return
    try:
        if kind == "series":
            summary = sweep_series_orphans(folder, dry_run=False)
        else:
            summary = sweep_movie_orphans(folder, dry_run=False)
    except Exception as e:  # noqa: BLE001
        log.warning("orphan sweep failed for {}: {}", folder, e)
        return
    n_nfo = int(summary.get("nfo_removed") or 0)
    n_thumb = int(summary.get("thumb_removed") or 0)
    if n_nfo == 0 and n_thumb == 0:
        return
    msg = (
        f"Removed {n_nfo} orphaned NFO(s) and {n_thumb} orphaned "
        "thumbnail(s) left behind by a Sonarr/Radarr file upgrade. "
        "This prevents Plex from creating a duplicate library entry "
        "for the show."
    )
    log.info(msg)
    job["messages"].append(msg)


def _maybe_schedule_plex_refresh(folder: Path, settings, job: dict, log) -> None:
    """Fire-and-forget task that asks Plex to rescan ``folder`` after a
    user-configured delay. Never raises into the build pipeline.
    """
    if not (settings.plex_auto_refresh and settings.plex_url and settings.plex_token):
        return
    delay = max(0, int(settings.plex_refresh_delay_seconds or 0))

    async def _do_refresh() -> None:
        try:
            res = await _plex_refresh_for_folder(
                str(folder), delay_seconds=delay, settings=settings,
            )
            if res.get("refreshed"):
                strategy = res.get("strategy") or "refresh"
                if strategy == "metadata-refresh":
                    msg = (
                        f"Plex metadata refresh sent for {res.get('item_title') or 'item'} "
                        f"(ratingKey={res.get('rating_key')}) in section "
                        f"{res.get('section_title')!r}"
                    )
                else:
                    msg = (
                        f"Plex partial scan queued for section {res.get('section_title')!r} "
                        f"path={res.get('translated_path')} (item not yet indexed; "
                        f"NFO won't be re-read until Plex finishes scanning)"
                    )
                log.info(msg)
                job["messages"].append(msg)
                if res.get("error") and strategy != "metadata-refresh":
                    job["messages"].append(f"Plex note: {res['error']}")
            elif res.get("error"):
                log.warning("Plex auto-refresh skipped: {}", res["error"])
                job["messages"].append(f"Plex auto-refresh skipped: {res['error']}")
        except Exception as e:
            log.warning("Plex auto-refresh task crashed: {}", e)

    try:
        asyncio.create_task(_do_refresh())
    except RuntimeError:
        # No running loop (shouldn't happen — builder runs on the loop)
        log.warning("Plex auto-refresh: no running event loop, skipping")


# ---- Movie -----------------------------------------------------------------

async def build_movie(folder: Path, *, force: bool = False,
                      language: Optional[str] = None,
                      _jid: Optional[str] = None) -> str:
    settings = get_user_settings()
    lang = language or settings.preferred_language
    fallbacks = settings.fallback_languages or [lang]
    jid = _jid or _new_job("movie", str(folder))
    log = job_logger(jid)
    job = _jobs[jid]
    binding = db.get_binding(str(folder))
    library_name = folder.parent.name
    lib_source = effective_metadata_source(library_name)
    if binding and binding["provider"] == "tmdb":
        return await _build_movie_tmdb(folder, binding, settings, lang, fallbacks,
                                       force=force, jid=jid, log=log, job=job)
    if not binding and lib_source == "tmdb":
        log.info("Library {!r} resolved to TMDB metadata source", library_name)
        return await _build_movie_tmdb(folder, None, settings, lang, fallbacks,
                                       force=force, jid=jid, log=log, job=job)
    try:
        if binding and binding["provider"] == "tvdb":
            client = get_client()
            data = await client.movie_extended(binding["external_id"], force=force)
        else:
            data = await auto_match_movie(folder, language=lang,
                                          threshold=settings.auto_match_threshold)
        if not data:
            job["status"] = "failed"
            job["messages"].append("No TVDB match. Use manual matching.")
            return jid

        videos = [f for f in folder.iterdir() if f.is_file() and is_video(f)]
        if not videos:
            job["status"] = "failed"
            job["messages"].append("No video file in folder")
            return jid
        main = videos[0]
        # v0.11.8: report progress for movie builds too. One movie folder =
        # one NFO + artwork pass, surfaced as a 1/1 progress in the Jobs view.
        job["total"] = 1
        client = get_client()
        movie_translation = await client.best_translation(
            "movies", data["id"], lang, fallbacks, force=force
        )
        if movie_translation:
            log.info("Movie translation resolved to language={}",
                     movie_translation.get("_resolved_language"))
        nfo_overrides = db.get_nfo_overrides(str(folder))
        preferred_overrides = await resolve_preferred_artwork_movie(
            settings=settings,
            bound_provider="tvdb",
            tvdb_data=data,
            prefer_languages=[lang, *fallbacks],
            force=force,
            manual_secondary_id=_manual_secondary_id(binding, "tmdb"),
        )
        if preferred_overrides:
            log.info("Preferred artwork source override applied for {} slot(s): {}",
                     len(preferred_overrides), sorted(preferred_overrides.keys()))
        # v0.11.15: backfill missing actor portraits from /people/{id}.
        await _hydrate_tvdb_character_thumbs(
            data.get("characters"), log=log, force=force
        )
        nfo_text = build_movie_nfo(
            data, language=lang, fallbacks=fallbacks,
            translation=movie_translation,
            folder_path=str(folder),
            overrides=nfo_overrides,
            preferred_overrides=preferred_overrides,
            manual_secondary=_manual_secondary_tuple(binding),
        )
        main.with_suffix(".nfo").write_text(nfo_text, encoding="utf-8")
        job["progress"] += 1

        # Artwork — direct canonical files in the movie folder.
        try:
            await download_movie_canonical(
                folder, data, data.get("artworks") or [],
                prefer_languages=[lang, *fallbacks],
                force=force,
                preferred_overrides=preferred_overrides,
            )
        except Exception as e:
            log.warning("Movie artwork: {}", e)
        # v0.11.17: see series build for the rationale on `.actors/`.
        try:
            await _download_actor_portraits_tvdb(
                folder, data.get("characters"), log=log, force=force,
            )
        except Exception as e:
            log.warning("Actor portrait download (TVDB movie): {}", e)
        _maybe_sweep_orphans(folder, "movie", settings, job, log)
        scan_movie_folder(folder, library=folder.parent.name)
        db.upsert_item_state(str(folder), last_built=int(time.time()))
        try:
            write_sidecar(folder)
        except Exception as se:
            log.warning("sidecar write failed: {}", se)
        job["status"] = "completed"
        _maybe_schedule_plex_refresh(folder, settings, job, log)
    except Exception as e:
        job["status"] = "failed"
        job["messages"].append(str(e))
        log.exception("Movie build failed: {}", e)
    finally:
        job["finished_at"] = int(time.time())
        sink = close_job_logger(log)
        if sink:
            job["log_file"] = str(sink)
    return jid


# ---- TMDB build paths ------------------------------------------------------
#
# These mirror the TVDB paths above but consume TMDB v3 JSON. They reuse the
# canonical artwork download for posters/backgrounds (which simply downloads
# whatever URLs we hand it) and skip per-season poster detection from TVDB
# entirely — we use TMDB's per-season image API instead.

import httpx as _httpx  # local alias to avoid touching top imports


async def _download_url(url: Optional[str], dest: Path, *, force: bool) -> bool:
    """Download `url` to `dest`, always overwriting any existing file.

    `force` is preserved on the signature for API compatibility but is no
    longer required — every build refreshes the on-disk artwork so users
    don't have to delete files manually.
    """
    _ = force
    if not url:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with _httpx.AsyncClient(headers={"User-Agent": "plex-nfo-builder/0.5"}) as c:
            async with c.stream("GET", url, timeout=60.0) as r:
                if r.status_code != 200:
                    logger.warning("Artwork {} -> HTTP {}", url, r.status_code)
                    return False
                tmp = dest.with_suffix(dest.suffix + ".part")
                with tmp.open("wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)
                tmp.replace(dest)
        return True
    except Exception as e:
        logger.warning("Artwork download failed for {}: {}", url, e)
        return False


def _selections_or(folder: Path, slot: str, fallback: Optional[str]) -> Optional[str]:
    sels = db.get_artwork_selections(str(folder))
    sel = sels.get(slot)
    if sel and sel.get("url"):
        return sel["url"]
    return fallback


def _pick_art(folder: Path, slot: str, preferred_overrides: dict,
              fallback: Optional[str]) -> Optional[str]:
    """Priority: user per-folder selection > preferred-source override > fallback."""
    sels = db.get_artwork_selections(str(folder))
    sel = sels.get(slot)
    if sel and sel.get("url"):
        return sel["url"]
    pv = (preferred_overrides or {}).get(slot)
    if pv:
        return pv
    return fallback


async def _build_series_tmdb(folder: Path, binding, settings, lang: str,
                             fallbacks: list[str], *, force: bool,
                             jid: str, log, job: dict) -> str:
    try:
        client = get_tmdb_client()
        if binding:
            try:
                data = await client.tv_details(binding["external_id"], language=lang, force=force)
            except Exception as e:
                # Mirror of the movie path's self-heal: if a stale binding
                # claims kind="series" for a movie id, swap it. v0.9.2.
                msg = str(e)
                if "404" not in msg:
                    raise
                log.warning("TMDB tv_details {} failed (404) \u2014 retrying as movie id",
                            binding["external_id"])
                mv = await client.movie_details(binding["external_id"], language=lang, force=force)
                if mv:
                    db.upsert_binding(
                        str(folder), "movie", "tmdb", str(mv.get("id")),
                        title=mv.get("title") or mv.get("name"),
                        year=binding["year"],
                        language=binding["language"], respect_lock=True,
                    )
                    log.info("Rebound {} as movie (tmdb id {}) and continuing build",
                             folder.name, mv.get("id"))
                    new_binding = db.get_binding(str(folder))
                    return await _build_movie_tmdb(
                        folder, new_binding, settings, lang, fallbacks,
                        force=force, jid=jid, log=log, job=job,
                    )
                raise
        else:
            data = await auto_match_series_tmdb(folder, language=lang,
                                                threshold=settings.auto_match_threshold)
        if not data:
            job["status"] = "failed"
            job["messages"].append("No TMDB match. Use manual matching.")
            log.error("No TMDB match for {}", folder.name)
            return jid

        # Series NFO
        nfo_overrides = db.get_nfo_overrides(str(folder))
        # We only fetch seasons whose folders exist locally.
        local_seasons: dict[int, list] = {}
        for sd in detect_season_dirs(folder):
            snum = season_number_from_dir(sd.name)
            local_seasons[snum] = list(list_season_episodes(sd))
        # v0.11.8: surface job progress for TMDB builds the same way the TVDB
        # path does. Total = 1 (tvshow.nfo) + every local episode file we'll
        # try to write a per-episode .nfo for. Without this the Jobs view
        # always renders "0/0" for TMDB-bound shows even though episodes
        # are being written correctly underneath.
        _tmdb_total_eps = sum(len(v) for v in local_seasons.values())
        job["total"] = 1 + _tmdb_total_eps
        preferred_overrides = await resolve_preferred_artwork_series(
            settings=settings,
            bound_provider="tmdb",
            tmdb_tv=data,
            local_season_numbers=list(local_seasons.keys()),
            prefer_languages=[lang, *fallbacks],
            force=force,
            manual_secondary_id=_manual_secondary_id(binding, "tvdb"),
        )
        if preferred_overrides:
            log.info("Preferred artwork source override applied for {} slot(s): {}",
                     len(preferred_overrides), sorted(preferred_overrides.keys()))
        nfo_text = build_series_nfo_tmdb(data, language=lang, fallbacks=fallbacks,
                                         folder_path=str(folder),
                                         extra_artwork={
            "poster": _pick_art(folder, "poster", preferred_overrides, None),
            "background": _pick_art(folder, "background", preferred_overrides, None),
            "banner": _pick_art(folder, "banner", preferred_overrides, None),
            "clearlogo": _pick_art(folder, "clearlogo", preferred_overrides, None),
        },
                                         overrides=nfo_overrides,
                                         manual_secondary=_manual_secondary_tuple(binding))
        (folder / "tvshow.nfo").write_text(nfo_text, encoding="utf-8")
        job["progress"] += 1
        log.info("Wrote tvshow.nfo (TMDB tv_id={})", data.get("id"))

        unmatched: list[str] = []
        for snum, parsed_list in local_seasons.items():
            try:
                season_data = await client.tv_season(data["id"], snum, language=lang, force=force)
            except Exception as e:
                log.warning("TMDB tv_season {}/{} failed: {}", data.get("id"), snum, e)
                continue
            ep_by_num: dict[int, dict] = {}
            for ep in season_data.get("episodes") or []:
                if ep.get("episode_number") is not None:
                    ep_by_num[int(ep["episode_number"])] = ep
            # Write a season.nfo from TMDB season payload (overrides take priority).
            try:
                season_scope_key = f"season-{int(snum):02d}"
                if snum >= 0 and (nfo_overrides.get(season_scope_key) or season_data.get("name") or season_data.get("overview")):
                    season_nfo = build_season_nfo(
                        int(snum),
                        base_title=season_data.get("name"),
                        base_plot=season_data.get("overview"),
                        base_aired=season_data.get("air_date"),
                        overrides=nfo_overrides,
                        external_id=str(season_data.get("id")) if season_data.get("id") else None,
                        provider="tmdb",
                    )
                    season_dir = folder / f"Season {int(snum):02d}"
                    if not season_dir.exists():
                        # fall back to whichever local dir we read episodes from
                        for sd in detect_season_dirs(folder):
                            if season_number_from_dir(sd.name) == int(snum):
                                season_dir = sd
                                break
                    if season_dir.exists():
                        (season_dir / "season.nfo").write_text(season_nfo, encoding="utf-8")
            except Exception as se:
                log.warning("TMDB season.nfo for s{:02d} failed: {}", snum, se)
            for parsed in parsed_list:
                # v0.9.0 parity: skip un-parseable placeholder rows but still
                # advance progress so the Jobs view counter doesn't stall.
                if not getattr(parsed, "parsed", True):
                    unmatched.append(parsed.path.name)
                    job["progress"] += 1
                    continue
                ep = ep_by_num.get(int(parsed.episode))
                if not ep:
                    unmatched.append(parsed.path.name)
                    job["progress"] += 1
                    continue
                ep_text = build_episode_nfo_tmdb(ep, language=lang, fallbacks=fallbacks,
                                                  overrides=nfo_overrides)
                parsed.path.with_suffix(".nfo").write_text(ep_text, encoding="utf-8")
                # Episode thumbnail next to the file.
                #
                # v0.11.9: TMDB ships multiple stills per episode and the
                # Overrides tab now lets the user pick which one. The chosen
                # URL is stored as ``episode-thumb-<external_id>`` in the
                # ``artwork_selections`` table; we fall back to the default
                # ``still_path`` when no override exists.
                ep_id = ep.get("id")
                slot = f"episode-thumb-{ep_id}" if ep_id is not None else None
                user_url: Optional[str] = None
                if slot:
                    sels = db.get_artwork_selections(str(folder))
                    sel = sels.get(slot)
                    if sel and sel.get("url"):
                        user_url = sel["url"]
                still = ep.get("still_path")
                thumb_url = user_url or (tmdb_image_url(still, "original") if still else None)
                if thumb_url:
                    dest = parsed.path.with_name(f"{parsed.path.stem}-thumb.jpg")
                    await _download_url(thumb_url, dest, force=force)
                job["progress"] += 1
        if unmatched:
            log.warning("{} unmatched local episodes: {}", len(unmatched), unmatched[:5])
            job["messages"].append(f"{len(unmatched)} episode file(s) unmatched")

        # Series-level artwork: poster + backdrop, plus per-season posters.
        poster = _pick_art(folder, "poster", preferred_overrides,
                           tmdb_image_url(data.get("poster_path"), "original"))
        bg = _pick_art(folder, "background", preferred_overrides,
                       tmdb_image_url(data.get("backdrop_path"), "original"))
        banner = _pick_art(folder, "banner", preferred_overrides, None)
        clearlogo = _pick_art(folder, "clearlogo", preferred_overrides, None)
        await _download_url(poster, folder / "poster.jpg", force=force)
        await _download_url(bg, folder / "background.jpg", force=force)
        if banner:
            await _download_url(banner, folder / "banner.jpg", force=force)
        if clearlogo:
            await _download_url(clearlogo, folder / "clearlogo.png", force=force)
        # v0.11.17: see TVDB series build for the rationale on `.actors/`.
        try:
            tmdb_cast = ((data.get("credits") or {}).get("cast") or [])
            await _download_actor_portraits_tmdb(
                folder, tmdb_cast[:30], log=log, force=force,
            )
        except Exception as e:
            log.warning("Actor portrait download (TMDB series): {}", e)
        # Per-season posters: user selection > preferred-source override > TMDB season poster_path
        for snum, parsed_list in local_seasons.items():
            slot = f"season-{snum:02d}-poster"
            sels = db.get_artwork_selections(str(folder))
            user_sel = sels.get(slot)
            if user_sel and user_sel.get("url"):
                await _download_url(user_sel["url"], folder / season_poster_filename(snum, ".jpg"), force=force)
                continue
            pref_url = (preferred_overrides or {}).get(slot)
            if pref_url:
                await _download_url(pref_url, folder / season_poster_filename(snum, ".jpg"), force=force)
                continue
            try:
                season_data = await client.tv_season(data["id"], snum, language=lang, force=force)
            except Exception:
                continue
            sp = season_data.get("poster_path")
            if sp:
                url = tmdb_image_url(sp, "original")
                await _download_url(url, folder / season_poster_filename(snum, ".jpg"), force=force)

        _maybe_sweep_orphans(folder, "series", settings, job, log)
        scan_series_folder(folder, library=folder.parent.name)
        db.upsert_item_state(str(folder), last_built=int(time.time()))
        try:
            write_sidecar(folder)
        except Exception as se:
            log.warning("sidecar write failed: {}", se)
        job["status"] = "completed"
        log.info("TMDB series build done for {}", folder.name)
        _maybe_schedule_plex_refresh(folder, settings, job, log)
    except Exception as e:
        job["status"] = "failed"
        job["messages"].append(str(e))
        log.exception("TMDB series build failed: {}", e)
    finally:
        job["finished_at"] = int(time.time())
        sink = close_job_logger(log)
        if sink:
            job["log_file"] = str(sink)
    return jid


async def _build_movie_tmdb(folder: Path, binding, settings, lang: str,
                            fallbacks: list[str], *, force: bool,
                            jid: str, log, job: dict) -> str:
    try:
        client = get_tmdb_client()
        if binding:
            try:
                data = await client.movie_details(binding["external_id"], language=lang, force=force)
            except Exception as e:
                # v0.9.2: a stale binding from before v0.9.1 may still claim
                # kind="movie" for what is actually a TV id. If the bound id
                # 404s as a movie, try it as a TV id and rewrite the binding
                # so future builds take the correct path.
                msg = str(e)
                if "404" not in msg:
                    raise
                log.warning("TMDB movie_details {} failed (404) \u2014 retrying as TV id",
                            binding["external_id"])
                tv = await client.tv_details(binding["external_id"], language=lang, force=force)
                if tv:
                    db.upsert_binding(
                        str(folder), "series", "tmdb", str(tv.get("id")),
                        title=tv.get("name"), year=binding["year"],
                        language=binding["language"], respect_lock=True,
                    )
                    log.info("Rebound {} as series (tmdb id {}) and continuing build",
                             folder.name, tv.get("id"))
                    new_binding = db.get_binding(str(folder))
                    return await _build_series_tmdb(
                        folder, new_binding, settings, lang, fallbacks,
                        force=force, jid=jid, log=log, job=job,
                    )
                raise
        else:
            data = await auto_match_movie_tmdb(folder, language=lang,
                                               threshold=settings.auto_match_threshold)
        if not data:
            job["status"] = "failed"
            job["messages"].append("No TMDB match. Use manual matching.")
            return jid

        videos = [f for f in folder.iterdir() if f.is_file() and is_video(f)]
        if not videos:
            job["status"] = "failed"
            job["messages"].append("No video file in folder")
            return jid
        main = videos[0]
        # v0.11.8: report progress for TMDB movie builds too.
        job["total"] = 1
        nfo_overrides = db.get_nfo_overrides(str(folder))
        preferred_overrides = await resolve_preferred_artwork_movie(
            settings=settings,
            bound_provider="tmdb",
            tmdb_mv=data,
            prefer_languages=[lang, *fallbacks],
            force=force,
            manual_secondary_id=_manual_secondary_id(binding, "tvdb"),
        )
        if preferred_overrides:
            log.info("Preferred artwork source override applied for {} slot(s): {}",
                     len(preferred_overrides), sorted(preferred_overrides.keys()))
        nfo_text = build_movie_nfo_tmdb(data, language=lang, fallbacks=fallbacks,
                                        folder_path=str(folder),
                                        extra_artwork={
            "poster": _pick_art(folder, "poster", preferred_overrides, None),
            "background": _pick_art(folder, "background", preferred_overrides, None),
            "banner": _pick_art(folder, "banner", preferred_overrides, None),
        },
                                        overrides=nfo_overrides,
                                        manual_secondary=_manual_secondary_tuple(binding))
        main.with_suffix(".nfo").write_text(nfo_text, encoding="utf-8")
        job["progress"] += 1

        poster = _pick_art(folder, "poster", preferred_overrides,
                           tmdb_image_url(data.get("poster_path"), "original"))
        bg = _pick_art(folder, "background", preferred_overrides,
                       tmdb_image_url(data.get("backdrop_path"), "original"))
        banner = _pick_art(folder, "banner", preferred_overrides, None)
        await _download_url(poster, folder / "poster.jpg", force=force)
        await _download_url(bg, folder / "background.jpg", force=force)
        if banner:
            await _download_url(banner, folder / "banner.jpg", force=force)
        # v0.11.17: see TVDB series build for the rationale on `.actors/`.
        try:
            tmdb_cast = ((data.get("credits") or {}).get("cast") or [])
            await _download_actor_portraits_tmdb(
                folder, tmdb_cast[:30], log=log, force=force,
            )
        except Exception as e:
            log.warning("Actor portrait download (TMDB movie): {}", e)
        _maybe_sweep_orphans(folder, "movie", settings, job, log)
        scan_movie_folder(folder, library=folder.parent.name)
        db.upsert_item_state(str(folder), last_built=int(time.time()))
        try:
            write_sidecar(folder)
        except Exception as se:
            log.warning("sidecar write failed: {}", se)
        job["status"] = "completed"
        _maybe_schedule_plex_refresh(folder, settings, job, log)
    except Exception as e:
        job["status"] = "failed"
        job["messages"].append(str(e))
        log.exception("TMDB movie build failed: {}", e)
    finally:
        job["finished_at"] = int(time.time())
        sink = close_job_logger(log)
        if sink:
            job["log_file"] = str(sink)
    return jid
