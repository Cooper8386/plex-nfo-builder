"""Auto/manual matching against TVDB or TMDB.

v0.9.0 hardening:

* When a folder name (or movie filename) carries an explicit
  ``{provider-id}`` tag we use it and **return early** \u2014 no fuzzy search,
  no fallthrough that could overwrite the binding with a noisier hit.
* Auto-match is now kind-aware: the caller's library kind is treated as a
  hint; if the folder content tells us otherwise (e.g. a Radarr movie
  sitting in an anime/TV library), we route to the movie matcher instead
  of calling ``tv_details`` on a movie TMDB ID (which 404s).
* Year filter is no longer a hard filter: searches retry without the year
  if the first pass yields nothing or only weak matches \u2014 anime release
  years often disagree across DBs by \u00b11.
* Year bonus increased from +5 to +15 so a correctly-dated candidate
  beats a same-titled remake that's a year off.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger
from rapidfuzz import fuzz

from .. import db
from ..config import effective_metadata_source, get_user_settings
from .parser import (
    detect_season_dirs,
    folder_looks_like_movie,
    is_video,
    parse_folder_name,
    parse_movie_filename,
)
from .tmdb import get_client as get_tmdb_client
from .tvdb import get_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _folder_kind(folder: Path) -> Optional[str]:
    """Return ``\"series\"``/``\"movie\"`` based on folder content, else ``None``.

    Mirrors ``scanner.scan_library``'s per-folder routing so matchers don't
    need the library kind passed in.
    """
    if detect_season_dirs(folder):
        return "series"
    if folder_looks_like_movie(folder):
        return "movie"
    return None


# ---------------------------------------------------------------------------
# TVDB matching
# ---------------------------------------------------------------------------


async def auto_match_series(folder: Path, language: Optional[str] = None,
                            threshold: int = 85) -> Optional[dict]:
    """Match series based on folder name. Returns the chosen TVDB record or None."""
    pf = parse_folder_name(folder.name)

    # 0. If the folder content actually looks like a movie, redirect to the
    #    movie matcher so we don't ``tv_details`` a movie id (404).
    if _folder_kind(folder) == "movie":
        logger.info("auto_match_series: {} looks like a movie \u2014 routing to movie matcher", folder.name)
        return await auto_match_movie(folder, language=language, threshold=threshold)

    # 1. Folder-id shortcut \u2014 trust the explicit tag and return early.
    if pf.provider == "tvdb" and pf.external_id and pf.external_id.isdigit():
        client = get_client()
        try:
            data = await client.series_extended(pf.external_id)
            if data:
                db.upsert_binding(str(folder), "series", "tvdb", str(data.get("id")),
                                  title=data.get("name"), year=pf.year, language=language, respect_lock=True)
                return data
        except Exception as e:
            logger.warning("series_extended {} failed: {}", pf.external_id, e)

    # If folder is tagged with a TMDB id but we're in TVDB mode, store a
    # TMDB binding so NFO can still emit the tmdb uniqueid \u2014 then return.
    if pf.provider == "tmdb" and pf.external_id and pf.external_id.isdigit():
        db.upsert_binding(str(folder), "series", "tmdb", pf.external_id,
                          title=pf.title, year=pf.year, language=language, respect_lock=True)
        # Try to enrich via TMDB tv_details for a nicer title in the binding.
        try:
            t = get_tmdb_client()
            data = await t.tv_details(pf.external_id, language=language)
            if data:
                db.upsert_binding(str(folder), "series", "tmdb", str(data.get("id")),
                                  title=data.get("name"), year=pf.year, language=language, respect_lock=True)
                return data
        except Exception:
            pass
        return {"id": pf.external_id, "name": pf.title, "year": pf.year}

    # 2. Search
    client = get_client()
    best = await _search_with_year_fallback(
        lambda yr: client.search(pf.title, type_="series", year=yr,
                                 language=language, limit=20),
        title=pf.title, year=pf.year, picker=_pick_best,
    )
    if not best:
        return None
    score = best.get("_score", 0)
    if score < threshold:
        logger.info("Auto-match score {} < threshold {} for '{}'", score, threshold, pf.title)
        return None
    tvdb_id = best.get("tvdb_id") or best.get("id")
    if not tvdb_id:
        return None
    full = await client.series_extended(str(tvdb_id))
    db.upsert_binding(str(folder), "series", "tvdb", str(full.get("id") or tvdb_id),
                      title=full.get("name"), year=pf.year, language=language, respect_lock=True)
    return full


async def auto_match_movie(folder: Path, language: Optional[str] = None,
                           threshold: int = 85) -> Optional[dict]:
    pf = parse_folder_name(folder.name)
    # Folder-id shortcut first \u2014 the folder name is the most reliable signal.
    if pf.provider == "tvdb" and pf.external_id and pf.external_id.isdigit():
        client = get_client()
        try:
            data = await client.movie_extended(pf.external_id)
            if data:
                db.upsert_binding(str(folder), "movie", "tvdb", str(data.get("id")),
                                  title=data.get("name"), year=pf.year, language=language, respect_lock=True)
                return data
        except Exception as e:
            logger.warning("movie_extended {} failed: {}", pf.external_id, e)
    if pf.provider == "tmdb" and pf.external_id and pf.external_id.isdigit():
        # Trust the folder tag and return early. Try to enrich via TMDB.
        try:
            t = get_tmdb_client()
            data = await t.movie_details(pf.external_id, language=language)
            if data:
                db.upsert_binding(str(folder), "movie", "tmdb", str(data.get("id")),
                                  title=data.get("title") or data.get("name"),
                                  year=pf.year, language=language, respect_lock=True)
                return data
        except Exception:
            pass
        db.upsert_binding(str(folder), "movie", "tmdb", pf.external_id,
                          title=pf.title, year=pf.year, language=language, respect_lock=True)
        return {"id": pf.external_id, "name": pf.title, "year": pf.year}

    # File-tag shortcut: Radarr drops {tmdb-...} into the filename too.
    main_video = next((f for f in folder.iterdir() if f.is_file() and is_video(f)), None)
    if main_video:
        pm = parse_movie_filename(main_video)
        provider = pm.provider
        eid = pm.external_id
        if provider == "tvdb" and eid and eid.isdigit():
            client = get_client()
            try:
                data = await client.movie_extended(eid)
                if data:
                    db.upsert_binding(str(folder), "movie", "tvdb", str(data.get("id")),
                                      title=data.get("name"), year=pm.year, language=language, respect_lock=True)
                    return data
            except Exception:
                pass
        if provider == "tmdb" and eid and eid.isdigit():
            try:
                t = get_tmdb_client()
                data = await t.movie_details(eid, language=language)
                if data:
                    db.upsert_binding(str(folder), "movie", "tmdb", str(data.get("id")),
                                      title=data.get("title") or data.get("name"),
                                      year=pm.year or pf.year, language=language, respect_lock=True)
                    return data
            except Exception:
                pass
            db.upsert_binding(str(folder), "movie", "tmdb", eid,
                              title=pm.title or pf.title, year=pm.year or pf.year,
                              language=language, respect_lock=True)
            return {"id": eid, "name": pm.title or pf.title, "year": pm.year or pf.year}

    # 2. TVDB search fallback (movies)
    client = get_client()
    best = await _search_with_year_fallback(
        lambda yr: client.search(pf.title, type_="movie", year=yr,
                                 language=language, limit=20),
        title=pf.title, year=pf.year, picker=_pick_best,
    )
    if not best:
        return None
    if best.get("_score", 0) < threshold:
        return None
    tvdb_id = best.get("tvdb_id") or best.get("id")
    if not tvdb_id:
        return None
    full = await client.movie_extended(str(tvdb_id))
    db.upsert_binding(str(folder), "movie", "tvdb", str(full.get("id") or tvdb_id),
                      title=full.get("name"), year=pf.year, language=language, respect_lock=True)
    return full


def _pick_best(results: list[dict], title: str, year: Optional[int]) -> Optional[dict]:
    if not results:
        return None
    scored: list[tuple[int, dict]] = []
    for r in results:
        name = r.get("name") or r.get("translations", {}).get("eng") or r.get("title") or ""
        if not name:
            continue
        score = fuzz.token_set_ratio(title.lower(), name.lower())
        if year and r.get("year"):
            try:
                ry = int(r.get("year"))
                if ry == year:
                    score += 15
                elif abs(ry - year) == 1:
                    score += 5  # off-by-one is common for anime
            except (TypeError, ValueError):
                pass
        r["_score"] = min(score, 100)
        scored.append((r["_score"], r))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


# ---------------------------------------------------------------------------
# Manual search
# ---------------------------------------------------------------------------


async def manual_search(query: str, type_: str = "series",
                        year: Optional[int] = None,
                        language: Optional[str] = None,
                        provider: Optional[str] = None,
                        library: Optional[str] = None) -> list[dict]:
    """Manual search.

    `provider` defaults to the effective metadata source for ``library`` (the
    library override if set, otherwise the global setting). Returns a list of
    dicts in a normalised shape:
      {provider, id, name, year, image_url, overview}

    v0.9.0: when a year is supplied but TMDB/TVDB returns nothing, we retry
    once without the year so users searching for an off-by-one anime title
    still see candidates instead of an empty list.
    """
    if provider is None:
        provider = effective_metadata_source(library)
    if provider == "tmdb":
        c = get_tmdb_client()
        tmdb_kind = "tv" if type_ == "series" else "movie"
        results = await c.search(query, type_=tmdb_kind, year=year, language=language, limit=25)
        if not results and year:
            results = await c.search(query, type_=tmdb_kind, year=None, language=language, limit=25)
        from .tmdb import image_url as _img
        out: list[dict] = []
        for r in results:
            name = r.get("name") or r.get("title")
            date = r.get("first_air_date") or r.get("release_date") or ""
            yr = int(date[:4]) if date[:4].isdigit() else None
            out.append({
                "provider": "tmdb",
                "id": r.get("id"),
                "name": name,
                "year": yr,
                "image_url": _img(r.get("poster_path"), "w342"),
                "overview": r.get("overview"),
            })
        return out
    client = get_client()
    raw = await client.search(query, type_=type_, year=year, language=language, limit=25)
    if not raw and year:
        raw = await client.search(query, type_=type_, year=None, language=language, limit=25)
    from .artwork import absolutize_tvdb_url
    out2: list[dict] = []
    for r in raw:
        name = r.get("name") or r.get("title")
        out2.append({
            "provider": "tvdb",
            "id": r.get("tvdb_id") or r.get("id"),
            "name": name,
            "year": int(r.get("year")) if r.get("year") and str(r.get("year")).isdigit() else None,
            "image_url": absolutize_tvdb_url(r.get("image_url") or r.get("image")),
            "overview": r.get("overview"),
            "tvdb_id": r.get("tvdb_id") or r.get("id"),
        })
    return out2


# ---------------------------------------------------------------------------
# TMDB matching
# ---------------------------------------------------------------------------


async def auto_match_series_tmdb(folder: Path, language: Optional[str] = None,
                                  threshold: int = 85) -> Optional[dict]:
    pf = parse_folder_name(folder.name)
    client = get_tmdb_client()

    # Route mismatched folders to the movie matcher (e.g. a Radarr movie
    # sitting in a TV library would otherwise hit ``tv_details(movie_id)``
    # which 404s).
    if _folder_kind(folder) == "movie":
        logger.info("auto_match_series_tmdb: {} looks like a movie \u2014 routing to movie matcher", folder.name)
        return await auto_match_movie_tmdb(folder, language=language, threshold=threshold)

    # Folder-id shortcut \u2014 trust the tag and return early on success.
    # Mirror of the movie matcher's 404 fallback: retry as a movie id if
    # tv_details 404s. v0.9.1.
    if pf.provider == "tmdb" and pf.external_id and pf.external_id.isdigit():
        try:
            data = await client.tv_details(pf.external_id, language=language)
            if data:
                db.upsert_binding(str(folder), "series", "tmdb", str(data.get("id")),
                                  title=data.get("name"), year=pf.year, language=language, respect_lock=True)
                return data
        except Exception as e:
            logger.warning("TMDB tv_details {} failed: {} \u2014 retrying as movie id", pf.external_id, e)
            try:
                mv = await client.movie_details(pf.external_id, language=language)
                if mv:
                    db.upsert_binding(str(folder), "movie", "tmdb", str(mv.get("id")),
                                      title=mv.get("title") or mv.get("name"),
                                      year=pf.year, language=language, respect_lock=True)
                    return mv
            except Exception as e2:
                logger.warning("TMDB movie_details fallback for {} failed: {}", pf.external_id, e2)
            db.upsert_binding(str(folder), "series", "tmdb", pf.external_id,
                              title=pf.title, year=pf.year, language=language, respect_lock=True)
            return {"id": pf.external_id, "name": pf.title, "year": pf.year}

    best = await _search_with_year_fallback(
        lambda yr: client.search(pf.title, type_="tv", year=yr,
                                  language=language, limit=20),
        title=pf.title, year=pf.year,
        picker=lambda r, t, y: _pick_best_tmdb(r, t, y, kind="tv"),
    )
    if not best:
        return None
    if best.get("_score", 0) < threshold:
        return None
    full = await client.tv_details(best["id"], language=language)
    db.upsert_binding(str(folder), "series", "tmdb", str(full.get("id") or best["id"]),
                      title=full.get("name"), year=pf.year, language=language, respect_lock=True)
    return full


async def auto_match_movie_tmdb(folder: Path, language: Optional[str] = None,
                                 threshold: int = 85) -> Optional[dict]:
    pf = parse_folder_name(folder.name)
    client = get_tmdb_client()

    # Folder-tag fast path.
    if pf.provider == "tmdb" and pf.external_id and pf.external_id.isdigit():
        try:
            data = await client.movie_details(pf.external_id, language=language)
            if data:
                db.upsert_binding(str(folder), "movie", "tmdb", str(data.get("id")),
                                  title=data.get("title") or data.get("name"),
                                  year=pf.year, language=language, respect_lock=True)
                return data
        except Exception as e:
            logger.warning("TMDB movie_details {} failed: {}", pf.external_id, e)
            db.upsert_binding(str(folder), "movie", "tmdb", pf.external_id,
                              title=pf.title, year=pf.year, language=language, respect_lock=True)
            return {"id": pf.external_id, "name": pf.title, "year": pf.year}

    main_video = next((f for f in folder.iterdir() if f.is_file() and is_video(f)), None)
    if main_video:
        pm = parse_movie_filename(main_video)
        provider = pm.provider
        eid = pm.external_id
        if provider == "tmdb" and eid and eid.isdigit():
            try:
                data = await client.movie_details(eid, language=language)
                if data:
                    db.upsert_binding(str(folder), "movie", "tmdb", str(data.get("id")),
                                      title=data.get("title") or data.get("name"),
                                      year=pm.year or pf.year, language=language,
                                      respect_lock=True)
                    return data
            except Exception:
                pass

    best = await _search_with_year_fallback(
        lambda yr: client.search(pf.title, type_="movie", year=yr,
                                  language=language, limit=20),
        title=pf.title, year=pf.year,
        picker=lambda r, t, y: _pick_best_tmdb(r, t, y, kind="movie"),
    )
    if not best:
        return None
    if best.get("_score", 0) < threshold:
        return None
    full = await client.movie_details(best["id"], language=language)
    db.upsert_binding(str(folder), "movie", "tmdb", str(full.get("id") or best["id"]),
                      title=full.get("title") or full.get("name"), year=pf.year,
                      language=language, respect_lock=True)
    return full


def _pick_best_tmdb(results: list[dict], title: str, year: Optional[int],
                    kind: str) -> Optional[dict]:
    if not results:
        return None
    scored: list[tuple[int, dict]] = []
    for r in results:
        name = r.get("name") or r.get("title") or r.get("original_name") or r.get("original_title") or ""
        if not name:
            continue
        score = fuzz.token_set_ratio(title.lower(), name.lower())
        date = r.get("first_air_date") if kind == "tv" else r.get("release_date")
        if year and date and len(str(date)) >= 4 and str(date)[:4].isdigit():
            ry = int(str(date)[:4])
            if ry == year:
                score += 15
            elif abs(ry - year) == 1:
                score += 5
        # Mild popularity tiebreaker so a popular hit beats an obscure
        # same-title match when both score equally.
        try:
            pop = float(r.get("popularity") or 0.0)
            score += min(int(pop / 50), 3)
        except (TypeError, ValueError):
            pass
        r["_score"] = min(score, 100)
        scored.append((r["_score"], r))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


# ---------------------------------------------------------------------------
# Search retry helper
# ---------------------------------------------------------------------------


async def _search_with_year_fallback(search_fn, *, title: str, year: Optional[int], picker):
    """Run ``search_fn(year)`` and rerun without ``year`` if results are weak.

    ``search_fn`` is an async callable taking a ``year`` (Optional[int]).
    ``picker`` is the synchronous picker used to score results.
    """
    try:
        results = await search_fn(year) if year else await search_fn(None)
    except Exception as e:
        logger.warning("search failed for {!r} ({}): {}", title, year, e)
        return None
    best = picker(results, title, year)
    # Retry without the year filter when:
    #   - the year-filtered search returned nothing, or
    #   - the best year-filtered hit is weak (<70). The without-year search
    #     might surface the correct title with the year just in metadata,
    #     where the +/- 1 year bonus still helps.
    if year and (not best or (best.get("_score", 0) < 70)):
        try:
            results2 = await search_fn(None)
        except Exception:
            results2 = []
        best2 = picker(results2, title, year)
        if best2 and (not best or best2.get("_score", 0) > best.get("_score", 0)):
            best = best2
    return best
