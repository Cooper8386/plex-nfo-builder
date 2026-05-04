"""Loguru configuration with rotating files and per-job logs."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from loguru import logger

from .config import LOG_DIR, env


_initialized = False


def setup_logging() -> None:
    global _initialized
    if _initialized:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level:<8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
    logger.add(sys.stdout, level=env.log_level, format=fmt, enqueue=True)
    logger.add(
        LOG_DIR / "app.log",
        level=env.log_level,
        rotation="10 MB",
        retention=10,
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    _initialized = True


def job_logger(job_id: str):
    """Return a contextualised logger that also writes to a job-specific file."""
    LOG_DIR.joinpath("jobs").mkdir(parents=True, exist_ok=True)
    sink = LOG_DIR / "jobs" / f"{job_id}.log"
    handler_id = logger.add(sink, level="DEBUG", enqueue=True, filter=lambda r: r["extra"].get("job") == job_id)
    bound = logger.bind(job=job_id)
    bound._sink_id = handler_id  # type: ignore[attr-defined]
    bound._sink_path = sink  # type: ignore[attr-defined]
    return bound


def close_job_logger(bound) -> Optional[Path]:
    sink_id = getattr(bound, "_sink_id", None)
    if sink_id is not None:
        try:
            logger.remove(sink_id)
        except Exception:
            pass
    return getattr(bound, "_sink_path", None)
