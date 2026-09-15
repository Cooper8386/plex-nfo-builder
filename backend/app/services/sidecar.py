"""Sidecar config file (.plex-nfo-builder.json) inside each show/movie folder.

Stores everything the app knows about the folder so that wiping the SQLite DB
does not lose work: the binding (provider, external_id, title, year, source
lock), per-field NFO overrides, artwork URL selections, and per-episode
TVDB id overrides.

The file is JSON, indented for readability, written atomically. It is the
source of truth on disk; the DB is the working cache. On scan, if a folder
has no binding row but a sidecar exists, it is restored.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

from loguru import logger

from .. import db
from .parser import is_within_folder


SIDECAR_NAME = ".plex-nfo-builder.json"
SIDECAR_VERSION = 1


def sidecar_path(folder: Path | str) -> Path:
    return Path(folder) / SIDECAR_NAME


def read_sidecar(folder: Path | str) -> Optional[dict]:
    p = sidecar_path(folder)
    if not p.exists() or not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version", SIDECAR_VERSION) != SIDECAR_VERSION:
            raise ValueError("unsupported sidecar object/version")
        for section in ("binding", "overrides", "artwork_selections", "episode_overrides", "episode_file_overrides"):
            if data.get(section) is not None and not isinstance(data[section], dict):
                raise ValueError(f"{section} must be an object")
        if data.get("ignored_episode_files") is not None and not isinstance(data["ignored_episode_files"], list):
            raise ValueError("ignored_episode_files must be an array")
        return data
    except (OSError, ValueError) as e:
        logger.warning("Sidecar at {} unreadable: {}", p, e)
        return None


def _atomic_write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pnb-", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def build_sidecar_payload(folder: Path | str) -> dict:
    """Snapshot the current DB state for `folder` into a sidecar dict."""
    folder_str = str(folder)
    binding_row = db.get_binding(folder_str)
    binding: Optional[dict] = None
    if binding_row:
        bd = dict(binding_row)
        binding = {
            "kind": bd.get("kind"),
            "provider": bd.get("provider"),
            "external_id": bd.get("external_id"),
            "title": bd.get("title"),
            "year": bd.get("year"),
            "language": bd.get("language"),
            "source_locked": bool(bd.get("source_locked") or 0),
            "secondary_provider": bd.get("secondary_provider"),
            "secondary_external_id": bd.get("secondary_external_id"),
        }
    overrides = db.get_nfo_overrides(folder_str)
    artwork_selections = db.get_artwork_selections(folder_str)
    ep_ovr_raw = db.get_episode_overrides(folder_str)  # {(s,e): tvdb_id}
    episode_overrides = {f"{s:02d}-{e:02d}": tid for (s, e), tid in ep_ovr_raw.items()}
    # v0.10.0: per-file overrides keyed by the file path *relative* to the
    # folder so a folder rename doesn't invalidate them.
    ep_file_raw = db.get_episode_file_overrides(folder_str)
    episode_file_overrides: dict[str, dict] = {}
    for fp, payload in ep_file_raw.items():
        if is_within_folder(Path(fp), Path(folder_str)):
            rel = Path(os.path.relpath(fp, folder_str)).as_posix()
            episode_file_overrides[rel] = payload
    ignored_episode_files = db.get_nfo_ignored_episodes(folder_str)
    custom_tags = db.list_custom_tags(folder_str)
    return {
        "version": SIDECAR_VERSION,
        "binding": binding,
        "overrides": overrides,
        "artwork_selections": artwork_selections,
        "episode_overrides": episode_overrides,
        "episode_file_overrides": episode_file_overrides,
        "ignored_episode_files": ignored_episode_files,
        "custom_tags": custom_tags,
    }


def write_sidecar(folder: Path | str, data: Optional[dict] = None) -> bool:
    """Persist the sidecar JSON for `folder`. Returns False on failure.

    If `data` is omitted, the payload is built from the current DB state.
    Silently no-ops if the folder doesn't exist (e.g. during cleanup).
    """
    p = sidecar_path(folder)
    if not Path(folder).exists():
        return False
    payload = data if data is not None else build_sidecar_payload(folder)
    try:
        text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
        _atomic_write(p, text)
        return True
    except Exception as e:
        logger.warning("Sidecar write failed for {}: {}", p, e)
        return False


def restore_from_sidecar(folder: Path | str) -> bool:
    """If the folder has a sidecar but no DB binding, restore everything we can.

    Used by the scanner so a wiped DB rebuilds itself from disk on next scan.
    Returns True if anything was restored.
    """
    folder_str = str(folder)
    data = read_sidecar(folder)
    if not data:
        return False
    try:
        with db.transaction():
            # Check while holding the restore transaction, so a concurrent
            # manual match cannot be overwritten after an earlier empty read.
            if db.get_binding(folder_str):
                return False
            return _restore_payload(folder_str, data)
    except Exception as error:
        logger.warning("Sidecar restore rolled back for {}: {}", folder_str, error)
        return False


def _restore_payload(folder_str: str, data: dict) -> bool:
    """Restore one validated payload inside the caller's database transaction."""
    restored = False
    binding = data.get("binding") or {}
    if binding.get("provider") and binding.get("external_id") and binding.get("kind"):
        if binding["provider"] not in {"tvdb", "tmdb"} or binding["kind"] not in {"series", "movie"}:
            raise ValueError("unsupported binding provider/kind")
        db.upsert_binding(
            folder_str,
            str(binding["kind"]),
            str(binding["provider"]),
            str(binding["external_id"]),
            title=binding.get("title"),
            year=binding.get("year"),
            language=binding.get("language"),
            source_locked=bool(binding.get("source_locked") or False),
        )
        restored = True
        sec_p = binding.get("secondary_provider")
        sec_id = binding.get("secondary_external_id")
        if sec_p in {"tvdb", "tmdb"} and sec_id:
            db.set_binding_secondary(folder_str, str(sec_p), str(sec_id))
    overrides = data.get("overrides") or {}
    if overrides:
        db.bulk_set_nfo_overrides(folder_str, overrides)
        restored = True
    artwork = data.get("artwork_selections") or {}
    if isinstance(artwork, dict):
        for slot, sel in artwork.items():
            if not isinstance(sel, dict) or not sel.get("url"):
                continue
            db.set_artwork_selection(
                folder_str, slot, sel["url"],
                language=sel.get("language"),
                score=sel.get("score"),
            )
            restored = True
    ep_ovr = data.get("episode_overrides") or {}
    if isinstance(ep_ovr, dict):
        for key, tid in ep_ovr.items():
            try:
                # key format "SS-EE"
                s_str, e_str = str(key).split("-", 1)
                s_num = int(s_str)
                e_num = int(e_str)
            except (ValueError, TypeError):
                continue
            if tid and s_num >= 0 and e_num >= 0:
                db.set_episode_override(folder_str, s_num, e_num, str(tid))
                restored = True
    # v0.10.0: per-file overrides keyed by relative path.
    ep_file_ovr = data.get("episode_file_overrides") or {}
    if isinstance(ep_file_ovr, dict):
        for rel, payload in ep_file_ovr.items():
            if not isinstance(payload, dict):
                continue
            # Both slash styles occur in sidecars moved between Docker and
            # Windows. Reject drives, absolute paths, and traversal first.
            relative = PurePosixPath(str(rel).replace("\\", "/"))
            if relative.is_absolute() or PureWindowsPath(str(rel)).drive or ".." in relative.parts:
                continue
            full = Path(folder_str).joinpath(*relative.parts)
            if full == Path(folder_str) or not is_within_folder(full, Path(folder_str)):
                continue
            db.set_episode_file_override(
                folder_str, str(full), payload.get("season"),
                payload.get("episode"), payload.get("external_id"),
            )
            restored = True
    ignored_files = data.get("ignored_episode_files") or []
    if isinstance(ignored_files, list):
        for rel in ignored_files:
            relative = PurePosixPath(str(rel).replace("\\", "/"))
            if relative.is_absolute() or PureWindowsPath(str(rel)).drive or ".." in relative.parts:
                continue
            full = Path(folder_str).joinpath(*relative.parts)
            if full == Path(folder_str) or not is_within_folder(full, Path(folder_str)):
                continue
            db.set_nfo_ignored_episode(folder_str, relative.as_posix())
            restored = True
    # v0.8.0: custom user-added tags
    custom_tags = data.get("custom_tags")
    if isinstance(custom_tags, list) and custom_tags:
        db.bulk_set_custom_tags(folder_str, [str(t) for t in custom_tags])
        restored = True
    return restored


def sync_sidecar_from_db(folder: Path | str) -> bool:
    """Convenience: write_sidecar() with a DB-built payload."""
    return write_sidecar(folder, build_sidecar_payload(folder))
