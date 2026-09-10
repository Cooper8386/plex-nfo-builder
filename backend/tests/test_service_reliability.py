import asyncio
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest

from app import config, db
from app.logging_setup import _RedactTokenFilter, redact_tokens
from app.services import builder, jobs, watcher
from app.services.provider_http import retry_delay


def test_settings_atomic_failure_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    config.UserSettings(preferred_language="jpn").save(path)
    previous = path.read_bytes()

    def fail_replace(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(config.os, "replace", fail_replace)
    with pytest.raises(OSError):
        config.UserSettings(preferred_language="eng").save(path)
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


def test_nfo_writes_preserve_foreign_files_until_explicit_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "MEDIA_ROOT", tmp_path)
    settings = config.UserSettings(overwrite_foreign_nfo=False)
    monkeypatch.setattr(builder, "get_user_settings", lambda: settings)
    path = tmp_path / "tvshow.nfo"
    original = b"<tvshow><title>Other tool's metadata</title></tvshow>"
    path.write_bytes(original)
    builder._atomic_write_text(path, "replacement")
    assert path.read_bytes() == original
    settings.overwrite_foreign_nfo = True
    builder._atomic_write_text(path, "<!-- plex-nfo-builder -->\n<tvshow/>")
    settings.overwrite_foreign_nfo = False
    builder._atomic_write_text(path, "<!-- plex-nfo-builder -->\n<updated/>")
    assert path.read_text() == "<!-- plex-nfo-builder -->\n<updated/>"


def test_nfo_write_cannot_escape_media_root(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "MEDIA_ROOT", tmp_path / "media")
    with pytest.raises(ValueError):
        builder._atomic_write_text(tmp_path / "outside.nfo", "forbidden")
    assert not (tmp_path / "outside.nfo").exists()


def test_database_initialization_is_single_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "_conn", None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        connections = list(pool.map(lambda _: db.conn(), range(32)))
    assert len({id(connection) for connection in connections}) == 1
    connections[0].close()


def test_database_transaction_rolls_back_related_changes(monkeypatch):
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    db._init_schema(connection)
    monkeypatch.setattr(db, "_conn", connection)
    db.upsert_binding("folder", "series", "tvdb", "1")
    with pytest.raises(RuntimeError), db.transaction():
        db.upsert_binding("folder", "series", "tvdb", "2")
        db.set_nfo_override("folder", "series", "title", "Changed")
        raise RuntimeError("restore interrupted")
    assert db.get_binding("folder")["external_id"] == "1"
    assert db.get_nfo_overrides("folder") == {}
    connection.close()


def test_rebinding_clears_stale_secondary_provider_ids(monkeypatch):
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    db._init_schema(connection)
    monkeypatch.setattr(db, "_conn", connection)
    db.upsert_binding("folder", "series", "tvdb", "1")
    db.set_binding_secondary("folder", "tmdb", "2")
    db.upsert_binding("folder", "series", "tvdb", "1", title="Refreshed")
    assert db.get_binding("folder")["secondary_external_id"] == "2"
    db.upsert_binding("folder", "series", "tmdb", "2")
    assert db.get_binding("folder")["secondary_provider"] is None
    connection.close()


def test_watcher_waits_for_actual_stability(monkeypatch, tmp_path):
    instance = watcher.Watcher()
    monkeypatch.setattr(watcher.asyncio, "sleep", AsyncMock())
    snapshots = iter([(1, 1), (2, 2), (3, 3)])
    monkeypatch.setattr(watcher, "_snapshot_folder", lambda folder: next(snapshots))
    assert asyncio.run(instance._is_stable(tmp_path)) is False
    monkeypatch.setattr(watcher, "_snapshot_folder", lambda folder: (3, 3))
    assert asyncio.run(instance._is_stable(tmp_path)) is True


def test_watcher_requeues_import_during_active_run(monkeypatch, tmp_path):
    async def run():
        instance = watcher.Watcher()
        instance._loop = asyncio.get_running_loop()
        instance._enabled = True
        task = asyncio.create_task(asyncio.Event().wait())
        instance._running[tmp_path] = task
        instance._pending[tmp_path] = watcher._PendingFolder(library="TV")
        monkeypatch.setattr(watcher, "effective_watcher_debounce_seconds", lambda: 30)
        instance._fire_debounce(tmp_path)
        assert tmp_path in instance._pending
        await instance.aclose()
        assert task.cancelled()
        assert not instance._pending

    asyncio.run(run())


def test_watcher_queues_build_on_event_loop_with_keyword_force(monkeypatch, tmp_path):
    queued = []

    def start(folder, kind, *, force):
        assert asyncio.get_running_loop().is_running()
        queued.append((folder, kind, force))
        return "job"

    monkeypatch.setattr(watcher.build_svc, "start_build", start)
    monkeypatch.setattr(watcher.build_svc, "wait_build", AsyncMock())
    asyncio.run(watcher.Watcher()._queue_build(tmp_path, "series", "TV"))
    assert queued == [(tmp_path, "series", False)]


def test_build_jobs_coalesce_limit_concurrency_and_cancel(tmp_path):
    async def run():
        await jobs.shutdown_builds()
        jobs._jobs.clear()
        release = asyncio.Event()
        active = []

        async def build(job_id):
            active.append(job_id)
            await release.wait()
            return job_id

        first = jobs.start(tmp_path / "a", "series", build)
        assert jobs.start(tmp_path / "a", "series", build) == first
        second = jobs.start(tmp_path / "b", "series", build)
        third = jobs.start(tmp_path / "c", "series", build)
        await asyncio.sleep(0)
        assert active == [first, second]
        assert jobs.get_job(third)["status"] == "queued"
        await jobs.shutdown_builds()
        assert all(job["status"] == "cancelled" for job in jobs.list_jobs())
        assert not jobs._tasks

    asyncio.run(run())


@pytest.mark.parametrize(("value", "expected"), [(None, 5), ("nonsense", 5), ("-30", 0), ("99999999999", 60), ("1.5", 1.5)])
def test_retry_after_is_safe_and_bounded(value, expected):
    assert retry_delay(value) == expected


def test_credentials_redacted_in_plain_and_encoded_query_keys():
    message = '/api/image?api%5Ftoken=secret&api_key=provider-secret&q=title'
    assert redact_tokens(message) == '/api/image?api%5Ftoken=REDACTED&api_key=REDACTED&q=title'
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s", (message,), None)
    assert _RedactTokenFilter().filter(record)
    assert "secret" not in record.getMessage()
