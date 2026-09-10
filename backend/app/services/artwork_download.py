"""Bounded artwork transfers with public-address pinning and atomic replacement."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import shutil
import socket
from pathlib import Path

import httpx
from loguru import logger

from .. import db
from ..config import CUSTOM_ARTWORK_DIR, MEDIA_ROOT
from .async_io import run_in_thread as _file_io
from .media_files import create_media_temp

MAX_IMAGE_BYTES = 50 * 1024 * 1024
_UPLOAD_URL = re.compile(r"/api/artwork/custom/([a-f0-9]{40})")


def image_type(data: bytes) -> tuple[str, str]:
    """Identify supported raster formats instead of trusting upload headers."""
    for signature, extension, content_type in (
        (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
        (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
        (b"GIF87a", ".gif", "image/gif"),
        (b"GIF89a", ".gif", "image/gif"),
        (b"BM", ".bmp", "image/bmp"),
        (b"II*\x00", ".tiff", "image/tiff"),
        (b"MM\x00*", ".tiff", "image/tiff"),
    ):
        if data.startswith(signature):
            return extension, content_type
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    raise ValueError("Unsupported image content")


def uploaded_file(url: str) -> Path | None:
    """Resolve only registered uploads within the configured artwork directory."""
    match = _UPLOAD_URL.fullmatch(url)
    if not match:
        return None
    row = db.get_custom_artwork(match[1])
    if not row or row["source"] != "upload":
        raise ValueError("Uploaded artwork is no longer available")
    path = Path(row["file_path"]).resolve()
    if not path.is_relative_to(CUSTOM_ARTWORK_DIR.resolve()) or not path.is_file():
        raise ValueError("Uploaded artwork is outside its storage directory or missing")
    return path


async def public_url(url: str) -> tuple[httpx.URL, str]:
    """Resolve a public HTTP(S) destination; return a pinned IP and original host.

    Connecting to this IP (with the original TLS SNI/Host) prevents a second
    DNS lookup from changing a validated public address into a private one.
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as error:
        raise ValueError("Invalid artwork URL") from error
    if parsed.scheme not in {"http", "https"} or not parsed.host or parsed.userinfo:
        raise ValueError("Artwork URL must be HTTP(S), without embedded credentials")
    if parsed.port not in {None, 80, 443}:
        raise ValueError("Artwork URL must use port 80 or 443")
    addresses = await asyncio.to_thread(
        socket.getaddrinfo, parsed.host, parsed.port or (443 if parsed.scheme == "https" else 80),
        0, socket.SOCK_STREAM,
    )
    if not addresses:
        raise ValueError("Artwork host has no public address")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError("Artwork URL must resolve only to public Internet addresses")
    return parsed.copy_with(host=addresses[0][4][0]), parsed.host


async def validate_artwork_url(url: str) -> None:
    if url.startswith("/api/artwork/custom/"):
        if await asyncio.to_thread(uploaded_file, url) is None:
            raise ValueError("Invalid uploaded artwork URL")
    else:
        await public_url(url)


def _create_temp(dest: Path) -> tuple[int, Path]:
    # Check the resolved parent before creating anything. A symlinked .actors
    # or season directory must never redirect writes outside the media root.
    if not dest.parent.resolve().is_relative_to(MEDIA_ROOT.resolve()):
        raise ValueError("Artwork destination is outside MEDIA_ROOT")
    dest.parent.mkdir(parents=True, exist_ok=True)
    return create_media_temp(dest)


async def download_image(client: httpx.AsyncClient, url: str, dest: Path) -> bool:
    """Keep the previous image on errors, oversize responses, or cancellation."""
    temporary: Path | None = None
    try:
        source = await asyncio.to_thread(uploaded_file, url)
        if source is not None:
            if source.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError("Uploaded artwork exceeds 50 MB")
            descriptor, local_temp = _create_temp(dest)
            temporary = local_temp
            os.close(descriptor)
            await _file_io(shutil.copyfile, source, local_temp)
        else:
            original = httpx.URL(url)
            for redirect in range(4):
                pinned, hostname = await public_url(str(original))
                async with client.stream(
                    # Connections are keyed by the pinned IP; do not reuse TLS
                    # sessions across different hostnames sharing that address.
                    "GET", pinned, headers={
                        "Host": original.netloc.decode("ascii"), "Connection": "close", "Accept-Encoding": "identity",
                    },
                    extensions={"sni_hostname": hostname}, timeout=60.0, follow_redirects=False,
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location or redirect == 3:
                            raise ValueError("Artwork redirect limit exceeded")
                        original = original.join(location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type and not (content_type.startswith("image/") or content_type == "application/octet-stream"):
                        raise ValueError("Artwork server did not return an image")
                    if int(response.headers.get("content-length", "0")) > MAX_IMAGE_BYTES:
                        raise ValueError("Artwork exceeds 50 MB")
                    descriptor, temporary = _create_temp(dest)
                    with os.fdopen(descriptor, "wb") as output:
                        size = 0
                        async for chunk in response.aiter_raw(64 * 1024):
                            size += len(chunk)
                            if size > MAX_IMAGE_BYTES:
                                raise ValueError("Artwork exceeds 50 MB")
                            await _file_io(output.write, chunk)
                    if not size:
                        raise ValueError("Artwork response is empty")
                    break
        if not dest.parent.resolve().is_relative_to(MEDIA_ROOT.resolve()):
            raise ValueError("Artwork destination moved outside MEDIA_ROOT")
        if temporary is None:
            raise ValueError("Artwork download did not produce an image")
        with temporary.open("rb") as image:
            image_type(image.read(16))
        os.replace(temporary, dest)
        return True
    except (OSError, ValueError, httpx.HTTPError, httpx.InvalidURL) as error:
        # URLs may contain signed credentials. Keep them out of logs.
        logger.warning("Artwork download failed for {} ({})", dest.name, type(error).__name__)
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as error:
                logger.warning("Could not remove artwork temporary file {} ({})", temporary, type(error).__name__)
