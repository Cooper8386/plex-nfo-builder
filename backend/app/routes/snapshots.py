"""Library snapshot creation, history, and authenticated downloads."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import db
from ..services import jobs, snapshots
from ..services.async_io import run_in_thread

router = APIRouter(prefix="/api/libraries")


def _library(name: str, *, must_exist: bool = False):
    try:
        folder = snapshots.library_path(name)
        snapshots.storage_path(name)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if db.get_library(name) is None:
        raise HTTPException(404, "Library not found")
    if must_exist and not folder.is_dir():
        raise HTTPException(404, "Library directory not found")
    return folder


@router.get("/{name}/snapshots")
def list_snapshots(name: str):
    folder = _library(name)
    try:
        saved = snapshots.list_snapshots(name)
    except OSError as error:
        raise HTTPException(500, "Cannot read snapshot storage") from error
    return {
        "snapshots": saved, "storage_path": str(snapshots.storage_path(name)),
        "jobs": [job for job in jobs.list_jobs()
                 if job["kind"] == "library_snapshot" and job["folder"] == str(folder)],
    }


@router.post("/{name}/snapshots", status_code=202)
async def create_snapshot(name: str):
    await run_in_thread(_library, name, must_exist=True)
    return {"job_id": snapshots.start_snapshot(name)}


@router.get("/{name}/snapshots/{snapshot_id}/download")
def download_snapshot(name: str, snapshot_id: str):
    _library(name)
    try:
        path = snapshots.download_path(name, snapshot_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    return FileResponse(path, media_type="application/zip",
                        filename=snapshots.snapshot_filename(name, snapshot_id))
