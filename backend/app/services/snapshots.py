"""Persistent, library-scoped ZIP backups of non-media files."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from ..config import CONFIG_DIR, MEDIA_ROOT
from . import jobs
from .async_io import run_in_thread
from .parser import VIDEO_EXT
from .snapshot_automation import paused_automation

# Include formats the scanner does not index, plus audio and disc images.
MEDIA_EXTENSIONS = VIDEO_EXT | {
    ".3g2", ".3gp", ".asf", ".divx", ".f4v", ".m2t", ".m2ts", ".m2v", ".mp2", ".mpeg", ".mpg",
    ".mts", ".mxf", ".ogm", ".ogv", ".rm", ".rmvb", ".vob", ".wtv", ".iso", ".img",
    ".h264", ".h265", ".hevc", ".ssif", ".evo",
    ".aac", ".ac3", ".aif", ".aiff", ".alac", ".ape", ".dff", ".dsf", ".dts", ".eac3", ".flac",
    ".m4a", ".m4b", ".mka", ".mp3", ".oga", ".ogg", ".opus", ".pcm", ".wav", ".wma", ".wv",
}
SNAPSHOT_ID = re.compile(r"\d{8}T\d{12}Z-[0-9a-f]{32}")


def snapshot_filename(name: str, snapshot_id: str) -> str:
    created = datetime.strptime(snapshot_id[:22], "%Y%m%dT%H%M%S%fZ")
    return f"{name}-{created.strftime('%Y-%m-%d_%H-%M-%S')}Z.zip"


def library_path(name: str) -> Path:
    if not name or name in {".", ".."} or any(c in name for c in "/\\:"):
        raise ValueError("Choose a single library")
    root = MEDIA_ROOT.resolve()
    folder = root / name
    if folder.is_symlink() or folder.resolve() != folder or folder.parent != root:
        raise ValueError("Library must be a directory directly inside MEDIA_ROOT, without symlinks")
    return folder


def storage_path(name: str) -> Path:
    library_path(name)
    base = CONFIG_DIR.resolve() / "library-snapshots"
    folder = base / hashlib.sha256(name.encode("utf-8")).hexdigest()
    if base.is_symlink() or folder.is_symlink() or folder.resolve() != folder:
        raise ValueError("Snapshot storage must not contain symlinks")
    if folder.is_relative_to(MEDIA_ROOT.resolve()):
        raise ValueError("Snapshot storage must be outside MEDIA_ROOT; move CONFIG_DIR outside it")
    return folder


def download_path(name: str, snapshot_id: str) -> Path:
    if not SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ValueError("Invalid snapshot ID")
    path = storage_path(name) / f"{snapshot_id}.zip"
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("Snapshot not found")
    return path


def list_snapshots(name: str) -> list[dict]:
    snapshots = []
    for path in sorted(storage_path(name).glob("*.zip"), reverse=True):
        if path.is_symlink() or not SNAPSHOT_ID.fullmatch(path.stem):
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                metadata = json.loads(archive.comment)
            snapshots.append({
                "id": path.stem, "filename": snapshot_filename(name, path.stem),
                "created_at": metadata["created_at"],
                "file_count": metadata["file_count"], "size_bytes": path.stat().st_size,
                "skipped_link_count": metadata.get("skipped_link_count", 0),
            })
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
            logger.warning("Cannot read library snapshot: {}", path)
    return snapshots


def _files(
    folder: Path,
) -> tuple[dict[Path, tuple[Path, os.stat_result]], dict[Path, tuple[Path, os.stat_result]]]:
    """Copy existing files and separately track already-broken library-local links."""
    files: dict[Path, tuple[Path, os.stat_result]] = {}
    broken: dict[Path, tuple[Path, os.stat_result]] = {}

    def fail(error: OSError) -> None:
        raise error

    for directory, dirs, names in os.walk(folder, onerror=fail, followlinks=False):
        for name in dirs + names:
            path = Path(directory) / name
            info = path.lstat()
            try:
                source = path.resolve(strict=True)
            except FileNotFoundError:
                # Only an existing link with an already-missing target can be
                # skipped. A regular file disappearing during traversal is an error.
                if not stat.S_ISLNK(info.st_mode):
                    raise
                source = path.resolve(strict=False)
                if not source.is_relative_to(folder):
                    raise ValueError(
                        f"Snapshot cannot include symbolic links outside the library: {path.relative_to(folder)}"
                    )
                broken[path] = source, info
                continue
            except RuntimeError as error:
                raise ValueError(f"Snapshot cannot include cyclic symbolic links: {path.relative_to(folder)}") from error
            if not source.is_relative_to(folder):
                raise ValueError(f"Snapshot cannot include symbolic links outside the library: {path.relative_to(folder)}")
            if stat.S_ISLNK(info.st_mode) or source != path:
                info = source.stat()
                if stat.S_ISDIR(info.st_mode):
                    raise ValueError(f"Snapshot cannot include directory symbolic links: {path.relative_to(folder)}")
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Snapshot cannot include special files: {path.relative_to(folder)}")
            if path.suffix.lower() not in MEDIA_EXTENSIONS and source.suffix.lower() not in MEDIA_EXTENSIONS:
                files[path] = source, info
    return files, broken


def _identity(info: os.stat_result) -> tuple:
    # Windows stat/fstat can report different creation times for the same file.
    changed = info.st_ctime_ns if os.name == "posix" else None
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, changed


def create_snapshot(name: str, job: dict) -> dict:
    folder = library_path(name)
    if not folder.is_dir():
        raise FileNotFoundError("Library directory not found")
    storage = storage_path(name)
    storage.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc)
    snapshot_id = f"{created.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex}"
    destination = storage / f"{snapshot_id}.zip"
    temporary = storage / f"{snapshot_id}.partial"
    try:
        files, broken = _files(folder)
        for path, (missing_target, _) in broken.items():
            message = (f"Skipped broken link: {path.relative_to(folder)} "
                       f"(missing target: {missing_target.relative_to(folder)})")
            job.setdefault("messages", []).append(message)
            logger.warning("{}: {}", name, message)
        job["total"] = len(files)
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True, strict_timestamps=False) as archive:
            for path, (source_path, original) in files.items():
                # Pin the resolved target while retaining the original archive name.
                # O_NOFOLLOW rejects a target replaced by a link after the walk.
                if path.resolve(strict=True) != source_path or library_path(name) != folder:
                    raise ValueError("Library changed during snapshot; pause file activity and retry")
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                with os.fdopen(os.open(source_path, flags), "rb") as source:
                    if _identity(os.fstat(source.fileno())) != _identity(original):
                        raise ValueError("Library changed during snapshot; pause file activity and retry")
                    info = zipfile.ZipInfo.from_file(source_path, path.relative_to(folder).as_posix(),
                                                    strict_timestamps=False)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with archive.open(info, "w", force_zip64=True) as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
                    if _identity(os.fstat(source.fileno())) != _identity(original):
                        raise ValueError("Library changed during snapshot; pause file activity and retry")
                job["progress"] += 1
            current_files, current_broken = _files(folder)
            for before, after in ((files, current_files), (broken, current_broken)):
                if ({p: (target, _identity(s)) for p, (target, s) in before.items()}
                        != {p: (target, _identity(s)) for p, (target, s) in after.items()}):
                    raise ValueError("Library changed during snapshot; pause file activity and retry")
            metadata = {"library": name, "created_at": created.isoformat(), "file_count": len(files),
                        "skipped_link_count": len(broken)}
            archive.comment = json.dumps(metadata).encode("utf-8")
        # Publish only closed archives; an interrupted run never replaces a saved ZIP.
        with temporary.open("r+b") as completed:
            os.fsync(completed.fileno())
        temporary.replace(destination)
        return {"id": snapshot_id, "filename": snapshot_filename(name, snapshot_id), **metadata,
                "size_bytes": destination.stat().st_size}
    finally:
        temporary.unlink(missing_ok=True)


def start_snapshot(name: str) -> str:
    folder = library_path(name)

    async def run(job_id: str) -> str:
        job = jobs.get_job(job_id)
        assert job is not None
        try:
            job["messages"].append("Pausing watcher and scheduled jobs; waiting for active work to finish")
            async with paused_automation():
                job["messages"].append("Automation paused; copying library metadata")
                snapshot = await run_in_thread(create_snapshot, name, job)
            warning = (f" ({snapshot['skipped_link_count']} broken links skipped)"
                       if snapshot.get("skipped_link_count") else "")
            job["messages"].append(f"Saved {snapshot['file_count']} files to {snapshot['filename']}{warning}")
            job.update(status="completed", finished_at=int(time.time()))
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            logger.exception("Library snapshot failed: {}", name)
            job["messages"].append(f"Snapshot failed: {error}")
            job.update(status="error", finished_at=int(time.time()))
        return job_id

    # A snapshot must not consume a build slot while it waits for queued builds.
    return jobs.start(folder, "library_snapshot", run, use_build_slot=False)
