"""SQLite cache + bindings + state DB. Single connection, pragma'd for concurrency."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .config import DB_PATH

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _init_schema(_conn)
        _migrate(_conn)
    return _conn


def _init_schema(c: sqlite3.Connection) -> None:
    with _lock:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS tvdb_cache (
                key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                fetched_at INTEGER NOT NULL,
                ttl INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bindings (
                folder_path TEXT PRIMARY KEY,
                kind TEXT NOT NULL,           -- series | movie
                provider TEXT NOT NULL,       -- tvdb | tmdb | imdb
                external_id TEXT NOT NULL,
                title TEXT,
                year INTEGER,
                language TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS active_artwork (
                folder_path TEXT NOT NULL,
                slot TEXT NOT NULL,           -- poster | background | banner | clearlogo | season-NN-poster | episode-<id>
                source_path TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (folder_path, slot)
            );

            CREATE TABLE IF NOT EXISTS item_state (
                folder_path TEXT PRIMARY KEY,
                library TEXT,
                kind TEXT,
                title TEXT,
                year INTEGER,
                external_id TEXT,
                provider TEXT,
                nfo_status TEXT,              -- none | partial | complete | stale | foreign | mixed
                episode_count_local INTEGER,
                episode_count_tvdb INTEGER,
                last_scanned INTEGER,
                last_built INTEGER,
                poster_path TEXT
            );

            CREATE TABLE IF NOT EXISTS libraries (
                name TEXT PRIMARY KEY,         -- folder name under MEDIA_ROOT
                kind TEXT NOT NULL,            -- tv | movies | mixed
                enabled INTEGER NOT NULL DEFAULT 1,
                detected_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artwork_selections (
                folder_path TEXT NOT NULL,
                slot TEXT NOT NULL,           -- poster | background | banner | clearlogo | season-NN-poster
                url TEXT NOT NULL,
                language TEXT,
                score INTEGER,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (folder_path, slot)
            );

            CREATE TABLE IF NOT EXISTS episode_overrides (
                folder_path TEXT NOT NULL,
                season INTEGER NOT NULL,
                episode INTEGER NOT NULL,
                tvdb_episode_id TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (folder_path, season, episode)
            );

            CREATE TABLE IF NOT EXISTS nfo_overrides (
                folder_path TEXT NOT NULL,
                scope TEXT NOT NULL,           -- 'series' | 'season-NN' | 'episode-<id>' | 'movie'
                field TEXT NOT NULL,           -- 'title' | 'sorttitle' | 'plot' | 'tagline' | 'originaltitle'
                value TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (folder_path, scope, field)
            );

            CREATE INDEX IF NOT EXISTS idx_nfo_ovr_folder ON nfo_overrides(folder_path);

            CREATE TABLE IF NOT EXISTS custom_artwork (
                id TEXT PRIMARY KEY,           -- sha1 hex
                folder_path TEXT NOT NULL,     -- where it was uploaded for (used to filter the picker)
                slot TEXT,                     -- optional intended slot
                source TEXT NOT NULL,          -- 'upload' | 'url'
                origin TEXT,                   -- original filename or URL
                file_path TEXT NOT NULL,       -- on-disk path under CUSTOM_ARTWORK_DIR (or remote URL for url-source)
                content_type TEXT,
                size INTEGER,
                created_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_custom_art_folder ON custom_artwork(folder_path);
            CREATE INDEX IF NOT EXISTS idx_item_state_library ON item_state(library);
            CREATE INDEX IF NOT EXISTS idx_item_state_status ON item_state(nfo_status);
            CREATE INDEX IF NOT EXISTS idx_artwork_sel_folder ON artwork_selections(folder_path);
            CREATE INDEX IF NOT EXISTS idx_episode_ovr_folder ON episode_overrides(folder_path);
            """
        )


def _migrate(c: sqlite3.Connection) -> None:
    """Idempotent ALTER-TABLE migrations for legacy DBs."""
    with _lock:
        cols = {r[1] for r in c.execute("PRAGMA table_info(bindings)").fetchall()}
        if "source_locked" not in cols:
            try:
                c.execute("ALTER TABLE bindings ADD COLUMN source_locked INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass


# ---- TVDB cache -------------------------------------------------------------

def cache_get(key: str) -> Optional[dict[str, Any]]:
    c = conn()
    with _lock:
        row = c.execute("SELECT payload, fetched_at, ttl FROM tvdb_cache WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    if row["ttl"] > 0 and (time.time() - row["fetched_at"]) > row["ttl"]:
        return None
    try:
        return json.loads(row["payload"])
    except Exception:
        return None


def cache_set(key: str, payload: Any, ttl: int) -> None:
    c = conn()
    with _lock:
        c.execute(
            "INSERT OR REPLACE INTO tvdb_cache(key, payload, fetched_at, ttl) VALUES (?,?,?,?)",
            (key, json.dumps(payload), int(time.time()), int(ttl)),
        )


def cache_clear(prefix: Optional[str] = None) -> int:
    c = conn()
    with _lock:
        if prefix:
            cur = c.execute("DELETE FROM tvdb_cache WHERE key LIKE ?", (prefix + "%",))
        else:
            cur = c.execute("DELETE FROM tvdb_cache")
        return cur.rowcount


# ---- Bindings ---------------------------------------------------------------

def upsert_binding(folder_path: str, kind: str, provider: str, external_id: str,
                   title: Optional[str] = None, year: Optional[int] = None,
                   language: Optional[str] = None,
                   source_locked: Optional[bool] = None,
                   respect_lock: bool = False) -> bool:
    """Insert or update a folder binding.

    If `respect_lock=True` and an existing binding has source_locked=1, this is a
    no-op and returns False. Used by auto-match so it never silently switches
    the provider for a show the user explicitly pinned. When `source_locked` is
    None the existing value is preserved on update.
    """
    now = int(time.time())
    c = conn()
    with _lock:
        existing = c.execute(
            "SELECT source_locked FROM bindings WHERE folder_path = ?", (folder_path,)
        ).fetchone()
        if existing and respect_lock and int(existing["source_locked"] or 0) == 1:
            return False
        if source_locked is None:
            locked = int(existing["source_locked"]) if existing else 0
        else:
            locked = 1 if source_locked else 0
        c.execute(
            """
            INSERT INTO bindings(folder_path, kind, provider, external_id, title, year, language, source_locked, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(folder_path) DO UPDATE SET
                kind=excluded.kind, provider=excluded.provider, external_id=excluded.external_id,
                title=excluded.title, year=excluded.year, language=excluded.language,
                source_locked=excluded.source_locked,
                updated_at=excluded.updated_at
            """,
            (folder_path, kind, provider, external_id, title, year, language, locked, now, now),
        )
        return True


def set_binding_lock(folder_path: str, locked: bool) -> None:
    c = conn()
    with _lock:
        c.execute(
            "UPDATE bindings SET source_locked = ?, updated_at = ? WHERE folder_path = ?",
            (1 if locked else 0, int(time.time()), folder_path),
        )


def set_binding_provider(folder_path: str, provider: str, external_id: str,
                         locked: bool = True, kind: Optional[str] = None,
                         title: Optional[str] = None, year: Optional[int] = None,
                         language: Optional[str] = None) -> None:
    """Switch the metadata provider for a folder, defaulting to locking it."""
    existing = get_binding(folder_path)
    upsert_binding(
        folder_path,
        kind or (existing["kind"] if existing else "series"),
        provider,
        external_id,
        title=title if title is not None else (existing["title"] if existing else None),
        year=year if year is not None else (existing["year"] if existing else None),
        language=language if language is not None else (existing["language"] if existing else None),
        source_locked=locked,
    )


def get_binding(folder_path: str) -> Optional[sqlite3.Row]:
    c = conn()
    with _lock:
        return c.execute("SELECT * FROM bindings WHERE folder_path = ?", (folder_path,)).fetchone()


def delete_binding(folder_path: str) -> None:
    c = conn()
    with _lock:
        c.execute("DELETE FROM bindings WHERE folder_path = ?", (folder_path,))


# ---- Active artwork ---------------------------------------------------------

def set_active_artwork(folder_path: str, slot: str, source_path: str) -> None:
    c = conn()
    with _lock:
        c.execute(
            """
            INSERT OR REPLACE INTO active_artwork(folder_path, slot, source_path, updated_at)
            VALUES (?,?,?,?)
            """,
            (folder_path, slot, source_path, int(time.time())),
        )


def get_active_artwork(folder_path: str) -> dict[str, str]:
    c = conn()
    with _lock:
        rows = c.execute(
            "SELECT slot, source_path FROM active_artwork WHERE folder_path = ?", (folder_path,)
        ).fetchall()
    return {r["slot"]: r["source_path"] for r in rows}


# ---- Item state -------------------------------------------------------------

def upsert_item_state(folder_path: str, **fields: Any) -> None:
    c = conn()
    cols = ["folder_path"] + list(fields.keys())
    vals = [folder_path] + list(fields.values())
    placeholders = ",".join(["?"] * len(cols))
    sets = ",".join([f"{k}=excluded.{k}" for k in fields.keys()])
    with _lock:
        c.execute(
            f"INSERT INTO item_state({','.join(cols)}) VALUES ({placeholders})"
            f" ON CONFLICT(folder_path) DO UPDATE SET {sets}",
            vals,
        )


def delete_item_state(folder_path: str) -> int:
    """Remove a single item plus its bindings/selections/overrides/active artwork.

    Used to forget folders that have been deleted on disk. The TVDB cache is
    untouched on purpose so re-adding the same show later still benefits.
    Returns the number of item_state rows removed (0 or 1).
    """
    c = conn()
    with _lock:
        c.execute("DELETE FROM bindings WHERE folder_path = ?", (folder_path,))
        c.execute("DELETE FROM artwork_selections WHERE folder_path = ?", (folder_path,))
        c.execute("DELETE FROM episode_overrides WHERE folder_path = ?", (folder_path,))
        c.execute("DELETE FROM active_artwork WHERE folder_path = ?", (folder_path,))
        c.execute("DELETE FROM custom_artwork WHERE folder_path = ?", (folder_path,))
        c.execute("DELETE FROM nfo_overrides WHERE folder_path = ?", (folder_path,))
        cur = c.execute("DELETE FROM item_state WHERE folder_path = ?", (folder_path,))
        return cur.rowcount


def list_item_state(library: Optional[str] = None,
                    statuses: Optional[list[str]] = None,
                    title_q: Optional[str] = None,
                    limit: int = 5000) -> list[sqlite3.Row]:
    c = conn()
    sql = "SELECT * FROM item_state WHERE 1=1"
    args: list[Any] = []
    if library:
        sql += " AND library = ?"
        args.append(library)
    if statuses:
        sql += f" AND nfo_status IN ({','.join(['?'] * len(statuses))})"
        args.extend(statuses)
    if title_q:
        sql += " AND title LIKE ? COLLATE NOCASE"
        args.append(f"%{title_q}%")
    sql += " ORDER BY title COLLATE NOCASE LIMIT ?"
    args.append(limit)
    with _lock:
        return c.execute(sql, args).fetchall()


# ---- Libraries --------------------------------------------------------------

def upsert_library(name: str, kind: str) -> None:
    c = conn()
    with _lock:
        c.execute(
            """
            INSERT INTO libraries(name, kind, enabled, detected_at)
            VALUES (?,?,1,?)
            ON CONFLICT(name) DO UPDATE SET kind=excluded.kind
            """,
            (name, kind, int(time.time())),
        )


def list_libraries() -> list[sqlite3.Row]:
    c = conn()
    with _lock:
        return c.execute("SELECT * FROM libraries ORDER BY name").fetchall()


def set_library_kind(name: str, kind: str) -> None:
    c = conn()
    with _lock:
        c.execute("UPDATE libraries SET kind = ? WHERE name = ?", (kind, name))


def set_library_enabled(name: str, enabled: bool) -> None:
    c = conn()
    with _lock:
        c.execute("UPDATE libraries SET enabled = ? WHERE name = ?", (1 if enabled else 0, name))


# ---- Artwork selections (v0.4.0 picker) ------------------------------------

def set_artwork_selection(folder_path: str, slot: str, url: str,
                          language: Optional[str] = None,
                          score: Optional[int] = None) -> None:
    c = conn()
    with _lock:
        c.execute(
            """
            INSERT OR REPLACE INTO artwork_selections(folder_path, slot, url, language, score, updated_at)
            VALUES (?,?,?,?,?,?)
            """,
            (folder_path, slot, url, language, score, int(time.time())),
        )


def clear_artwork_selection(folder_path: str, slot: Optional[str] = None) -> int:
    c = conn()
    with _lock:
        if slot is None:
            cur = c.execute("DELETE FROM artwork_selections WHERE folder_path = ?", (folder_path,))
        else:
            cur = c.execute(
                "DELETE FROM artwork_selections WHERE folder_path = ? AND slot = ?",
                (folder_path, slot),
            )
        return cur.rowcount


def get_artwork_selections(folder_path: str) -> dict[str, dict[str, Any]]:
    c = conn()
    with _lock:
        rows = c.execute(
            "SELECT slot, url, language, score FROM artwork_selections WHERE folder_path = ?",
            (folder_path,),
        ).fetchall()
    return {
        r["slot"]: {"url": r["url"], "language": r["language"], "score": r["score"]}
        for r in rows
    }


# ---- Episode overrides (v0.4.0 mapper) -------------------------------------

def set_episode_override(folder_path: str, season: int, episode: int,
                         tvdb_episode_id: str) -> None:
    c = conn()
    with _lock:
        c.execute(
            """
            INSERT OR REPLACE INTO episode_overrides(folder_path, season, episode, tvdb_episode_id, updated_at)
            VALUES (?,?,?,?,?)
            """,
            (folder_path, int(season), int(episode), str(tvdb_episode_id), int(time.time())),
        )


def clear_episode_override(folder_path: str, season: Optional[int] = None,
                           episode: Optional[int] = None) -> int:
    c = conn()
    with _lock:
        if season is None or episode is None:
            cur = c.execute(
                "DELETE FROM episode_overrides WHERE folder_path = ?", (folder_path,)
            )
        else:
            cur = c.execute(
                "DELETE FROM episode_overrides WHERE folder_path = ? AND season = ? AND episode = ?",
                (folder_path, int(season), int(episode)),
            )
        return cur.rowcount


def get_episode_overrides(folder_path: str) -> dict[tuple[int, int], str]:
    c = conn()
    with _lock:
        rows = c.execute(
            "SELECT season, episode, tvdb_episode_id FROM episode_overrides WHERE folder_path = ?",
            (folder_path,),
        ).fetchall()
    return {(int(r["season"]), int(r["episode"])): r["tvdb_episode_id"] for r in rows}


# ---- NFO field overrides (v0.5.3) ------------------------------------------

def set_nfo_override(folder_path: str, scope: str, field: str,
                     value: Optional[str]) -> None:
    c = conn()
    with _lock:
        if value is None or value == "":
            c.execute(
                "DELETE FROM nfo_overrides WHERE folder_path = ? AND scope = ? AND field = ?",
                (folder_path, scope, field),
            )
            return
        c.execute(
            """
            INSERT OR REPLACE INTO nfo_overrides(folder_path, scope, field, value, updated_at)
            VALUES (?,?,?,?,?)
            """,
            (folder_path, scope, field, value, int(time.time())),
        )


def clear_nfo_override(folder_path: str, scope: Optional[str] = None,
                       field: Optional[str] = None) -> int:
    c = conn()
    with _lock:
        if scope is None:
            cur = c.execute("DELETE FROM nfo_overrides WHERE folder_path = ?", (folder_path,))
        elif field is None:
            cur = c.execute(
                "DELETE FROM nfo_overrides WHERE folder_path = ? AND scope = ?",
                (folder_path, scope),
            )
        else:
            cur = c.execute(
                "DELETE FROM nfo_overrides WHERE folder_path = ? AND scope = ? AND field = ?",
                (folder_path, scope, field),
            )
        return cur.rowcount


def get_nfo_overrides(folder_path: str) -> dict[str, dict[str, str]]:
    c = conn()
    with _lock:
        rows = c.execute(
            "SELECT scope, field, value FROM nfo_overrides WHERE folder_path = ?",
            (folder_path,),
        ).fetchall()
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        out.setdefault(r["scope"], {})[r["field"]] = r["value"] or ""
    return out


def bulk_set_nfo_overrides(folder_path: str,
                           overrides: dict[str, dict[str, Optional[str]]]) -> None:
    """Replace all overrides for a folder with the given map. Used by sidecar restore."""
    c = conn()
    now = int(time.time())
    with _lock:
        c.execute("DELETE FROM nfo_overrides WHERE folder_path = ?", (folder_path,))
        for scope, fields in (overrides or {}).items():
            if not isinstance(fields, dict):
                continue
            for field, value in fields.items():
                if value is None or value == "":
                    continue
                c.execute(
                    "INSERT OR REPLACE INTO nfo_overrides(folder_path, scope, field, value, updated_at) VALUES (?,?,?,?,?)",
                    (folder_path, scope, field, str(value), now),
                )


# ---- Custom artwork (v0.5.0) -----------------------------------------------

def add_custom_artwork(art_id: str, *, folder_path: str, slot: Optional[str],
                       source: str, origin: Optional[str], file_path: str,
                       content_type: Optional[str] = None,
                       size: Optional[int] = None) -> None:
    c = conn()
    with _lock:
        c.execute(
            """
            INSERT OR REPLACE INTO custom_artwork(id, folder_path, slot, source, origin, file_path, content_type, size, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (art_id, folder_path, slot, source, origin, file_path, content_type, size, int(time.time())),
        )


def list_custom_artwork(folder_path: Optional[str] = None) -> list[sqlite3.Row]:
    c = conn()
    with _lock:
        if folder_path:
            return c.execute(
                "SELECT * FROM custom_artwork WHERE folder_path = ? ORDER BY created_at DESC",
                (folder_path,),
            ).fetchall()
        return c.execute("SELECT * FROM custom_artwork ORDER BY created_at DESC").fetchall()


def get_custom_artwork(art_id: str) -> Optional[sqlite3.Row]:
    c = conn()
    with _lock:
        return c.execute("SELECT * FROM custom_artwork WHERE id = ?", (art_id,)).fetchone()


def delete_custom_artwork(art_id: str) -> int:
    c = conn()
    with _lock:
        cur = c.execute("DELETE FROM custom_artwork WHERE id = ?", (art_id,))
        return cur.rowcount
