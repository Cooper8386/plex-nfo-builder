"""FastAPI entrypoint."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from . import __version__
from . import db
from .config import CONFIG_DIR, MEDIA_ROOT, env
from .logging_setup import setup_logging
from .routes.api import router as api_router
from .services import scanner
from .services.scheduler import scheduler
from .services.watcher import watcher

setup_logging()
db.conn()  # init sqlite


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup + shutdown coordination (v0.12.0).

    Replaces the legacy ``@app.on_event`` pair so the watcher can hook in
    cleanly alongside the scheduler. Library detection runs in the
    background so the API can begin serving immediately; the watcher is
    started after detection completes (or fails) so it always sees the
    fresh library set.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(
        "plex-nfo-builder v{} starting (media={}, config={})",
        __version__, MEDIA_ROOT, CONFIG_DIR,
    )

    loop = asyncio.get_running_loop()

    async def _detect_libraries_bg() -> None:
        try:
            libs = await asyncio.to_thread(scanner.detect_libraries)
            logger.info("Detected libraries: {}", [l["name"] for l in libs])
        except Exception as e:
            logger.warning("Initial library detection failed: {}", e)
        # Start the watcher *after* detection so it sees the right paths.
        try:
            watcher.start(loop=loop)
        except Exception as e:
            logger.warning("Watcher failed to start: {}", e)

    asyncio.create_task(_detect_libraries_bg())

    try:
        scheduler.start()
    except Exception as e:
        logger.warning("Scheduler failed to start: {}", e)

    try:
        yield
    finally:
        # Stop the watcher first so it doesn't keep spawning build jobs
        # after the rest of the app has begun tearing down.
        try:
            watcher.stop()
        except Exception as e:
            logger.warning("Watcher failed to stop cleanly: {}", e)
        try:
            await scheduler.stop()
        except Exception as e:
            logger.warning("Scheduler failed to stop cleanly: {}", e)


app = FastAPI(title="Plex NFO Builder", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


# Serve the built frontend if present
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        # fall through API
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
else:
    @app.get("/")
    async def root():
        return {
            "name": "plex-nfo-builder",
            "version": __version__,
            "docs": "/docs",
            "frontend": "not-built",
        }
