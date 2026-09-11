"""Optional OMDb ratings by exact IMDb identity; native TMDB ratings need no extra key.

API: https://www.omdbapi.com/ (including series ID + Season/Episode lookup).
NFO: https://kodi.wiki/view/NFO_files/Movies#nfo_Tags
"""
from __future__ import annotations

import math
import re
from xml.etree import ElementTree as ET

import httpx

from ..config import effective_omdb_credentials, get_user_settings
from ..db import cache_get, cache_set

_client: httpx.AsyncClient | None = None
_IMDB_ID = re.compile(r"tt\d{7,}")


def imdb_id(record: dict) -> str | None:
    external = record.get("external_ids") or {}
    candidates = [record.get("imdb_id"), record.get("imdbId"), external.get("imdb_id")]
    candidates.extend(remote.get("id") for remote in record.get("remoteIds") or []
                      if isinstance(remote, dict) and "imdb" in str(remote.get("sourceName", "")).lower())
    return next((str(value) for value in candidates if value and _IMDB_ID.fullmatch(str(value))), None)


def _number(value, maximum: int) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and 0 <= number <= maximum else None
    except (TypeError, ValueError):
        return None


def normalise_omdb(payload: dict) -> dict[str, dict]:
    ratings: dict[str, dict] = {}
    score = _number(payload.get("imdbRating"), 10)
    if score is not None:
        ratings["imdb"] = {"value": score, "max": 10}
        votes = str(payload.get("imdbVotes", "")).replace(",", "")
        if votes.isdigit():
            ratings["imdb"]["votes"] = int(votes)
    for item in payload.get("Ratings") or []:
        if not isinstance(item, dict):
            continue
        source, raw = item.get("Source"), str(item.get("Value") or "")
        if source == "Internet Movie Database" and "imdb" not in ratings and raw.endswith("/10"):
            name, maximum, raw = "imdb", 10, raw[:-3]
        elif source == "Rotten Tomatoes" and raw.endswith("%"):
            name, maximum, raw = "tomatometerallcritics", 100, raw[:-1]
        elif source == "Metacritic" and raw.endswith("/100"):
            name, maximum, raw = "metacritic", 100, raw[:-4]
        else:
            continue
        score = _number(raw, maximum)
        if score is not None:
            ratings[name] = {"value": score, "max": maximum}
    return ratings


async def _fetch(params: dict[str, str], api_key: str, *, force: bool) -> dict:
    global _client
    key = "omdb:" + ":".join(f"{name}={value}" for name, value in sorted(params.items()))
    ttl = int(get_user_settings().cache_ttl_hours * 3600)
    if not force and ttl != 0:
        cached = cache_get(key)
        if cached is not None:
            return cached
    if _client is None:
        _client = httpx.AsyncClient(base_url="https://www.omdbapi.com", timeout=15.0)
    response = await _client.get("/", params={**params, "apikey": api_key})
    # Do not include response bodies or credential-bearing URLs in failures.
    if response.status_code != 200:
        raise RuntimeError(f"OMDb HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Invalid OMDb response")
    if payload.get("Response") == "False":
        if "not found" not in str(payload.get("Error", "")).lower():
            raise RuntimeError("OMDb rejected the lookup; check API key and quota")
        payload = {}
    if ttl != 0:
        cache_set(key, payload, ttl=ttl if payload else (min(ttl, 3600) if ttl > 0 else 3600))
    return payload


async def hydrate_ratings(record: dict, *, kind: str, provider: str, log,
                          force: bool = False, series: dict | None = None) -> None:
    """Add available external scores without failing a build or guessing by title."""
    api_key = effective_omdb_credentials()
    if not api_key:
        return
    ident = imdb_id(record)
    params = {"i": ident, "type": kind} if ident else {}
    if not ident and kind == "episode" and series:
        ident = imdb_id(series)
        season = record.get("season_number" if provider == "tmdb" else "seasonNumber")
        episode = record.get("episode_number" if provider == "tmdb" else "number")
        if ident and season is not None and episode is not None:
            params = {"i": ident, "Season": str(season), "Episode": str(episode)}
    if not params:
        return
    try:
        payload = await _fetch(params, api_key, force=force)
        if not payload:
            return
        discovered_id = str(payload.get("imdbID") or "")
        if not _IMDB_ID.fullmatch(discovered_id) or payload.get("Type") != kind:
            return
        if "Season" in params:
            # Never inherit the series rating when an episode is unavailable.
            if (payload.get("seriesID") != ident or str(payload.get("Season")) != params["Season"]
                    or str(payload.get("Episode")) != params["Episode"]):
                return
        elif discovered_id != ident:
            return
        record["_ratings"] = normalise_omdb(payload)
        record["imdb_id"] = discovered_id
    except Exception as error:
        log.warning("OMDb ratings unavailable for {} ({}); keeping provider metadata", ident, type(error).__name__)


def write_ratings(root: ET.Element, record: dict, *, provider: str) -> None:
    ratings = dict(record.get("_ratings") or {})
    if provider == "tmdb":
        score = _number(record.get("vote_average"), 10)
        votes = _number(record.get("vote_count"), 2**63 - 1)
        if score is not None and votes is not None and votes > 0:
            ratings["themoviedb"] = {"value": score, "max": 10, "votes": int(votes)}
    # TVDB's `score` is popularity, not a user rating. Never export it as one.
    if not ratings:
        return
    default = next((source for source in ("imdb", "themoviedb") if source in ratings), next(iter(ratings)))
    container = ET.SubElement(root, "ratings")
    for name, rating in ratings.items():
        node = ET.SubElement(container, "rating", name=name, max=str(rating["max"]),
                             default="true" if name == default else "false")
        ET.SubElement(node, "value").text = f"{rating['value']:g}"
        if "votes" in rating:
            ET.SubElement(node, "votes").text = str(rating["votes"])
    # Legacy NFO readers use a 0–10 default and a 0–100 critic rating.
    selected = ratings[default]
    ET.SubElement(root, "rating").text = f"{selected['value'] * 10 / selected['max']:g}"
    if "votes" in selected:
        ET.SubElement(root, "votes").text = str(selected["votes"])
    if "tomatometerallcritics" in ratings:
        ET.SubElement(root, "criticrating").text = f"{ratings['tomatometerallcritics']['value']:g}"


async def close_client() -> None:
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()
