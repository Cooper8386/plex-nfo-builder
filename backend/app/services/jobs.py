"""In-process build ownership, bounded concurrency, and shutdown."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

from loguru import logger

_jobs: dict[str, dict] = {}
_tasks: dict[str, asyncio.Task] = {}
_folders: dict[Path, str] = {}
_slots: asyncio.Semaphore | None = None
_followups: set[asyncio.Task] = set()


def new_job(kind: str, folder: str) -> str:
    # Keep only the history exposed by /jobs, without evicting running jobs.
    completed = [key for key, job in _jobs.items() if job["finished_at"] is not None]
    for key in completed[:-199]:
        _jobs.pop(key)
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "id": job_id, "kind": kind, "folder": folder, "status": "running",
        "progress": 0, "total": 0, "started_at": int(time.time()),
        "finished_at": None, "messages": [],
    }
    return job_id


def get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def list_jobs() -> list[dict]:
    return sorted(_jobs.values(), key=lambda job: job["started_at"], reverse=True)[:200]


def start(folder: Path, kind: str, build: Callable[[str], Awaitable[str]]) -> str:
    """Coalesce duplicate clicks and cap builds from every API/watcher/schedule."""
    global _slots
    folder = folder.resolve()
    existing = _folders.get(folder)
    if existing and existing in _tasks and not _tasks[existing].done():
        return existing
    if _slots is None:
        # ponytail: one process-wide cap; make configurable when measured workloads require it.
        _slots = asyncio.Semaphore(2)
    slots = _slots
    job_id = new_job(kind, str(folder))
    _jobs[job_id]["status"] = "queued"

    async def run() -> None:
        try:
            async with slots:
                _jobs[job_id]["status"] = "running"
                with logger.contextualize(job=job_id):
                    await build(job_id)
        except asyncio.CancelledError:
            _jobs[job_id].update(status="cancelled", finished_at=int(time.time()))
            raise
        except Exception as error:
            logger.exception("Build {} failed ({})", job_id, type(error).__name__)
            _jobs[job_id].update(status="error", finished_at=int(time.time()))
            _jobs[job_id]["messages"].append("Build failed before completion. Check the application log.")
        finally:
            if _folders.get(folder) == job_id:
                _folders.pop(folder, None)

    task = asyncio.create_task(run(), name=f"build-{job_id}")
    _tasks[job_id] = task
    _folders[folder] = job_id
    task.add_done_callback(lambda _: _tasks.pop(job_id, None))
    return job_id


async def wait_build(job_id: str) -> None:
    if task := _tasks.get(job_id):
        # A watcher reload must not cancel a separately owned file write.
        await asyncio.shield(task)


def start_followup(operation: Coroutine[Any, Any, None]) -> None:
    """Own delayed Plex refreshes so none outlive application shutdown."""
    task = asyncio.create_task(operation)
    _followups.add(task)
    task.add_done_callback(_followups.discard)


async def shutdown_builds() -> None:
    global _slots
    tasks = [*list(_tasks.values()), *list(_followups)]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for job in _jobs.values():
        if job["finished_at"] is None:
            job.update(status="cancelled", finished_at=int(time.time()))
    _tasks.clear()
    _folders.clear()
    _followups.clear()
    _slots = None
