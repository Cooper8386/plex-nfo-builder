import asyncio
import os
import socket
import stat
import threading

import httpx
import pytest

from app.services import artwork_download as download


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "http://user:secret@example.com/image.jpg", "http://example.com:8080/image.jpg",
])
def test_disallowed_url_forms(url):
    with pytest.raises(ValueError):
        asyncio.run(download.public_url(url))


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fd00::1", "224.0.0.1"])
def test_private_or_multicast_addresses_rejected(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(ValueError):
        asyncio.run(download.public_url("https://image.example/photo.jpg"))


def test_download_pins_address_preserves_host_and_replaces_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    destination = tmp_path / "poster.jpg"
    destination.write_bytes(b"previous")
    requests = []

    class ImageStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"\x89PNG\r\n\x1a\nnew-image"

    def handle(request):
        requests.append(request)
        assert destination.read_bytes() == b"previous"
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, stream=ImageStream())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            assert await download.download_image(client, "https://image.example/photo.jpg", destination)

    asyncio.run(run())
    assert requests[0].url.host == "8.8.8.8"
    assert requests[0].headers["host"] == "image.example"
    assert requests[0].extensions["sni_hostname"] == "image.example"
    assert destination.read_bytes() == b"\x89PNG\r\n\x1a\nnew-image"
    assert list(tmp_path.glob("*.tmp")) == []


def test_oversize_transfer_keeps_old_file_and_cleans_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(download, "MAX_IMAGE_BYTES", 4)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    destination = tmp_path / "poster.jpg"
    destination.write_bytes(b"old")

    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"oversized"

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Chunks()))
        async with httpx.AsyncClient(transport=transport) as client:
            assert not await download.download_image(client, "https://image.example/image", destination)

    asyncio.run(run())
    assert destination.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [destination]


def test_redirect_to_private_address_never_requested(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (
        "127.0.0.1" if host == "private.example" else "8.8.8.8", 443))])
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://private.example/admin"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            assert not await download.download_image(client, "https://image.example/image", tmp_path / "poster.jpg")

    asyncio.run(run())
    assert len(seen) == 1
    assert list(tmp_path.iterdir()) == []


def test_uploaded_artwork_copies_locally_without_http(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    source = uploads / "source.jpg"
    source.write_bytes(b"\x89PNG\r\n\x1a\ncustom-image")
    monkeypatch.setattr(download, "MEDIA_ROOT", media)
    monkeypatch.setattr(download, "CUSTOM_ARTWORK_DIR", uploads)
    monkeypatch.setattr(download.db, "get_custom_artwork", lambda art_id: {"source": "upload", "file_path": str(source)})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected HTTP request"))) as client:
            assert await download.download_image(client, "/api/artwork/custom/" + "a" * 40, media / "poster.jpg")

    asyncio.run(run())
    assert (media / "poster.jpg").read_bytes() == b"\x89PNG\r\n\x1a\ncustom-image"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions and umask")
@pytest.mark.parametrize(("existing_mode", "mask", "expected"), [
    (None, 0o027, 0o640), (0o644, 0o077, 0o644), (0o600, 0o022, 0o600),
])
def test_published_artwork_keeps_media_permissions(tmp_path, monkeypatch, existing_mode, mask, expected):
    monkeypatch.setattr(download, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    destination = tmp_path / "poster.jpg"
    if existing_mode is not None:
        destination.write_bytes(b"previous")
        destination.chmod(existing_mode)

    class ImageStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"\x89PNG\r\n\x1a\nimage"

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=ImageStream()))) as client:
            assert await download.download_image(client, "https://image.example/image", destination)

    previous_mask = os.umask(mask)
    try:
        asyncio.run(run())
    finally:
        os.umask(previous_mask)
    assert stat.S_IMODE(destination.stat().st_mode) == expected


def test_cancellation_removes_temp_preserves_existing_image(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    destination = tmp_path / "poster.jpg"
    destination.write_bytes(b"old")

    class CancelledStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial"
            raise asyncio.CancelledError

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=CancelledStream()))) as client:
            with pytest.raises(asyncio.CancelledError):
                await download.download_image(client, "https://image.example/image", destination)

    asyncio.run(run())
    assert destination.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [destination]


def test_cancellation_drains_file_worker_before_returning():
    started, release = threading.Event(), threading.Event()

    def write():
        started.set()
        assert release.wait(2)

    async def run():
        task = asyncio.create_task(download._file_io(write))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
