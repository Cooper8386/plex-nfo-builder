"""Plex-compatible NFO XML generation.

Spec: https://support.plex.tv/articles/using-nfo-metadata-files-with-plex/

v0.3.0 change: <thumb>/<fanart><thumb> always reference TVDB CDN URLs (never
local filenames). Plex caches these on the server and they remain valid even
when local files like poster.jpg are present (Plex will prefer the local file
for thumbnail display thanks to its Local Media Assets agent, but the URL
serves as a robust fallback).
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Optional
from xml.dom import minidom
from xml.etree import ElementTree as ET

from .. import __version__
from .artwork import absolutize_tvdb_url, movie_image_urls, series_image_urls
from .tmdb import image_url as _tmdb_image


def _el(parent: ET.Element, tag: str, text: Optional[Any] = None,
        attrib: Optional[dict] = None) -> ET.Element:
    e = ET.SubElement(parent, tag, attrib or {})
    if text is not None and text != "":
        e.text = str(text)
    return e


def _pretty(root: ET.Element, provenance: dict) -> str:
    raw = ET.tostring(root, encoding="utf-8", xml_declaration=False).decode("utf-8")
    dom = minidom.parseString(raw)
    pretty = dom.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    body = pretty.split("?>", 1)[1].lstrip() if "?>" in pretty else pretty
    body_hash = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    header = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<!-- plex-nfo-builder version={__version__} "
        f"generated_at={int(time.time())} "
        f"tvdb_id={provenance.get('tvdb_id', '')} "
        f"content_hash={body_hash} -->\n"
    )
    return header + body


def _t(translation: Optional[dict], field: str, fallback: Optional[str] = None) -> Optional[str]:
    if isinstance(translation, dict):
        v = translation.get(field)
        if v:
            return v
    return fallback


def _ovr(overrides: Optional[dict], scope: str, field: str,
         fallback: Optional[str]) -> Optional[str]:
    """Pick the override value for (scope, field) if non-empty, else `fallback`."""
    if isinstance(overrides, dict):
        scoped = overrides.get(scope)
        if isinstance(scoped, dict):
            v = scoped.get(field)
            if v not in (None, ""):
                return v
    return fallback


# ----- Series ---------------------------------------------------------------

def build_series_nfo(series_extended: dict, *, language: str, fallbacks: list[str],
                     translation: Optional[dict] = None,
                     folder_path: Optional[str] = None,
                     overrides: Optional[dict] = None) -> str:
    s = series_extended
    title = _ovr(overrides, "series", "title",
                 _t(translation, "name", s.get("name")) or "")
    plot = _ovr(overrides, "series", "plot",
                _t(translation, "overview", s.get("overview")) or "")
    original_title = _ovr(overrides, "series", "originaltitle", s.get("name") or title)
    sort_title = _ovr(overrides, "series", "sorttitle", s.get("sortName") or title)
    tagline = _ovr(overrides, "series", "tagline", None)
    year = None
    aired = s.get("firstAired") or s.get("first_aired")
    if aired:
        year = int(str(aired)[:4]) if str(aired)[:4].isdigit() else None

    root = ET.Element("tvshow")
    _el(root, "title", title)
    _el(root, "originaltitle", original_title)
    _el(root, "sorttitle", sort_title)
    _el(root, "plot", plot)
    if tagline:
        _el(root, "tagline", tagline)
    if aired:
        _el(root, "premiered", aired)
        _el(root, "year", year)
    studios = [c.get("name") for c in (s.get("companies") or {}).get("studio", [])] if isinstance(s.get("companies"), dict) else []
    for st in studios:
        if st:
            _el(root, "studio", st)
    network = (s.get("originalNetwork") or {}).get("name") if isinstance(s.get("originalNetwork"), dict) else s.get("network")
    if network:
        _el(root, "studio", network)
    for g in (s.get("genres") or []):
        name = g.get("name") if isinstance(g, dict) else g
        if name:
            _el(root, "genre", name)
    rating_obj = (s.get("ratings") or [{}])[0] if isinstance(s.get("ratings"), list) else None
    if rating_obj:
        _el(root, "mpaa", rating_obj.get("name") or "")
    if s.get("status") and isinstance(s["status"], dict):
        _el(root, "status", s["status"].get("name") or "")
    runtime = s.get("averageRuntime") or s.get("runtime")
    if runtime:
        _el(root, "runtime", runtime)
    # uniqueids
    _el(root, "uniqueid", str(s.get("id") or ""), attrib={"type": "tvdb", "default": "true"})
    rms = s.get("remoteIds") or []
    for rm in rms:
        if isinstance(rm, dict) and rm.get("sourceName"):
            _el(root, "uniqueid", str(rm.get("id") or ""),
                attrib={"type": _provider_slug(rm.get("sourceName"))})

    # Artwork: always TVDB CDN URLs.
    urls = series_image_urls(s, s.get("artworks") or [], prefer_languages=[language, *fallbacks], folder_path=folder_path)
    if urls.get("poster"):
        _el(root, "thumb", urls["poster"], attrib={"aspect": "poster"})
    if urls.get("banner"):
        _el(root, "thumb", urls["banner"], attrib={"aspect": "banner"})
    fanart = ET.SubElement(root, "fanart")
    if urls.get("background"):
        _el(fanart, "thumb", urls["background"])

    # actors
    for c in (s.get("characters") or []):
        if not isinstance(c, dict):
            continue
        actor = ET.SubElement(root, "actor")
        _el(actor, "name", c.get("personName") or "")
        _el(actor, "role", c.get("name") or "")
        if c.get("image"):
            _el(actor, "thumb", absolutize_tvdb_url(c["image"]) or "")

    return _pretty(root, {"tvdb_id": s.get("id")})


# ----- Episode --------------------------------------------------------------

def build_episode_nfo(episode_extended: dict, *, language: str, fallbacks: list[str],
                      translation: Optional[dict] = None,
                      overrides: Optional[dict] = None) -> str:
    e = episode_extended
    scope = f"episode-{e.get('id')}"
    title = _ovr(overrides, scope, "title",
                 _t(translation, "name", e.get("name")) or "")
    plot = _ovr(overrides, scope, "plot",
                _t(translation, "overview", e.get("overview")) or "")

    root = ET.Element("episodedetails")
    _el(root, "title", title)
    if e.get("seasonNumber") is not None:
        _el(root, "season", e.get("seasonNumber"))
    if e.get("number") is not None:
        _el(root, "episode", e.get("number"))
    if e.get("aired"):
        _el(root, "aired", e.get("aired"))
    _el(root, "plot", plot)
    if e.get("runtime"):
        _el(root, "runtime", e.get("runtime"))
    _el(root, "uniqueid", str(e.get("id") or ""), attrib={"type": "tvdb", "default": "true"})
    # episode thumbnail: TVDB CDN URL (Plex caches it; local <stem>-thumb.jpg
    # is also written by the artwork pipeline as a fallback)
    if e.get("image"):
        _el(root, "thumb", absolutize_tvdb_url(e["image"]) or "")
    for c in (e.get("characters") or []):
        if isinstance(c, dict) and c.get("personName"):
            actor = ET.SubElement(root, "actor")
            _el(actor, "name", c["personName"])
            _el(actor, "role", c.get("name") or "")
    return _pretty(root, {"tvdb_id": e.get("id")})


# ----- Movie ----------------------------------------------------------------

def build_movie_nfo(movie_extended: dict, *, language: str, fallbacks: list[str],
                    translation: Optional[dict] = None,
                    folder_path: Optional[str] = None,
                    overrides: Optional[dict] = None) -> str:
    m = movie_extended
    title = _ovr(overrides, "movie", "title",
                 _t(translation, "name", m.get("name")) or "")
    plot = _ovr(overrides, "movie", "plot",
                _t(translation, "overview", m.get("overview")) or "")
    sort_title = _ovr(overrides, "movie", "sorttitle", None)
    tagline = _ovr(overrides, "movie", "tagline", None)
    original_title = _ovr(overrides, "movie", "originaltitle", m.get("name") or title)
    root = ET.Element("movie")
    _el(root, "title", title)
    _el(root, "originaltitle", original_title)
    if sort_title:
        _el(root, "sorttitle", sort_title)
    _el(root, "plot", plot)
    if tagline:
        _el(root, "tagline", tagline)
    if m.get("releases"):
        first = m["releases"][0] if isinstance(m["releases"], list) and m["releases"] else None
        if first and isinstance(first, dict) and first.get("date"):
            _el(root, "premiered", first["date"])
            year = first["date"][:4]
            if year.isdigit():
                _el(root, "year", year)
    if m.get("runtime"):
        _el(root, "runtime", m["runtime"])
    for g in (m.get("genres") or []):
        name = g.get("name") if isinstance(g, dict) else g
        if name:
            _el(root, "genre", name)
    _el(root, "uniqueid", str(m.get("id") or ""), attrib={"type": "tvdb", "default": "true"})
    rms = m.get("remoteIds") or []
    for rm in rms:
        if isinstance(rm, dict) and rm.get("sourceName"):
            _el(root, "uniqueid", str(rm.get("id") or ""),
                attrib={"type": _provider_slug(rm.get("sourceName"))})

    urls = movie_image_urls(m, m.get("artworks") or [], prefer_languages=[language, *fallbacks], folder_path=folder_path)
    if urls.get("poster"):
        _el(root, "thumb", urls["poster"], attrib={"aspect": "poster"})
    if urls.get("banner"):
        _el(root, "thumb", urls["banner"], attrib={"aspect": "banner"})
    fanart = ET.SubElement(root, "fanart")
    if urls.get("background"):
        _el(fanart, "thumb", urls["background"])

    for c in (m.get("characters") or []):
        if isinstance(c, dict) and c.get("personName"):
            actor = ET.SubElement(root, "actor")
            _el(actor, "name", c["personName"])
            _el(actor, "role", c.get("name") or "")
    return _pretty(root, {"tvdb_id": m.get("id")})


def _provider_slug(name: Optional[str]) -> str:
    if not name:
        return "external"
    n = name.lower()
    if "tmdb" in n or "moviedb" in n or "movie database" in n:
        return "tmdb"
    if "imdb" in n:
        return "imdb"
    if "tvdb" in n:
        return "tvdb"
    return n.replace(" ", "")


def has_provenance(nfo_text: str) -> bool:
    return "<!-- plex-nfo-builder" in nfo_text[:2000]


# ----- TMDB-sourced NFOs ----------------------------------------------------
#
# When the user picks TMDB as the metadata source, the data shape we get back
# is different (TMDB v3 JSON). We provide separate builders that produce
# Plex-compatible NFO XML out of TMDB payloads.

def build_series_nfo_tmdb(tv: dict, *, language: str, fallbacks: list[str],
                          folder_path: Optional[str] = None,
                          extra_artwork: Optional[dict] = None,
                          overrides: Optional[dict] = None) -> str:
    title = _ovr(overrides, "series", "title", tv.get("name") or "")
    plot = _ovr(overrides, "series", "plot", tv.get("overview") or "")
    original_title = _ovr(overrides, "series", "originaltitle", tv.get("original_name") or title)
    sort_title = _ovr(overrides, "series", "sorttitle", title)
    tagline = _ovr(overrides, "series", "tagline", tv.get("tagline"))
    aired = tv.get("first_air_date")
    year = int(str(aired)[:4]) if aired and str(aired)[:4].isdigit() else None

    root = ET.Element("tvshow")
    _el(root, "title", title)
    _el(root, "originaltitle", original_title)
    _el(root, "sorttitle", sort_title)
    _el(root, "plot", plot)
    if tagline:
        _el(root, "tagline", tagline)
    if aired:
        _el(root, "premiered", aired)
    if year:
        _el(root, "year", year)
    for net in (tv.get("networks") or []):
        if isinstance(net, dict) and net.get("name"):
            _el(root, "studio", net["name"])
    for g in (tv.get("genres") or []):
        if isinstance(g, dict) and g.get("name"):
            _el(root, "genre", g["name"])
    if tv.get("status"):
        _el(root, "status", tv.get("status"))
    runtimes = tv.get("episode_run_time") or []
    if runtimes:
        _el(root, "runtime", runtimes[0])
    # uniqueids
    _el(root, "uniqueid", str(tv.get("id") or ""), attrib={"type": "tmdb", "default": "true"})
    ext = tv.get("external_ids") or {}
    if ext.get("tvdb_id"):
        _el(root, "uniqueid", str(ext["tvdb_id"]), attrib={"type": "tvdb"})
    if ext.get("imdb_id"):
        _el(root, "uniqueid", str(ext["imdb_id"]), attrib={"type": "imdb"})

    # Artwork: respect per-folder selections, else use TMDB poster + backdrop URLs.
    extra = extra_artwork or {}
    poster = extra.get("poster") or _tmdb_image(tv.get("poster_path"), "original")
    background = extra.get("background") or _tmdb_image(tv.get("backdrop_path"), "original")
    banner = extra.get("banner")
    if poster:
        _el(root, "thumb", poster, attrib={"aspect": "poster"})
    if banner:
        _el(root, "thumb", banner, attrib={"aspect": "banner"})
    fanart = ET.SubElement(root, "fanart")
    if background:
        _el(fanart, "thumb", background)

    cast = ((tv.get("credits") or {}).get("cast") or [])
    for c in cast[:30]:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        actor = ET.SubElement(root, "actor")
        _el(actor, "name", c.get("name"))
        _el(actor, "role", c.get("character") or "")
        if c.get("profile_path"):
            _el(actor, "thumb", _tmdb_image(c["profile_path"], "w185"))

    return _pretty(root, {"tvdb_id": tv.get("external_ids", {}).get("tvdb_id") or ""})


def build_episode_nfo_tmdb(ep: dict, *, language: str, fallbacks: list[str],
                            overrides: Optional[dict] = None) -> str:
    scope = f"episode-{ep.get('id')}"
    title = _ovr(overrides, scope, "title", ep.get("name") or "")
    plot = _ovr(overrides, scope, "plot", ep.get("overview") or "")
    root = ET.Element("episodedetails")
    _el(root, "title", title)
    if ep.get("season_number") is not None:
        _el(root, "season", ep.get("season_number"))
    if ep.get("episode_number") is not None:
        _el(root, "episode", ep.get("episode_number"))
    if ep.get("air_date"):
        _el(root, "aired", ep.get("air_date"))
    _el(root, "plot", plot)
    if ep.get("runtime"):
        _el(root, "runtime", ep.get("runtime"))
    _el(root, "uniqueid", str(ep.get("id") or ""), attrib={"type": "tmdb", "default": "true"})
    if ep.get("still_path"):
        _el(root, "thumb", _tmdb_image(ep["still_path"], "original"))
    return _pretty(root, {"tvdb_id": ""})


def build_movie_nfo_tmdb(mv: dict, *, language: str, fallbacks: list[str],
                         folder_path: Optional[str] = None,
                         extra_artwork: Optional[dict] = None,
                         overrides: Optional[dict] = None) -> str:
    title = _ovr(overrides, "movie", "title", mv.get("title") or mv.get("name") or "")
    plot = _ovr(overrides, "movie", "plot", mv.get("overview") or "")
    original_title = _ovr(overrides, "movie", "originaltitle", mv.get("original_title") or title)
    sort_title = _ovr(overrides, "movie", "sorttitle", None)
    tagline = _ovr(overrides, "movie", "tagline", mv.get("tagline"))
    aired = mv.get("release_date")
    year = int(str(aired)[:4]) if aired and str(aired)[:4].isdigit() else None

    root = ET.Element("movie")
    _el(root, "title", title)
    _el(root, "originaltitle", original_title)
    if sort_title:
        _el(root, "sorttitle", sort_title)
    _el(root, "plot", plot)
    if tagline:
        _el(root, "tagline", tagline)
    if aired:
        _el(root, "premiered", aired)
    if year:
        _el(root, "year", year)
    if mv.get("runtime"):
        _el(root, "runtime", mv.get("runtime"))
    for g in (mv.get("genres") or []):
        if isinstance(g, dict) and g.get("name"):
            _el(root, "genre", g["name"])
    for s in (mv.get("production_companies") or []):
        if isinstance(s, dict) and s.get("name"):
            _el(root, "studio", s["name"])
    _el(root, "uniqueid", str(mv.get("id") or ""), attrib={"type": "tmdb", "default": "true"})
    if mv.get("imdb_id"):
        _el(root, "uniqueid", str(mv.get("imdb_id")), attrib={"type": "imdb"})
    ext = mv.get("external_ids") or {}
    if ext.get("tvdb_id"):
        _el(root, "uniqueid", str(ext["tvdb_id"]), attrib={"type": "tvdb"})

    extra = extra_artwork or {}
    poster = extra.get("poster") or _tmdb_image(mv.get("poster_path"), "original")
    background = extra.get("background") or _tmdb_image(mv.get("backdrop_path"), "original")
    banner = extra.get("banner")
    if poster:
        _el(root, "thumb", poster, attrib={"aspect": "poster"})
    if banner:
        _el(root, "thumb", banner, attrib={"aspect": "banner"})
    fanart = ET.SubElement(root, "fanart")
    if background:
        _el(fanart, "thumb", background)

    cast = ((mv.get("credits") or {}).get("cast") or [])
    for c in cast[:30]:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        actor = ET.SubElement(root, "actor")
        _el(actor, "name", c.get("name"))
        _el(actor, "role", c.get("character") or "")
        if c.get("profile_path"):
            _el(actor, "thumb", _tmdb_image(c["profile_path"], "w185"))

    return _pretty(root, {"tvdb_id": (mv.get("external_ids") or {}).get("tvdb_id") or ""})


# ----- Season ---------------------------------------------------------------
#
# Plex reads optional season metadata from `<show>/Season XX/season.nfo`. We
# emit it whenever a per-season override exists (or when the metadata source
# provides a season-level title/plot we want to surface).

def build_season_nfo(season_number: int, *,
                     base_title: Optional[str] = None,
                     base_plot: Optional[str] = None,
                     base_aired: Optional[str] = None,
                     overrides: Optional[dict] = None,
                     external_id: Optional[str] = None,
                     provider: str = "tvdb") -> str:
    scope = f"season-{int(season_number):02d}"
    title = _ovr(overrides, scope, "title", base_title or f"Season {int(season_number)}")
    plot = _ovr(overrides, scope, "plot", base_plot or "")
    sort_title = _ovr(overrides, scope, "sorttitle", None)
    tagline = _ovr(overrides, scope, "tagline", None)

    root = ET.Element("season")
    _el(root, "title", title)
    if sort_title:
        _el(root, "sorttitle", sort_title)
    _el(root, "seasonnumber", int(season_number))
    if base_aired:
        _el(root, "premiered", base_aired)
    _el(root, "plot", plot)
    if tagline:
        _el(root, "tagline", tagline)
    if external_id:
        _el(root, "uniqueid", str(external_id), attrib={"type": provider, "default": "true"})
    return _pretty(root, {"tvdb_id": external_id or ""})
