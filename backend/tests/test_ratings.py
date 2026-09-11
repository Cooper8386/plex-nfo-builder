import asyncio
from unittest.mock import AsyncMock, Mock
from xml.etree import ElementTree as ET

import httpx
from app.config import UserSettings
from app.services import nfo, ratings


def test_rating_sources_and_scales_survive_all_nfo_paths():
    scores = ratings.normalise_omdb({
        "imdbRating": "8.7", "imdbVotes": "12,345",
        "Ratings": [{"Source": "Rotten Tomatoes", "Value": "90%"},
                    {"Source": "Metacritic", "Value": "83/100"}],
    })
    assert scores["imdb"]["votes"] == 12345
    for operation, provider in (
        (nfo.build_series_nfo, "tvdb"), (nfo.build_movie_nfo, "tvdb"), (nfo.build_episode_nfo, "tvdb"),
        (nfo.build_series_nfo_tmdb, "tmdb"), (nfo.build_movie_nfo_tmdb, "tmdb"),
        (nfo.build_episode_nfo_tmdb, "tmdb"),
    ):
        record = {"id": 1, "name": "Example", "_ratings": scores, "vote_average": 8.2, "vote_count": 120}
        root = ET.fromstring(operation(record, language="eng", fallbacks=["eng"]))
        assert root.findtext("ratings/rating[@name='imdb']/value") == "8.7"
        assert root.findtext("ratings/rating[@name='imdb']/votes") == "12345"
        assert root.find("ratings/rating[@name='tomatometerallcritics']").get("max") == "100"
        assert root.findtext("criticrating") == "90"
        assert root.findtext("rating") == "8.7"
        assert len(root.findall("ratings/rating[@default='true']")) == 1
        assert (root.find("ratings/rating[@name='themoviedb']") is not None) == (provider == "tmdb")


def test_missing_or_invalid_scores_are_never_fabricated():
    assert ratings.normalise_omdb({"imdbRating": "N/A", "Ratings": [
        {"Source": "Rotten Tomatoes", "Value": "101%"},
        {"Source": "Metacritic", "Value": "NaN/100"},
    ]}) == {}
    assert ratings.normalise_omdb({"Ratings": [{"Source": "Rotten Tomatoes", "Value": "0%"}]}) == {
        "tomatometerallcritics": {"value": 0.0, "max": 100},
    }
    for provider in ("tmdb", "tvdb"):
        root = ET.Element("movie")
        ratings.write_ratings(root, {"vote_average": 0, "vote_count": 0, "score": 99999}, provider=provider)
        assert list(root) == []


def test_exact_episode_lookup_never_copies_series_or_wrong_episode_scores(monkeypatch):
    monkeypatch.setattr(ratings, "effective_omdb_credentials", lambda: "secret")
    fetch = AsyncMock(return_value={"Type": "episode", "imdbID": "tt1234567", "seriesID": "tt7654321",
                                   "Season": "2", "Episode": "3", "imdbRating": "9.1"})
    monkeypatch.setattr(ratings, "_fetch", fetch)
    episode = {"season_number": 2, "episode_number": 3}
    series = {"external_ids": {"imdb_id": "tt7654321"}}
    kwargs = {"kind": "episode", "provider": "tmdb", "log": Mock(), "series": series}
    asyncio.run(ratings.hydrate_ratings(episode, **kwargs))
    assert episode["_ratings"]["imdb"]["value"] == 9.1
    assert episode["imdb_id"] == "tt1234567"
    assert fetch.call_args.args[0] == {"i": "tt7654321", "Season": "2", "Episode": "3"}
    for changes in ({"Type": "series"}, {"Episode": "4"}, {"seriesID": "tt1111111"}):
        fetch.return_value = {**fetch.return_value, **changes}
        other = {"season_number": 2, "episode_number": 3}
        asyncio.run(ratings.hydrate_ratings(other, **kwargs))
        assert "_ratings" not in other
    direct = {"imdb_id": "tt1234567"}
    fetch.return_value = {"Type": "episode", "imdbID": "tt9999999", "imdbRating": "8.1"}
    asyncio.run(ratings.hydrate_ratings(direct, **kwargs))
    assert "_ratings" not in direct
    fetch.side_effect = httpx.ConnectError("network unavailable")
    asyncio.run(ratings.hydrate_ratings(direct, **kwargs))
    assert "_ratings" not in direct
    monkeypatch.setattr(ratings, "effective_omdb_credentials", lambda: None)
    fetch.reset_mock()
    asyncio.run(ratings.hydrate_ratings(direct, **kwargs))
    fetch.assert_not_called()


def test_omdb_lookup_caches_without_secrets_and_force_refreshes(monkeypatch):
    requests = []
    cached = {}

    def response(request):
        requests.append(request)
        return httpx.Response(200, json={"Response": "True", "Type": "movie", "imdbID": "tt1234567"})

    monkeypatch.setattr(ratings, "cache_get", cached.get)
    monkeypatch.setattr(ratings, "cache_set", lambda key, payload, ttl: cached.update({key: payload}))
    monkeypatch.setattr(ratings, "get_user_settings", UserSettings)

    async def run():
        async with httpx.AsyncClient(base_url="https://www.omdbapi.com", transport=httpx.MockTransport(response)) as client:
            monkeypatch.setattr(ratings, "_client", client)
            params = {"i": "tt1234567", "type": "movie"}
            first = await ratings._fetch(params, "secret", force=False)
            assert await ratings._fetch(params, "secret", force=False) == first
            assert len(requests) == 1
            await ratings._fetch(params, "secret", force=True)
            assert len(requests) == 2
            assert all("secret" not in key for key in cached)
            assert requests[0].url.params["apikey"] == "secret"
    asyncio.run(run())
