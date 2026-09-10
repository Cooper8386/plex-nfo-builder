"""Loguru configuration with rotating files and per-job logs."""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from loguru import logger

from .config import LOG_DIR, env


_initialized = False

# Redact the API token from uvicorn's access log, which otherwise records the
# full request line including the ``api_token`` query param used by <img> loads.
_TOKEN_QS = re.compile(r"([\w%.-]+)=([^&\s\"']+)")
_SECRET_KEYS = {"api_token", "api_key", "apikey", "x-plex-token", "token", "pin"}


def redact_tokens(message: str) -> str:
    return _TOKEN_QS.sub(
        lambda match: f"{match[1]}=REDACTED" if unquote(match[1]).lower() in _SECRET_KEYS else match[0],
        message,
    )


def _redact_log_record(record) -> None:
    record["message"] = redact_tokens(record["message"])


class _RedactTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_tokens(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_tokens(a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


def setup_logging() -> None:
    global _initialized
    if _initialized:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.configure(patcher=_redact_log_record)
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
    # Attach to the logger instance so the filter survives uvicorn configuring
    # its own handlers afterwards.
    _access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _RedactTokenFilter) for f in _access.filters):
        _access.addFilter(_RedactTokenFilter())
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
