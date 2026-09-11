import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import matcher


@pytest.mark.parametrize("provider,kind,data,expected", [
    ("tmdb", "series", {"external_ids": {"tvdb_id": 42}}, "42"),
    ("tvdb", "series", {"remoteIds": [{"sourceName": "TheMovieDB.com", "id": "51"}]}, "51"),
    ("tvdb", "movie", {"remoteIds": [{"sourceName": "TMDB", "id": "61"}]}, "61"),
])
def test_direct_cross_reference_needs_only_primary_provider(monkeypatch, provider, kind, data, expected):
    primary = SimpleNamespace(**{method: AsyncMock(return_value=data) for method in
                                 ("tv_details", "movie_details", "series_extended", "movie_extended")})
    getter = "get_tmdb_client" if provider == "tmdb" else "get_client"
    monkeypatch.setattr(matcher, getter, lambda: primary)
    monkeypatch.setattr(matcher, "get_client" if provider == "tmdb" else "get_tmdb_client",
                        lambda: pytest.fail("direct cross-reference must not need the secondary API"))
    assert asyncio.run(matcher.discover_secondary({"provider": provider, "kind": kind,
                                                   "external_id": "1"})) == expected


@pytest.mark.parametrize("provider,kind", [("tmdb", "series"), ("tmdb", "movie"),
                                          ("tvdb", "series"), ("tvdb", "movie")])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_shared_id_lookup_filters_kind_and_rejects_ambiguity(monkeypatch, provider, kind, ambiguous):
    primary = {"external_ids": {"imdb_id": "tt1234"},
               "remoteIds": [{"sourceName": "IMDB", "id": "tt1234"}]}
    ids = [{"id": 2}, {"id": 3}] if ambiguous else [{"id": 2}]
    tmdb_get = AsyncMock(side_effect=lambda path, **kwargs: (
        {"tv_results": [], "movie_results": []} if path == "/find/1" else
        {"tv_results" if kind == "series" else "movie_results": ids,
         "movie_results" if kind == "series" else "tv_results": [{"id": 99}]}
    ))
    tvdb_get = AsyncMock(return_value={"data": [{kind: row} for row in ids] + [{"people": {"id": 99}}]})
    tmdb = SimpleNamespace(tv_details=AsyncMock(return_value=primary), movie_details=AsyncMock(return_value=primary),
                           _get=tmdb_get, _ttl=lambda: 3600)
    tvdb = SimpleNamespace(series_extended=AsyncMock(return_value=primary), movie_extended=AsyncMock(return_value=primary),
                           _get=tvdb_get, _ttl=lambda: 3600)
    monkeypatch.setattr(matcher, "get_tmdb_client", lambda: tmdb)
    monkeypatch.setattr(matcher, "get_client", lambda: tvdb)
    result = asyncio.run(matcher.discover_secondary({"provider": provider, "kind": kind, "external_id": "1"}))
    assert result == (None if ambiguous else "2")
    if provider == "tmdb":
        tvdb_get.assert_awaited_once_with("/search/remoteid/tt1234", ttl=3600)
    else:
        tmdb_get.assert_any_await("/find/tt1234", params={"external_source": "imdb_id"}, ttl=3600)
