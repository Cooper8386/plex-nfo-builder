"""Auto/manual matching against TVDB or TMDB."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger
from rapidfuzz import fuzz

from .. import db
from ..config import effective_metadata_source, get_user_settings
from .parser import parse_folder_name, parse_movie_filename, is_video
from .tmdb import get_client as get_tmdb_client
from .tvdb import get_client


async def auto_match_series(folder: Path, language: Optional[str] = None,
                            threshold: int = 85) -> Optional[dict]:
    """Match series based on folder name. Returns the chosen TVDB record or None."""
    pf = parse_folder_name(folder.name)
    # 1. Folder-id shortcut
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

    # 2. Search
    client = get_client()
    try:
        results = await client.search(pf.title, type_="series", year=pf.year,
                                      language=language, limit=20)
    except Exception as e:
        logger.warning("TVDB search failed for {}: {}", pf.title, e)
        return None

    best = _pick_best(results, pf.title, pf.year)
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
    # try filename id tag first (your movies are tmdb-tagged)
    main_video = next((f for f in folder.iterdir() if f.is_file() and is_video(f)), None)
    if main_video:
        pm = parse_movie_filename(main_video)
        provider = pm.provider or pf.provider
        eid = pm.external_id or pf.external_id
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
        # fallback: store TMDB binding so NFO can still emit tmdb uniqueid
        if provider == "tmdb" and eid:
            db.upsert_binding(str(folder), "movie", "tmdb", eid,
                              title=pm.title or pf.title, year=pm.year or pf.year, language=language, respect_lock=True)
            # search TVDB by name to enrich
    client = get_client()
    try:
        results = await client.search(pf.title, type_="movie", year=pf.year,
                                      language=language, limit=20)
    except Exception as e:
        logger.warning("TVDB search failed for {}: {}", pf.title, e)
        return None
    best = _pick_best(results, pf.title, pf.year)
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
        if year and r.get("year") and str(year) == str(r.get("year")):
            score += 5
        r["_score"] = min(score, 100)
        scored.append((r["_score"], r))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


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
    """
    if provider is None:
        provider = effective_metadata_source(library)
    if provider == "tmdb":
        c = get_tmdb_client()
        results = await c.search(query, type_="tv" if type_ == "series" else "movie",
                                  year=year, language=language, limit=25)
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


# ---- TMDB matching ---------------------------------------------------------

async def auto_match_series_tmdb(folder: Path, language: Optional[str] = None,
                                  threshold: int = 85) -> Optional[dict]:
    pf = parse_folder_name(folder.name)
    client = get_tmdb_client()
    # Folder-id shortcut for tmdb
    if pf.provider == "tmdb" and pf.external_id and pf.external_id.isdigit():
        try:
            data = await client.tv_details(pf.external_id, language=language)
            if data:
                db.upsert_binding(str(folder), "series", "tmdb", str(data.get("id")),
                                  title=data.get("name"), year=pf.year, language=language, respect_lock=True)
                return data
        except Exception as e:
            logger.warning("TMDB tv_details {} failed: {}", pf.external_id, e)
    try:
        results = await client.search(pf.title, type_="tv", year=pf.year,
                                       language=language, limit=20)
    except Exception as e:
        logger.warning("TMDB search failed for {}: {}", pf.title, e)
        return None
    best = _pick_best_tmdb(results, pf.title, pf.year, kind="tv")
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
    main_video = next((f for f in folder.iterdir() if f.is_file() and is_video(f)), None)
    client = get_tmdb_client()
    if main_video:
        pm = parse_movie_filename(main_video)
        provider = pm.provider or pf.provider
        eid = pm.external_id or pf.external_id
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
    try:
        results = await client.search(pf.title, type_="movie", year=pf.year,
                                       language=language, limit=20)
    except Exception as e:
        logger.warning("TMDB search failed for {}: {}", pf.title, e)
        return None
    best = _pick_best_tmdb(results, pf.title, pf.year, kind="movie")
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
        if year and date and str(date)[:4] == str(year):
            score += 5
        r["_score"] = min(score, 100)
        scored.append((r["_score"], r))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]
