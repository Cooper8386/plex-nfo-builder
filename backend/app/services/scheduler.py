"""Scheduler service (v0.8.0).

Lightweight asyncio-based scheduler with a minimal 5-field cron parser. We
deliberately avoid pulling in APScheduler/croniter to keep the runtime
dependency footprint small — the parser supports the small subset users
actually configure (numbers, ``*``, ``*/N`` step, ``a-b`` ranges and
``a,b,c`` lists) which covers every preset surfaced in the UI.

Cron expressions are interpreted in **UTC**.

Schedules are persisted in the ``schedules`` table (see ``db.py``). At
startup the FastAPI app calls :func:`Scheduler.start`; on shutdown it calls
:func:`Scheduler.stop`. The scheduler ticks once per minute, finds every
enabled row whose cron matches the *current* minute and hasn't already run
this minute, and dispatches the configured action.

Actions:
    * ``scan_only`` — rescan the library so new folders show up in the UI.
    * ``match_only`` — auto-match every still-unmatched folder.
    * ``build_only`` — build/rebuild every folder that isn't ``complete``
      *and* has detected new local episodes since the last build.
    * ``match_and_build`` — match unmatched, then build the new + dirty
      folders.
    * ``full`` — scan, match, build (the everything-button).

A schedule with ``library = NULL`` runs against every enabled library.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

from .. import db
from ..config import MEDIA_ROOT, effective_metadata_source, get_user_settings
from . import builder as build_svc
from . import matcher as matcher_svc
from . import scanner as scanner_svc
from . import sidecar as sidecar_svc

# ---------------------------------------------------------------------------
# Cron parser
# ---------------------------------------------------------------------------

# (min_value, max_value) for each cron field.
_FIELD_RANGES: tuple[tuple[int, int], ...] = (
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 6),    # day of week (0 = Sunday)
)


def _parse_field(field: str, lo: int, hi: int) -> set[int]:
    """Parse a single cron field into the set of matching integers.

    Supports ``*``, ``*/N``, ``a-b``, ``a-b/N``, comma lists, and bare
    numbers. Raises ``ValueError`` for anything we don't understand so the
    caller can mark the schedule as broken instead of silently mis-firing.
    """
    if field == "" or field is None:
        raise ValueError("empty cron field")
    out: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty cron field segment")
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError("cron step must be positive")
        else:
            base = part
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(base)
        if start < lo or end > hi or start > end:
            raise ValueError(f"cron value out of range: {part}")
        out.update(range(start, end + 1, step))
    return out


def _expand_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron must have 5 fields, got {len(parts)}: {expr!r}")
    return tuple(_parse_field(p, lo, hi) for p, (lo, hi) in zip(parts, _FIELD_RANGES))


def cron_matches(expr: str, dt: datetime) -> bool:
    """Return True iff ``dt`` (UTC) matches the cron expression."""
    minute, hour, dom, month, dow = _expand_cron(expr)
    if dt.minute not in minute or dt.hour not in hour or dt.month not in month:
        return False
    # cron weekday: 0=Sunday … 6=Saturday. Python's weekday(): 0=Monday.
    py_dow = (dt.weekday() + 1) % 7
    # Per POSIX, when both DOM and DOW are restricted (not '*'), match if
    # *either* matches. We approximate "is restricted" by length < full set.
    dom_full = len(dom) == 31
    dow_full = len(dow) == 7
    if dom_full and dow_full:
        return True
    if dom_full:
        return py_dow in dow
    if dow_full:
        return dt.day in dom
    return dt.day in dom or py_dow in dow


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


@dataclass
class _RunContext:
    sched_id: int
    library: Optional[str]
    action: str


_VALID_ACTIONS = {"scan_only", "match_only", "build_only", "match_and_build", "full"}


class Scheduler:
    """Owns the minute-by-minute tick loop and per-schedule run state."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        # Track the last "minute-key" (UTC) we fired each schedule on so a
        # second tick within the same minute doesn't double-run.
        self._last_fire: dict[int, str] = {}
        # Active per-schedule asyncio.Task so manual triggers don't pile up.
        self._running: dict[int, asyncio.Task] = {}

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="nfo-scheduler")
        logger.info("Scheduler started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None
        # Don't await running jobs; let them finish in the background. The
        # FastAPI shutdown hook is best-effort and we'd rather not block it.
        logger.info("Scheduler stopped")

    # -- main tick loop -----------------------------------------------------

    async def _loop(self) -> None:
        # Sleep until the next minute boundary so cron fires at :00 seconds.
        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            try:
                await self.tick(now)
            except Exception as e:
                logger.exception("Scheduler tick failed: {}", e)
            # Sleep ~60s (re-aligned to wall clock) — break early on stop.
            sleep_for = 60 - datetime.now(timezone.utc).second
            if sleep_for <= 0:
                sleep_for = 60
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                continue

    async def tick(self, now: datetime) -> None:
        """Inspect persisted schedules and run anything due at ``now``."""
        rows = db.list_schedules()
        if not rows:
            return
        # Floor to the minute so cron_matches sees a stable value.
        floor = now.replace(second=0, microsecond=0)
        key = floor.isoformat()
        for row in rows:
            d = dict(row)
            if not int(d.get("enabled") or 0):
                continue
            sched_id = int(d["id"])
            if self._last_fire.get(sched_id) == key:
                continue
            try:
                if not cron_matches(d.get("cron") or "", floor):
                    continue
            except ValueError as e:
                logger.warning("Schedule {} has invalid cron {!r}: {}",
                               sched_id, d.get("cron"), e)
                db.update_schedule_run(sched_id, last_run=int(time.time()),
                                       last_status="error",
                                       last_message=f"invalid cron: {e}")
                self._last_fire[sched_id] = key
                continue
            self._last_fire[sched_id] = key
            self._launch(sched_id, d.get("library"), d.get("action") or "")

    # -- public manual trigger ---------------------------------------------

    def run_now(self, sched_id: int) -> bool:
        """Trigger a schedule immediately. Returns False if no such row."""
        row = db.get_schedule(sched_id)
        if not row:
            return False
        d = dict(row)
        self._launch(sched_id, d.get("library"), d.get("action") or "")
        return True

    # -- dispatching --------------------------------------------------------

    def _launch(self, sched_id: int, library: Optional[str], action: str) -> None:
        existing = self._running.get(sched_id)
        if existing and not existing.done():
            logger.info("Schedule {} already running, skip", sched_id)
            return
        ctx = _RunContext(sched_id=sched_id, library=library, action=action)
        task = asyncio.create_task(self._execute(ctx),
                                   name=f"schedule-{sched_id}")
        self._running[sched_id] = task

    async def _execute(self, ctx: _RunContext) -> None:
        sched_id = ctx.sched_id
        action = ctx.action
        if action not in _VALID_ACTIONS:
            db.update_schedule_run(sched_id, last_run=int(time.time()),
                                   last_status="error",
                                   last_message=f"unknown action {action!r}")
            return
        # Mark "running" up front so the UI shows progress immediately.
        db.update_schedule_run(sched_id, last_run=int(time.time()),
                               last_status="running",
                               last_message=None)
        try:
            libraries = self._target_libraries(ctx.library)
            summary = await self._run_action(action, libraries)
            db.update_schedule_run(sched_id, last_run=int(time.time()),
                                   last_status="ok",
                                   last_message=summary)
            logger.info("Schedule {} ({}) completed: {}", sched_id, action, summary)
        except Exception as e:
            logger.exception("Schedule {} failed: {}", sched_id, e)
            db.update_schedule_run(sched_id, last_run=int(time.time()),
                                   last_status="error",
                                   last_message=str(e))

    def _target_libraries(self, library: Optional[str]) -> list[str]:
        rows = db.list_libraries()
        enabled = [r["name"] for r in rows if int(r["enabled"] or 0)]
        if library:
            return [library] if library in enabled else []
        return enabled

    # -- action handlers ----------------------------------------------------

    async def _run_action(self, action: str, libraries: list[str]) -> str:
        if not libraries:
            return "no enabled libraries"
        scanned = matched = built = 0
        for lib in libraries:
            if action in ("scan_only", "full"):
                scanned += await asyncio.to_thread(scanner_svc.scan_library, lib)
            else:
                # Match/build still need item_state to be up to date.
                await asyncio.to_thread(scanner_svc.scan_library, lib)
            if action in ("match_only", "match_and_build", "full"):
                matched += await self._match_unmatched(lib)
            if action in ("build_only", "match_and_build", "full"):
                built += await self._build_changed(lib)
        return (
            f"libraries={len(libraries)} scanned={scanned} "
            f"matched={matched} built={built}"
        )

    async def _match_unmatched(self, library: str) -> int:
        """Auto-match every folder in ``library`` that has no external_id."""
        rows = db.list_item_state(library=library)
        targets: list[Path] = []
        for r in rows:
            d = dict(r)
            if d.get("external_id"):
                continue
            try:
                p = Path(d["folder_path"])
            except Exception:
                continue
            if not p.exists():
                continue
            # Respect locked bindings.
            b = db.get_binding(str(p))
            if b and int(b["source_locked"] or 0) == 1:
                continue
            targets.append(p)
        if not targets:
            return 0
        settings = get_user_settings()
        lang = settings.preferred_language
        sem = asyncio.Semaphore(4)
        matched = 0

        async def _one(p: Path) -> bool:
            nonlocal matched
            async with sem:
                kind = _detect_kind(p)
                source = effective_metadata_source(p.parent.name)
                try:
                    if source == "tmdb":
                        if kind == "series":
                            data = await matcher_svc.auto_match_series_tmdb(
                                p, language=lang,
                                threshold=settings.auto_match_threshold)
                        else:
                            data = await matcher_svc.auto_match_movie_tmdb(
                                p, language=lang,
                                threshold=settings.auto_match_threshold)
                    else:
                        if kind == "series":
                            data = await matcher_svc.auto_match_series(
                                p, language=lang,
                                threshold=settings.auto_match_threshold)
                        else:
                            data = await matcher_svc.auto_match_movie(
                                p, language=lang,
                                threshold=settings.auto_match_threshold)
                except Exception as e:
                    logger.warning("scheduled auto-match {} failed: {}", p, e)
                    return False
                if not data:
                    return False
                # Re-scan + sidecar so subsequent build pass sees the binding.
                try:
                    if kind == "series":
                        scanner_svc.scan_series_folder(p, library=p.parent.name)
                    else:
                        scanner_svc.scan_movie_folder(p, library=p.parent.name)
                    sidecar_svc.write_sidecar(p)
                except Exception as e:
                    logger.warning("post-match rescan {} failed: {}", p, e)
                matched += 1
                return True

        await asyncio.gather(*[_one(p) for p in targets])
        return matched

    async def _build_changed(self, library: str) -> int:
        """Queue builds for matched folders that are missing/changed.

        "Changed" = NFO status is not ``complete`` OR (for series) the local
        episode count differs from what was recorded last time. This keeps
        runtime bounded: untouched, fully-built shows are skipped, but a new
        season/episode triggers a rebuild automatically.
        """
        rows = db.list_item_state(library=library)
        queued = 0
        for r in rows:
            d = dict(r)
            if not d.get("external_id"):
                continue  # unmatched — skip
            try:
                p = Path(d["folder_path"])
            except Exception:
                continue
            if not p.exists():
                continue
            kind = d.get("kind") or _detect_kind(p)
            status = d.get("nfo_status") or ""
            needs_build = status != "complete"
            # Re-scan to refresh local episode count before deciding.
            if not needs_build and kind == "series":
                try:
                    fresh = scanner_svc.scan_series_folder(p, library=library)
                    prior = d.get("episode_count_local") or 0
                    if fresh.episode_count != prior:
                        needs_build = True
                except Exception as e:
                    logger.warning("pre-build rescan {} failed: {}", p, e)
            if not needs_build:
                continue
            try:
                build_svc.start_build(p, kind, force=False)
                queued += 1
            except Exception as e:
                logger.warning("scheduled build {} failed: {}", p, e)
        return queued


def _detect_kind(p: Path) -> str:
    lib_name = p.parent.name
    rows = db.list_libraries()
    lib_kind = next((r["kind"] for r in rows if r["name"] == lib_name), "tv")
    return "movie" if lib_kind == "movies" else "series"


# Module-level singleton so other modules (api routes, main) can share state.
scheduler = Scheduler()
