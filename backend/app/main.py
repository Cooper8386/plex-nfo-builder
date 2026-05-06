"""FastAPI entrypoint."""
from __future__ import annotations

import os
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

setup_logging()
db.conn()  # init sqlite

app = FastAPI(title="Plex NFO Builder", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


@app.on_event("startup")
async def startup():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("plex-nfo-builder starting (media={}, config={})", MEDIA_ROOT, CONFIG_DIR)
    try:
        libs = scanner.detect_libraries()
        logger.info("Detected libraries: {}", [l["name"] for l in libs])
    except Exception as e:
        logger.warning("Initial library detection failed: {}", e)
    try:
        scheduler.start()
    except Exception as e:
        logger.warning("Scheduler failed to start: {}", e)


@app.on_event("shutdown")
async def shutdown():
    try:
        await scheduler.stop()
    except Exception as e:
        logger.warning("Scheduler failed to stop cleanly: {}", e)


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
