"""Snapshots pause automation without overwriting saved preferences or losing events."""
import asyncio
import threading
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app import db
from app.routes import api
from app.services import jobs, snapshot_automation, snapshots
from app.services.scheduler import Scheduler
from app.services.watcher import Watcher


@pytest.fixture
def automation(monkeypatch, tmp_path):
    watcher = Watcher()
    scheduler = Scheduler()
    monkeypatch.setattr(snapshot_automation, "watcher", watcher)
    monkeypatch.setattr(snapshot_automation, "scheduler", scheduler)
    monkeypatch.setattr(jobs, "_jobs", {})
    monkeypatch.setattr(jobs, "_tasks", {})
    monkeypatch.setattr(jobs, "_folders", {})
    monkeypatch.setattr(jobs, "_slots", None)
    monkeypatch.setattr(jobs, "_followups", set())
    monkeypatch.setattr(snapshots, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr("app.services.watcher.effective_watcher_enabled", lambda: True)
    monkeypatch.setattr("app.services.watcher.effective_watcher_debounce_seconds", lambda: 300)
    return watcher, scheduler


def test_pause_defers_watcher_events_and_schedule_runs_without_changing_preferences(automation, monkeypatch, tmp_path):
    watcher, scheduler = automation
    schedule = {"id": 1, "enabled": 1, "cron": "* * * * *", "library": "TV", "action": "scan_only"}
    monkeypatch.setattr(db, "list_schedules", lambda: [schedule])
    monkeypatch.setattr(db, "get_schedule", lambda _: schedule)
    execute = AsyncMock()
    monkeypatch.setattr(scheduler, "_execute", execute)
    process = AsyncMock()
    monkeypatch.setattr(watcher, "_process_folder", process)
    folder = tmp_path / "TV" / "Show"

    async def run():
        watcher._loop = asyncio.get_running_loop()
        watcher._enabled = True
        watcher._observer = object()
        watcher._schedule_debounce("TV", folder, str(folder), True)
        timer = watcher._pending[folder].timer
        async with snapshot_automation.paused_automation():
            assert timer.cancelled()
            assert watcher.status()["paused_for_snapshot"] and not watcher.status()["running"]
            assert watcher.status()["enabled"] and schedule["enabled"] == 1
            watcher._schedule_debounce("TV", folder, str(folder / "episode.mkv"), False)
            watcher._fire_debounce(folder)
            assert folder in watcher._pending and not watcher._running
            assert watcher._pending[folder].timer is None
            await scheduler.tick(datetime.now(timezone.utc))
            execute.assert_not_called()
            with pytest.raises(ValueError, match="paused"):
                scheduler.run_now(1)
        assert not scheduler.paused_for_snapshot and watcher.status()["running"]
        watcher._pending[folder].timer.cancel()
        watcher._fire_debounce(folder)
        await watcher.wait_idle()
        process.assert_awaited_once_with(folder, "TV")
        await scheduler.tick(datetime.now(timezone.utc))
        await scheduler.wait_idle()
        execute.assert_awaited_once()
        assert schedule["enabled"] == 1

    asyncio.run(run())


def test_overlapping_snapshots_preserve_pause_and_disabled_or_stopped_watcher(automation):
    watcher, scheduler = automation

    async def run():
        async with snapshot_automation.paused_automation():
            async with snapshot_automation.paused_automation():
                assert watcher._snapshot_pauses == 2 and scheduler.paused_for_snapshot
            assert watcher._snapshot_pauses == 1 and scheduler.paused_for_snapshot
        assert watcher._snapshot_pauses == 0 and not scheduler.paused_for_snapshot
        assert not watcher._enabled and watcher._observer is None
        # A stop/disable during the pause must not be reversed by its cleanup.
        watcher._enabled = True
        async with snapshot_automation.paused_automation():
            watcher.stop()
        assert not watcher._enabled and watcher._observer is None

    asyncio.run(run())


def test_pause_drains_automation_and_queued_builds_before_copy_without_slot_deadlock(automation, monkeypatch, tmp_path):
    watcher, scheduler = automation
    order = []

    async def build(job_id):
        await asyncio.sleep(0)
        order.append("build")
        return job_id

    async def pipeline():
        await asyncio.sleep(0)
        for number in range(3):
            jobs.start(tmp_path / f"item-{number}", "series", build)
        order.append("pipeline")

    def copy(name, job):
        assert watcher._snapshot_pauses and scheduler.paused_for_snapshot
        assert order.count("pipeline") == 2 and order.count("build") >= 3
        order.append("copy")
        return {"filename": "backup.zip", "file_count": 0}

    monkeypatch.setattr(snapshots, "create_snapshot", copy)

    async def run():
        watcher._running[tmp_path] = asyncio.create_task(pipeline())
        scheduler._running[1] = asyncio.create_task(pipeline())
        first = snapshots.start_snapshot("TV")
        second = snapshots.start_snapshot("Movies")
        await asyncio.wait_for(asyncio.gather(jobs.wait_build(first), jobs.wait_build(second)), timeout=2)
        assert order.count("copy") == 2
        assert all(jobs.get_job(job_id)["status"] == "completed" for job_id in (first, second))
        assert not watcher._snapshot_pauses and not scheduler.paused_for_snapshot
        await jobs.shutdown_builds()

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_snapshot_always_resumes_after_worker_finishes(automation, monkeypatch, outcome):
    watcher, scheduler = automation
    started = threading.Event()
    release = threading.Event()

    def copy(name, job):
        assert watcher._snapshot_pauses and scheduler.paused_for_snapshot
        started.set()
        assert release.wait(timeout=3)
        if outcome == "failure":
            raise OSError("disk full")
        return {"filename": "backup.zip", "file_count": 0}

    monkeypatch.setattr(snapshots, "create_snapshot", copy)

    async def run():
        job_id = snapshots.start_snapshot("TV")
        try:
            assert await asyncio.to_thread(started.wait, 2)
            if outcome == "cancel":
                jobs._tasks[job_id].cancel()
                await asyncio.sleep(0)
                # Cancellation drains the worker before resuming automation.
                assert watcher._snapshot_pauses and scheduler.paused_for_snapshot
        finally:
            release.set()
        await asyncio.gather(jobs.wait_build(job_id), return_exceptions=True)
        assert not watcher._snapshot_pauses and not scheduler.paused_for_snapshot
        assert jobs.get_job(job_id)["status"] == {
            "success": "completed", "failure": "error", "cancel": "cancelled",
        }[outcome]
        assert not watcher._enabled  # A watcher disabled before the snapshot stays disabled.
        await jobs.shutdown_builds()

    asyncio.run(run())


def test_manual_schedule_endpoint_reports_temporary_pause(automation, monkeypatch):
    _, scheduler = automation
    monkeypatch.setattr(api, "_scheduler", scheduler)
    monkeypatch.setattr(db, "get_schedule", lambda _: {"id": 1})

    async def run():
        async with snapshot_automation.paused_automation():
            with pytest.raises(api.HTTPException) as error:
                await api.schedules_run(1)
            assert error.value.status_code == 409

    asyncio.run(run())
