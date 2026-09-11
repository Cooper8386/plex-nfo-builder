import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.routes import api
from app.services import tmdb


def test_artwork_languages_accepts_and_caches_tmdb_array(monkeypatch):
    payload = [
        {"iso_639_1": "ja", "english_name": "Japanese", "name": "日本語"},
        {"iso_639_1": "en", "english_name": "English", "name": "English"},
    ]
    cache = {}
    monkeypatch.setattr(tmdb, "cache_get", cache.get)
    monkeypatch.setattr(tmdb, "cache_set", lambda key, data, ttl: cache.update({key: data}))
    monkeypatch.setattr(tmdb, "effective_tmdb_credentials", lambda: "test-key")
    monkeypatch.setattr(api, "effective_tmdb_credentials", lambda: "test-key")
    monkeypatch.setattr(api, "effective_tvdb_credentials", lambda: (None, None))

    async def run():
        client = tmdb.TMDBClient()
        request = AsyncMock(return_value=httpx.Response(200, json=payload))
        monkeypatch.setattr(client._client, "get", request)
        monkeypatch.setattr(api, "get_tmdb_client", lambda: client)
        try:
            for _ in range(2):
                result = await api.artwork_languages()
                assert result["tmdb"] == [
                    {"code": "en", "name": "English", "native_name": "English"},
                    {"code": "ja", "name": "Japanese", "native_name": "日本語"},
                ]
            request.assert_awaited_once()
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("path,payload", [
    ("/tv/123", [{"id": 123}]),
    ("/configuration/languages", ["en"]),
])
def test_tmdb_still_rejects_unexpected_response_shapes(monkeypatch, path, payload):
    monkeypatch.setattr(tmdb, "effective_tmdb_credentials", lambda: "test-key")

    async def run():
        client = tmdb.TMDBClient()
        monkeypatch.setattr(client._client, "get", AsyncMock(return_value=httpx.Response(200, json=payload)))
        try:
            with pytest.raises(tmdb.TMDBError, match="unexpected response shape"):
                await client._get(path)
        finally:
            await client.aclose()

    asyncio.run(run())
