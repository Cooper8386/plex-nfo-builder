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


# v0.11.4: leading-article stripping for the auto-computed sort title. Plex,
# Sonarr, Radarr, and Kodi all do roughly this when no explicit sort title
# exists. We only handle English articles here — anything else, the user
# should use a per-show ``sorttitle`` override (e.g. an anime where the
# community uses a romanised release name like "Star Blazers 2199" but the
# actual TVDB title is "Uchuu Senkan Yamato 2199").
_LEADING_ARTICLES = ("the ", "a ", "an ")


def compute_sort_title(title: Optional[str], override: Optional[str]) -> str:
    """Resolve the effective sort title for an item.

    Priority: explicit override > stripped-article fallback. The override is
    the user-supplied ``sorttitle`` from ``nfo_overrides`` (or whatever the
    metadata provider supplies as ``sortName``); when set, it wins outright.
    Otherwise we strip a leading English article and return the rest.
    """
    if override:
        s = str(override).strip()
        if s:
            return s
    if not title:
        return ""
    raw = str(title).strip()
    low = raw.lower()
    for art in _LEADING_ARTICLES:
        if low.startswith(art):
            return raw[len(art):].strip()
    return raw


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
                source_locked INTEGER NOT NULL DEFAULT 0,
                secondary_provider TEXT,      -- tvdb | tmdb (the "other" source)
                secondary_external_id TEXT,   -- manual cross-id, optional
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
                detected_at INTEGER NOT NULL,
                metadata_source TEXT           -- NULL | tvdb | tmdb (overrides global)
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

            -- v0.10.0: per-file override anchored to the actual file path so
            -- multiple unparsed files in the same folder don't all collapse
            -- onto the same (season, episode) key. The legacy table above is
            -- kept for backward compatibility (and migrated on read).
            CREATE TABLE IF NOT EXISTS episode_file_overrides (
                folder_path TEXT NOT NULL,
                file_path TEXT NOT NULL,
                season INTEGER,
                episode INTEGER,
                external_id TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (folder_path, file_path)
            );
            CREATE INDEX IF NOT EXISTS idx_episode_file_ovr_folder
                ON episode_file_overrides(folder_path);

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

            CREATE TABLE IF NOT EXISTS custom_tags (
                folder_path TEXT NOT NULL,
                tag TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (folder_path, tag)
            );
            CREATE INDEX IF NOT EXISTS idx_custom_tags_folder ON custom_tags(folder_path);

            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                library TEXT,                  -- NULL = all libraries
                cron TEXT NOT NULL,            -- 5-field cron expression (UTC)
                action TEXT NOT NULL,          -- match_only | build_only | match_and_build | scan_only | full
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run INTEGER,
                last_status TEXT,              -- ok | error | running
                last_message TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
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
        # v0.11.3: optional secondary provider id so a TVDB-bound show can
        # carry a manual TMDB id (or vice versa) for cross-provider artwork,
        # fanart.tv lookups, and dual-uniqueid NFOs even when the providers
        # don't cross-reference each other.
        cols = {r[1] for r in c.execute("PRAGMA table_info(bindings)").fetchall()}
        if "secondary_provider" not in cols:
            try:
                c.execute("ALTER TABLE bindings ADD COLUMN secondary_provider TEXT")
            except Exception:
                pass
        if "secondary_external_id" not in cols:
            try:
                c.execute("ALTER TABLE bindings ADD COLUMN secondary_external_id TEXT")
            except Exception:
                pass
        lib_cols = {r[1] for r in c.execute("PRAGMA table_info(libraries)").fetchall()}
        if lib_cols and "metadata_source" not in lib_cols:
            try:
                c.execute("ALTER TABLE libraries ADD COLUMN metadata_source TEXT")
            except Exception:
                pass
        # v0.8.0: schedules.last_message added late — ensure existing DBs gain it.
        sched_cols = {r[1] for r in c.execute("PRAGMA table_info(schedules)").fetchall()}
        if sched_cols and "last_message" not in sched_cols:
            try:
                c.execute("ALTER TABLE schedules ADD COLUMN last_message TEXT")
            except Exception:
                pass
        # v0.11.4: item_state.sort_title — Plex/Sonarr-style ordering. Sourced
        # from (1) per-show sorttitle override, (2) provider sortName, (3)
        # title with leading articles stripped. Backfilled here for any rows
        # already in the DB so the very first library load after upgrade
        # already sorts correctly.
        item_cols = {r[1] for r in c.execute("PRAGMA table_info(item_state)").fetchall()}
        if item_cols and "sort_title" not in item_cols:
            try:
                c.execute("ALTER TABLE item_state ADD COLUMN sort_title TEXT")
            except Exception:
                pass
            try:
                c.execute("CREATE INDEX IF NOT EXISTS idx_item_state_sort ON item_state(sort_title COLLATE NOCASE)")
            except Exception:
                pass
            # Backfill: derive a sort title from the existing display title for
            # every row that doesn't have one yet. Manual sorttitle overrides
            # take effect on the next scan/build.
            try:
                rows = c.execute(
                    "SELECT folder_path, title FROM item_state WHERE sort_title IS NULL OR sort_title = ''"
                ).fetchall()
                for r in rows:
                    st = compute_sort_title(r["title"], None)
                    c.execute(
                        "UPDATE item_state SET sort_title = ? WHERE folder_path = ?",
                        (st, r["folder_path"]),
                    )
            except Exception:
                pass

        # v0.11.11: cache the orphan-companion count on each item_state row so
        # the detail page can decide whether to render the orphans panel
        # without re-walking the season folders on every navigation, and so the
        # library-wide sweep can skip folders that have no orphans without
        # touching disk. The value is refreshed by every scan_series_folder /
        # scan_movie_folder call (see services/scanner.py). NULL is treated as
        # "unknown — show panel only after a fetch confirms it".
        item_cols = {r[1] for r in c.execute("PRAGMA table_info(item_state)").fetchall()}
        if item_cols and "orphan_count" not in item_cols:
            try:
                c.execute("ALTER TABLE item_state ADD COLUMN orphan_count INTEGER")
            except Exception:
                pass
        # Composite index for the library list pill (`status=needs|complete`).
        # The existing single-column indexes don't help when both filters are
        # present together because SQLite still has to merge per row.
        try:
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_item_state_lib_status "
                "ON item_state(library, nfo_status)"
            )
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


def set_binding_secondary(folder_path: str, provider: Optional[str],
                          external_id: Optional[str]) -> None:
    """Set or clear the manual secondary provider id on a binding.

    Pass ``provider=None`` (or empty ``external_id``) to clear.
    Provider must be 'tvdb' or 'tmdb' and must differ from the primary.
    No-op if the folder has no primary binding.
    """
    c = conn()
    with _lock:
        row = c.execute(
            "SELECT provider FROM bindings WHERE folder_path = ?", (folder_path,)
        ).fetchone()
        if not row:
            return
        primary = (row["provider"] or "").lower()
        if not provider or not external_id:
            sec_p, sec_id = None, None
        else:
            p = provider.lower()
            if p not in ("tvdb", "tmdb"):
                raise ValueError("secondary provider must be 'tvdb' or 'tmdb'")
            if p == primary:
                raise ValueError("secondary provider must differ from primary")
            sec_p, sec_id = p, str(external_id)
        c.execute(
            "UPDATE bindings SET secondary_provider = ?, secondary_external_id = ?, updated_at = ? "
            "WHERE folder_path = ?",
            (sec_p, sec_id, int(time.time()), folder_path),
        )


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
        c.execute("DELETE FROM episode_file_overrides WHERE folder_path = ?", (folder_path,))
        c.execute("DELETE FROM active_artwork WHERE folder_path = ?", (folder_path,))
        c.execute("DELETE FROM custom_artwork WHERE folder_path = ?", (folder_path,))
        c.execute("DELETE FROM nfo_overrides WHERE folder_path = ?", (folder_path,))
        cur = c.execute("DELETE FROM item_state WHERE folder_path = ?", (folder_path,))
        return cur.rowcount


def get_item_state(folder_path: str) -> Optional[sqlite3.Row]:
    """Single-row lookup by folder_path. v0.11.11.

    Replaces the legacy pattern of calling list_item_state() and filtering in
    Python — that pulled every library row across the wire on each detail /
    sweep / clean request which dominated request latency on large servers.
    """
    c = conn()
    with _lock:
        return c.execute(
            "SELECT * FROM item_state WHERE folder_path = ?", (folder_path,)
        ).fetchone()


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
    # v0.11.4: order by sort_title (Plex/Sonarr-style), falling back to the
    # display title when sort_title is NULL or empty. NOCASE so "the matrix"
    # and "The Matrix" sort the same.
    sql += (
        " ORDER BY COALESCE(NULLIF(sort_title, ''), title) COLLATE NOCASE,"
        " title COLLATE NOCASE LIMIT ?"
    )
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


def set_library_metadata_source(name: str, source: Optional[str]) -> None:
    """Set per-library metadata source override. Pass None to clear and inherit global."""
    c = conn()
    with _lock:
        norm: Optional[str]
        if source is None:
            norm = None
        else:
            s = str(source).strip().lower()
            if s in ("", "default", "inherit", "global"):
                norm = None
            elif s in ("tvdb", "tmdb"):
                norm = s
            else:
                # Reject unknown values silently to keep API forgiving.
                norm = None
        c.execute("UPDATE libraries SET metadata_source = ? WHERE name = ?", (norm, name))


def delete_library(name: str) -> dict:
    """Forget a library and every database row that belongs to it.

    Removes the libraries row, item_state rows, and the bindings/overrides/
    artwork_selections/episode_overrides for every folder that lived under the
    library. Files on disk (NFOs, artwork, sidecars) are not touched, so
    re-detecting + re-scanning later restores the library from sidecars.
    """
    c = conn()
    summary = {"items": 0, "bindings": 0}
    with _lock:
        # Find all folder paths attributed to this library.
        rows = c.execute(
            "SELECT folder_path FROM item_state WHERE library = ?", (name,)
        ).fetchall()
        folders = [r["folder_path"] for r in rows]
        if folders:
            placeholders = ",".join(["?"] * len(folders))
            for table in (
                "bindings",
                "nfo_overrides",
                "artwork_selections",
                "episode_overrides",
                "episode_file_overrides",
            ):
                cur = c.execute(
                    f"DELETE FROM {table} WHERE folder_path IN ({placeholders})",
                    folders,
                )
                if table == "bindings":
                    summary["bindings"] = cur.rowcount
        cur = c.execute("DELETE FROM item_state WHERE library = ?", (name,))
        summary["items"] = cur.rowcount
        c.execute("DELETE FROM libraries WHERE name = ?", (name,))
    return summary


def get_library(name: str) -> Optional[sqlite3.Row]:
    c = conn()
    with _lock:
        return c.execute("SELECT * FROM libraries WHERE name = ?", (name,)).fetchone()


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


# ---- Per-file episode overrides (v0.10.0) ----------------------------------
#
# Anchors override information to the actual file path on disk. Solves the
# v0.4.0–0.9.x problem where multiple unparsed files in the same folder all
# collapsed to (season=0, episode=0) and only the last selection "won".

def set_episode_file_override(folder_path: str, file_path: str,
                              season: Optional[int],
                              episode: Optional[int],
                              external_id: Optional[str]) -> None:
    c = conn()
    with _lock:
        c.execute(
            """
            INSERT OR REPLACE INTO episode_file_overrides
                (folder_path, file_path, season, episode, external_id, updated_at)
            VALUES (?,?,?,?,?,?)
            """,
            (
                folder_path,
                file_path,
                int(season) if season is not None else None,
                int(episode) if episode is not None else None,
                str(external_id) if external_id else None,
                int(time.time()),
            ),
        )


def clear_episode_file_override(folder_path: str,
                                 file_path: Optional[str] = None) -> int:
    c = conn()
    with _lock:
        if file_path is None:
            cur = c.execute(
                "DELETE FROM episode_file_overrides WHERE folder_path = ?",
                (folder_path,),
            )
        else:
            cur = c.execute(
                "DELETE FROM episode_file_overrides WHERE folder_path = ? AND file_path = ?",
                (folder_path, file_path),
            )
        return cur.rowcount


def get_episode_file_overrides(folder_path: str) -> dict[str, dict]:
    """Return ``{file_path: {season, episode, external_id}}`` for the folder."""
    c = conn()
    with _lock:
        rows = c.execute(
            """
            SELECT file_path, season, episode, external_id
            FROM episode_file_overrides
            WHERE folder_path = ?
            """,
            (folder_path,),
        ).fetchall()
    return {
        r["file_path"]: {
            "season": r["season"],
            "episode": r["episode"],
            "external_id": r["external_id"],
        }
        for r in rows
    }


def rename_episode_file_override(folder_path: str,
                                  old_file_path: str,
                                  new_file_path: str) -> None:
    """Move an override row when a file is renamed on disk.

    Drops any pre-existing row at ``new_file_path`` first so the rename can't
    collide on the (folder_path, file_path) primary key.
    """
    if old_file_path == new_file_path:
        return
    c = conn()
    with _lock:
        c.execute(
            "DELETE FROM episode_file_overrides WHERE folder_path = ? AND file_path = ?",
            (folder_path, new_file_path),
        )
        c.execute(
            """
            UPDATE episode_file_overrides
               SET file_path = ?, updated_at = ?
             WHERE folder_path = ? AND file_path = ?
            """,
            (new_file_path, int(time.time()), folder_path, old_file_path),
        )


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


# ---- Custom tags (v0.8.0) --------------------------------------------------

def list_custom_tags(folder_path: str) -> list[str]:
    """Return the user's custom tags for a folder, ordered by creation time."""
    c = conn()
    with _lock:
        rows = c.execute(
            "SELECT tag FROM custom_tags WHERE folder_path = ? ORDER BY created_at ASC, tag ASC",
            (folder_path,),
        ).fetchall()
    return [r["tag"] for r in rows]


def add_custom_tag(folder_path: str, tag: str) -> bool:
    """Append a custom tag. Case-insensitive duplicates are ignored.

    Returns True if a new row was inserted, False if it already existed (any
    case-insensitive match).
    """
    name = (tag or "").strip()
    if not name:
        return False
    c = conn()
    with _lock:
        existing = c.execute(
            "SELECT 1 FROM custom_tags WHERE folder_path = ? AND tag = ? COLLATE NOCASE",
            (folder_path, name),
        ).fetchone()
        if existing:
            return False
        c.execute(
            "INSERT INTO custom_tags(folder_path, tag, created_at) VALUES (?,?,?)",
            (folder_path, name, int(time.time())),
        )
        return True


def remove_custom_tag(folder_path: str, tag: str) -> int:
    """Remove a custom tag (case-insensitive). Returns the number of rows deleted."""
    name = (tag or "").strip()
    if not name:
        return 0
    c = conn()
    with _lock:
        cur = c.execute(
            "DELETE FROM custom_tags WHERE folder_path = ? AND tag = ? COLLATE NOCASE",
            (folder_path, name),
        )
        return cur.rowcount


def bulk_set_custom_tags(folder_path: str, tags: list[str]) -> None:
    """Replace all custom tags for a folder with the supplied list. Used by sidecar restore."""
    c = conn()
    now = int(time.time())
    seen: set[str] = set()
    with _lock:
        c.execute("DELETE FROM custom_tags WHERE folder_path = ?", (folder_path,))
        for raw in tags or []:
            name = str(raw or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            c.execute(
                "INSERT INTO custom_tags(folder_path, tag, created_at) VALUES (?,?,?)",
                (folder_path, name, now),
            )


# ---- Schedules (v0.8.0) ----------------------------------------------------

def list_schedules() -> list[sqlite3.Row]:
    c = conn()
    with _lock:
        return c.execute(
            "SELECT * FROM schedules ORDER BY id ASC"
        ).fetchall()


def get_schedule(sched_id: int) -> Optional[sqlite3.Row]:
    c = conn()
    with _lock:
        return c.execute(
            "SELECT * FROM schedules WHERE id = ?", (int(sched_id),)
        ).fetchone()


def insert_schedule(*, library: Optional[str], cron: str, action: str,
                    enabled: bool = True) -> int:
    c = conn()
    now = int(time.time())
    with _lock:
        cur = c.execute(
            """
            INSERT INTO schedules(library, cron, action, enabled, created_at, updated_at)
            VALUES (?,?,?,?,?,?)
            """,
            (library, cron, action, 1 if enabled else 0, now, now),
        )
        return int(cur.lastrowid)


def update_schedule(sched_id: int, *, library: Optional[str] = None,
                    cron: Optional[str] = None, action: Optional[str] = None,
                    enabled: Optional[bool] = None) -> None:
    c = conn()
    fields: list[str] = []
    args: list[Any] = []
    if library is not None or library is None:
        # library may be intentionally set to NULL (all-libraries). Distinguish
        # "no change" by passing the sentinel `__unset__` instead of None.
        pass
    # Build dynamic SET clause; treat None as "no change".
    if library is not None:
        fields.append("library = ?")
        args.append(library if library != "" else None)
    if cron is not None:
        fields.append("cron = ?")
        args.append(cron)
    if action is not None:
        fields.append("action = ?")
        args.append(action)
    if enabled is not None:
        fields.append("enabled = ?")
        args.append(1 if enabled else 0)
    if not fields:
        return
    fields.append("updated_at = ?")
    args.append(int(time.time()))
    args.append(int(sched_id))
    with _lock:
        c.execute(f"UPDATE schedules SET {', '.join(fields)} WHERE id = ?", args)


def delete_schedule(sched_id: int) -> int:
    c = conn()
    with _lock:
        cur = c.execute("DELETE FROM schedules WHERE id = ?", (int(sched_id),))
        return cur.rowcount


def update_schedule_run(sched_id: int, *, last_run: int,
                        last_status: str, last_message: Optional[str] = None) -> None:
    c = conn()
    with _lock:
        c.execute(
            """
            UPDATE schedules
               SET last_run = ?, last_status = ?, last_message = ?, updated_at = ?
             WHERE id = ?
            """,
            (int(last_run), last_status, last_message, int(time.time()), int(sched_id)),
        )
