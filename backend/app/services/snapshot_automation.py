"""Temporarily suspend automation without changing saved user preferences."""
import asyncio
from contextlib import asynccontextmanager

from . import jobs
from .scheduler import scheduler
from .watcher import watcher


@asynccontextmanager
async def paused_automation():
    # Counters keep overlapping library snapshots paused until the last exits.
    # Watchdog can still collect events, but no new pipeline is dispatched.
    watcher.pause_for_snapshot()
    scheduler.pause_for_snapshot()
    try:
        await asyncio.gather(watcher.wait_idle(), scheduler.wait_idle())
        await jobs.wait_for_builds()
        yield
    finally:
        try:
            watcher.resume_after_snapshot()
        finally:
            scheduler.resume_after_snapshot()
