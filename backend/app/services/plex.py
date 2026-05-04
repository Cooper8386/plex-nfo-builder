"""Plex Media Server integration (v0.6.0).

Only two things are needed from Plex:

1. Identify the library section that owns a given folder
2. Ask Plex to do a partial rescan of that folder

Plex exposes both via the HTTP API documented informally at
https://www.plexopedia.com/plex-media-server/api/library/ — we talk to
`/library/sections` to list mappings and `/library/sections/<id>/refresh`
with `?path=` for a partial rescan.

We deliberately **don't** add a heavy dependency like PlexAPI; a small
async httpx client is enough and keeps the Docker image lean.
"""
from __future__ import annotations

import asyncio
from typing import Optional
from xml.etree import ElementTree as ET

import httpx
from loguru import logger

from ..config import UserSettings, get_user_settings


class PlexError(RuntimeError):
    pass


def translate_path(local_path: str, settings: Optional[UserSettings] = None) -> str:
    """Translate an app-side path into the path Plex uses for the same folder.

    If no mapping matches, returns the input unchanged. The longest matching
    ``from`` prefix wins so that nested mappings work sensibly.
    """
    s = settings or get_user_settings()
    mappings = s.plex_path_mappings or []
    best: Optional[tuple[str, str]] = None
    for m in mappings:
        if not isinstance(m, dict):
            continue
        src = (m.get("from") or "").rstrip("/")
        dst = (m.get("to") or "").rstrip("/")
        if not src:
            continue
        if local_path == src or local_path.startswith(src + "/"):
            if best is None or len(src) > len(best[0]):
                best = (src, dst)
    if not best:
        return local_path
    src, dst = best
    if local_path == src:
        return dst or "/"
    return dst + local_path[len(src):]


class PlexClient:
    """Very small async Plex API client."""

    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self._http = httpx.AsyncClient(
            base_url=self.url,
            timeout=15.0,
            headers={
                "X-Plex-Token": token,
                "Accept": "application/xml",
                "User-Agent": "plex-nfo-builder/0.6",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def identity(self) -> dict:
        """Return server identity (name, machineIdentifier, version).

        Raises PlexError if the URL/token are wrong.
        """
        try:
            r = await self._http.get("/identity")
        except httpx.HTTPError as e:
            raise PlexError(f"Could not reach Plex at {self.url}: {e}") from e
        if r.status_code == 401:
            raise PlexError("Plex rejected the token (401). Check your token in Settings.")
        if r.status_code != 200:
            raise PlexError(f"Plex /identity returned HTTP {r.status_code}")
        try:
            root = ET.fromstring(r.text)
        except ET.ParseError as e:
            raise PlexError(f"Plex returned non-XML response to /identity: {e}") from e
        return {
            "machine_identifier": root.attrib.get("machineIdentifier"),
            "version": root.attrib.get("version"),
            "friendly_name": root.attrib.get("friendlyName"),
        }

    async def list_sections(self) -> list[dict]:
        """List library sections with their locations.

        Each item: ``{id, key, title, type, locations: [str]}``.
        """
        try:
            r = await self._http.get("/library/sections")
        except httpx.HTTPError as e:
            raise PlexError(f"Could not reach Plex: {e}") from e
        if r.status_code != 200:
            raise PlexError(f"Plex /library/sections returned HTTP {r.status_code}")
        root = ET.fromstring(r.text)
        out: list[dict] = []
        for dir_el in root.findall("Directory"):
            locations = [loc.attrib.get("path", "") for loc in dir_el.findall("Location")]
            out.append({
                "id": dir_el.attrib.get("key"),
                "key": dir_el.attrib.get("key"),
                "title": dir_el.attrib.get("title"),
                "type": dir_el.attrib.get("type"),
                "locations": locations,
            })
        return out

    async def refresh_path(self, section_id: str, path: str) -> None:
        """Ask Plex to partial-rescan a specific folder inside a section.

        Note: this scans for **new files** only; Plex will not re-read an
        existing item's metadata unless a media file was added. Use
        ``refresh_metadata_item`` to force a re-read of the NFO.
        """
        params = {"path": path}
        try:
            r = await self._http.get(f"/library/sections/{section_id}/refresh", params=params)
        except httpx.HTTPError as e:
            raise PlexError(f"Refresh failed: {e}") from e
        if r.status_code not in (200, 202):
            raise PlexError(f"Plex refresh returned HTTP {r.status_code}: {r.text[:200]}")

    async def refresh_section(self, section_id: str) -> None:
        """Fallback: full-scan an entire section."""
        try:
            r = await self._http.get(f"/library/sections/{section_id}/refresh")
        except httpx.HTTPError as e:
            raise PlexError(f"Refresh failed: {e}") from e
        if r.status_code not in (200, 202):
            raise PlexError(f"Plex refresh returned HTTP {r.status_code}: {r.text[:200]}")

    async def list_section_items(self, section_id: str, type_code: int) -> list[dict]:
        """List items in a section. ``type_code``: 1=movie, 2=show.

        For shows each item has ``locations`` (folder paths). For movies
        each item has ``files`` (individual media file paths).
        Returns: ``[{rating_key, title, type, locations: [...], files: [...]}]``.

        Note: ``includeLocations=1`` is required — without it Plex returns
        show ``<Directory>`` elements with no ``<Location>`` child, so we
        can't match them against an on-disk folder.
        """
        params = {
            "type": str(type_code),
            "includeCollections": "0",
            "includeLocations": "1",
        }
        try:
            r = await self._http.get(
                f"/library/sections/{section_id}/all", params=params,
            )
        except httpx.HTTPError as e:
            raise PlexError(f"Section listing failed: {e}") from e
        if r.status_code != 200:
            raise PlexError(f"Plex /library/sections/{section_id}/all returned HTTP {r.status_code}")
        root = ET.fromstring(r.text)
        out: list[dict] = []
        # Shows are <Directory>, movies are <Video>
        for el in list(root.findall("Directory")) + list(root.findall("Video")):
            locations = [loc.attrib.get("path", "") for loc in el.findall("Location")]
            files: list[str] = []
            for media in el.findall("Media"):
                for part in media.findall("Part"):
                    f = part.attrib.get("file")
                    if f:
                        files.append(f)
            out.append({
                "rating_key": el.attrib.get("ratingKey"),
                "title": el.attrib.get("title"),
                "type": el.attrib.get("type"),
                "locations": locations,
                "files": files,
                "grandparent_rating_key": el.attrib.get("grandparentRatingKey"),
                "grandparent_title": el.attrib.get("grandparentTitle"),
                "parent_rating_key": el.attrib.get("parentRatingKey"),
            })
        return out

    async def find_show_by_folder(self,
                                  section_id: str,
                                  folder_path: str,
                                  ) -> Optional[dict]:
        """Fallback show lookup: ask Plex to scan the folder, then list
        the section and return whichever show now claims that folder.

        Used when ``list_section_items`` (with includeLocations=1) doesn't
        return Locations — happens with some older Plex builds and very
        large libraries where the metadata response is truncated.
        """
        items = await self.list_section_items(section_id, 2)
        target = folder_path.rstrip("/")
        for it in items:
            for loc in it.get("locations") or []:
                if (loc or "").rstrip("/") == target:
                    return it
        return None

    async def refresh_metadata_item(self, rating_key: str, *, force: bool = False) -> None:
        """Tell Plex to re-read metadata for a single item (show or movie).

        This is what makes Plex pick up an updated .nfo / artwork
        file — a partial section scan alone will not, because no new
        media file was added.
        """
        params: dict[str, str] = {}
        if force:
            params["force"] = "1"
        url = f"/library/metadata/{rating_key}/refresh"
        try:
            r = await self._http.put(url, params=params)
        except httpx.HTTPError as e:
            raise PlexError(f"Metadata refresh failed: {e}") from e
        if r.status_code not in (200, 202):
            raise PlexError(
                f"Plex metadata refresh returned HTTP {r.status_code}: {r.text[:200]}"
            )


def _client(settings: Optional[UserSettings] = None) -> PlexClient:
    s = settings or get_user_settings()
    if not s.plex_url or not s.plex_token:
        raise PlexError("Plex is not configured. Set plex_url and plex_token in Settings.")
    return PlexClient(s.plex_url, s.plex_token)


def _plex_type_code(section_type: Optional[str]) -> Optional[int]:
    """Map Plex section type string to the numeric code used by /all."""
    if section_type == "movie":
        return 1
    if section_type == "show":
        return 2
    return None


def _item_matches_folder(item: dict, translated_path: str) -> bool:
    """True if ``item`` (from list_section_items) lives under
    ``translated_path``. Works for both shows (match Location) and
    movies (match Part.file's parent directory).
    """
    target = translated_path.rstrip("/")
    for loc in item.get("locations") or []:
        loc_norm = (loc or "").rstrip("/")
        if not loc_norm:
            continue
        if (loc_norm == target
                or loc_norm.startswith(target + "/")
                or target.startswith(loc_norm + "/")):
            return True
    for f in item.get("files") or []:
        if not f:
            continue
        # Compare every parent directory of the media file against the
        # target. Movies often live at <folder>/<movie>.mkv but can also
        # nest deeper, and shows have files inside Season folders.
        parts = f.rstrip("/").split("/")
        for i in range(len(parts) - 1, 0, -1):
            folder = "/".join(parts[:i]).rstrip("/")
            if not folder:
                continue
            if folder == target:
                return True
        # Also keep the previous prefix-style check for robustness.
        immediate = f.rsplit("/", 1)[0].rstrip("/")
        if immediate == target or target.startswith(immediate + "/"):
            return True
    return False


async def find_section_for_path(translated_path: str,
                                sections: Optional[list[dict]] = None,
                                *, settings: Optional[UserSettings] = None,
                                ) -> Optional[dict]:
    """Return the section dict whose locations contain ``translated_path``,
    or None if no section owns that folder.

    ``translated_path`` must already be in Plex's path namespace (after
    applying path mappings).
    """
    s = settings or get_user_settings()
    if sections is None:
        pc = _client(s)
        try:
            sections = await pc.list_sections()
        finally:
            await pc.aclose()
    best: Optional[tuple[str, dict]] = None
    for sec in sections or []:
        for loc in sec.get("locations") or []:
            loc_norm = (loc or "").rstrip("/")
            if not loc_norm:
                continue
            if translated_path == loc_norm or translated_path.startswith(loc_norm + "/"):
                if best is None or len(loc_norm) > len(best[0]):
                    best = (loc_norm, sec)
    return best[1] if best else None


async def refresh_for_folder(local_path: str, *,
                             delay_seconds: int = 0,
                             settings: Optional[UserSettings] = None,
                             ) -> dict:
    """Translate ``local_path`` into Plex's namespace, find the owning
    section, optionally sleep, then trigger a partial rescan.

    Returns a summary dict describing what happened. Never raises; any
    error is captured in the ``error`` field so callers (notably the
    post-build hook) can log but not fail the build.
    """
    s = settings or get_user_settings()
    summary: dict = {
        "requested_local_path": local_path,
        "translated_path": None,
        "section_id": None,
        "section_title": None,
        "refreshed": False,
        "error": None,
    }
    summary["rating_key"] = None
    summary["item_title"] = None
    summary["strategy"] = None
    try:
        translated = translate_path(local_path, s)
        summary["translated_path"] = translated
        pc = _client(s)
        try:
            sections = await pc.list_sections()
            sec = await find_section_for_path(translated, sections, settings=s)
            if not sec:
                summary["error"] = (
                    f"No Plex section contains {translated!r}. "
                    "Check your path mappings in Settings."
                )
                return summary
            summary["section_id"] = sec.get("id")
            summary["section_title"] = sec.get("title")
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

            # Step 1: partial scan so Plex picks up any newly-added media
            # files under the folder. Harmless if there are none.
            try:
                await pc.refresh_path(sec["id"], translated)
            except PlexError as e:
                # Don't abort — we still want to try the metadata refresh.
                logger.warning("Plex partial scan failed, continuing: {}", e)

            # Step 2: locate the matching show/movie item and force Plex
            # to re-read its metadata (which picks up the new .nfo and
            # artwork). Without this step Plex ignores NFO-only changes.
            type_code = _plex_type_code(sec.get("type"))
            matched = None
            items: list[dict] = []
            if type_code:
                try:
                    items = await pc.list_section_items(sec["id"], type_code)
                except PlexError as e:
                    logger.warning("Plex section listing failed: {}", e)
                    items = []
                for it in items:
                    if _item_matches_folder(it, translated):
                        matched = it
                        break
            # Fallback for show sections: if no show matched by Location
            # (some Plex builds omit <Location> even with
            # includeLocations=1), search episode file paths and
            # back-derive the parent show's ratingKey.
            if not matched and type_code == 2:
                try:
                    episodes = await pc.list_section_items(sec["id"], 4)
                except PlexError as e:
                    logger.warning("Plex episode listing failed: {}", e)
                    episodes = []
                target = translated.rstrip("/") + "/"
                grandparent_keys: dict[str, str] = {}
                for ep in episodes:
                    for f in ep.get("files") or []:
                        if not f:
                            continue
                        if f == translated or f.startswith(target):
                            gp = ep.get("grandparent_rating_key")
                            gpt = ep.get("grandparent_title") or ""
                            if gp:
                                grandparent_keys[gp] = gpt
                            break
                if len(grandparent_keys) == 1:
                    rk, title = next(iter(grandparent_keys.items()))
                    matched = {"rating_key": rk, "title": title}
            if matched and matched.get("rating_key"):
                summary["rating_key"] = matched["rating_key"]
                summary["item_title"] = matched.get("title")
                await pc.refresh_metadata_item(matched["rating_key"])
                summary["strategy"] = "metadata-refresh"
                summary["refreshed"] = True
            else:
                # Fallback: the partial scan already fired; report refreshed
                # but note we couldn't find the item. Include a bit of
                # diagnostic so the UI / logs can show why.
                summary["strategy"] = "partial-scan-only"
                summary["refreshed"] = True
                summary["item_count"] = len(items)
                summary["error"] = (
                    f"Partial scan sent to section {sec.get('title')!r} but "
                    f"no item in that section claims folder {translated!r} "
                    f"(listed {len(items)} items). If the show is clearly "
                    "visible in Plex, this is usually a path-mapping "
                    "mismatch — compare the path above to the Location "
                    "shown by Test Connection in Settings."
                )
        finally:
            await pc.aclose()
    except PlexError as e:
        summary["error"] = str(e)
        logger.warning("Plex refresh skipped: {}", e)
    except Exception as e:
        summary["error"] = f"Unexpected error: {e}"
        logger.exception("Plex refresh errored")
    return summary


async def test_connection(settings: Optional[UserSettings] = None) -> dict:
    """Validate Plex credentials and return server identity + section list.

    Does not raise — returns ``{ok: False, error: ...}`` on failure so
    the Settings UI can show the error without a 500.
    """
    s = settings or get_user_settings()
    try:
        pc = _client(s)
    except PlexError as e:
        return {"ok": False, "error": str(e)}
    try:
        identity = await pc.identity()
        sections = await pc.list_sections()
        return {"ok": True, "identity": identity, "sections": sections}
    except PlexError as e:
        return {"ok": False, "error": str(e)}
    finally:
        await pc.aclose()
