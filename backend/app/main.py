"""FastAPI entrypoint."""
from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from . import __version__
from . import db
from .config import CONFIG_DIR, MEDIA_ROOT, SettingsError, env
from .logging_setup import setup_logging
from .routes.api import router as api_router
from .routes.settings import router as settings_router
from .routes.snapshots import router as snapshots_router
from .services import scanner
from .services import builder
from .services.async_io import run_in_thread
from .services.scheduler import scheduler
from .services.watcher import watcher

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
    setup_logging()
    await run_in_thread(db.conn)
    logger.info(
        "plex-nfo-builder v{} starting (media={}, config={})",
        __version__, MEDIA_ROOT, CONFIG_DIR,
    )

    loop = asyncio.get_running_loop()

    async def _detect_libraries_bg() -> None:
        try:
            libs = await run_in_thread(scanner.detect_libraries)
            logger.info("Detected libraries: {}", [lib["name"] for lib in libs])
        except Exception as e:
            logger.warning("Initial library detection failed: {}", e)
        # Start the watcher *after* detection so it sees the right paths.
        try:
            watcher.start(loop=loop)
        except Exception as e:
            logger.warning("Watcher failed to start: {}", e)

    detection = asyncio.create_task(_detect_libraries_bg())

    try:
        scheduler.start()
    except Exception as e:
        logger.warning("Scheduler failed to start: {}", e)

    try:
        yield
    finally:
        # Detection must not start a fresh watcher after shutdown has begun.
        detection.cancel()
        await asyncio.gather(detection, return_exceptions=True)
        # Stop the watcher first so it doesn't keep spawning build jobs
        # after the rest of the app has begun tearing down.
        try:
            await watcher.aclose()
        except Exception as e:
            logger.warning("Watcher failed to stop cleanly: {}", e)
        try:
            await scheduler.stop()
        except Exception as e:
            logger.warning("Scheduler failed to stop cleanly: {}", e)
        await builder.shutdown_builds()
        from .services import tvdb, tmdb, fanart, ratings
        try:
            for provider in (tvdb, tmdb, fanart, ratings):
                await provider.close_client()
        finally:
            db.close()


app = FastAPI(title="Plex NFO Builder", version=__version__, lifespan=lifespan)

# ---- Access control (v0.14.0) ---------------------------------------------
# The API is fail-closed: with no API_TOKEN set, every /api call is refused
# so an upgraded-but-unconfigured instance can't be driven by a LAN attacker.
_API_TOKEN: Optional[str] = (env.api_token or "").strip() or None
if not _API_TOKEN:
    logger.warning(
        "API_TOKEN is not set — the API is locked (fail-closed). Set the "
        "API_TOKEN environment variable and reload to enable access."
    )

# Host header allowlist (DNS-rebinding defense). Outermost so a spoofed Host
# is rejected before anything else runs. Empty allowlist => accept any host.
_trusted = [h.strip() for h in env.trusted_hosts.split(",") if h.strip()]

# CORS is opt-in and off by default: the bundled SPA is same-origin. Only
# configured origins are allowed — never a wildcard on these file-mutating
# routes.
_origins = [o.strip() for o in env.cors_allow_origins.split(",") if o.strip()]
if "*" in _origins:
    raise ValueError("CORS_ALLOW_ORIGINS must list explicit origins, not '*'")


def _request_token(request: Request) -> Optional[str]:
    """Pull the API token from header, bearer auth, or the ``api_token`` query
    param (the last covers <img>/<a> requests that can't set headers)."""
    tok = request.headers.get("x-api-token")
    if tok:
        return tok
    auth = request.headers.get("authorization")
    if auth and auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    # Image/download links need query tokens; mutations require a header.
    return request.query_params.get("api_token") if request.method in {"GET", "HEAD"} else None


# The auto-generated schema/docs leak the full endpoint surface, so they're
# gated too (not just /api). The SPA and its static assets stay open so the
# login screen can bootstrap.
_PROTECTED_EXACT = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


@app.middleware("http")
async def _require_token(request: Request, call_next):
    path = request.url.path
    if path == "/api" or path.startswith("/api/") or path in _PROTECTED_EXACT:
        # Let CORS preflights through unauthenticated (browsers can't attach
        # the token to a preflight); the actual request still gets checked.
        if request.method != "OPTIONS":
            if not _API_TOKEN:
                return JSONResponse(
                    {"detail": "Server misconfigured: API_TOKEN is not set."},
                    status_code=503,
                )
            provided = _request_token(request)
            # Compare as bytes: compare_digest raises TypeError on non-ASCII
            # str, which a crafted token could otherwise turn into a 500.
            if not provided or not secrets.compare_digest(
                provided.encode("utf-8", "ignore"), _API_TOKEN.encode("utf-8")
            ):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    response = await call_next(request)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if path.startswith("/api/") or path in _PROTECTED_EXACT:
        response.headers["Cache-Control"] = "no-store"
    return response


# CORS must wrap auth so browsers can read 401/503 responses too. TrustedHost
# is outermost, rejecting spoofed Host headers before either middleware.
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"])
if _trusted:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted)


@app.exception_handler(SettingsError)
async def settings_unavailable(request: Request, error: SettingsError):
    return JSONResponse({"detail": str(error)}, status_code=503)


app.include_router(api_router)
app.include_router(settings_router)
app.include_router(snapshots_router)


# Serve the built frontend if present
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    _STATIC_ROOT = STATIC_DIR.resolve()

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        # fall through API
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # Resolve + enforce containment so URL-encoded traversal
        # (e.g. /%2e%2e/config/settings.json) can't escape the static dir
        # and serve secrets/DB/arbitrary files.
        candidate = (STATIC_DIR / full_path).resolve()
        if (candidate == _STATIC_ROOT or _STATIC_ROOT in candidate.parents) and candidate.is_file():
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
