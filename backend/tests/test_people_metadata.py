import asyncio
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET

import pytest

from app.services import nfo, tmdb, tvdb


def test_missing_tmdb_portraits_are_shared_and_failures_keep_other_credits(monkeypatch):
    client = tmdb.TMDBClient.__new__(tmdb.TMDBClient)
    client.person_image = AsyncMock(side_effect=["https://images.example/portrait.jpg", RuntimeError("offline")])
    record = {"credits": {
        "cast": [{"id": 1, "name": "Lead"}, {"id": 2, "name": "Unavailable"},
                 {"id": 3, "name": "Known", "profile_path": "/known.jpg"}],
        "crew": [{"id": 1, "name": "Lead", "job": "Writer"}],
    }, "guest_stars": [{"id": 3, "name": "Known"}]}
    asyncio.run(client.hydrate_credits(record, force=True))
    assert client.person_image.await_count == 2
    assert all(call.kwargs["force"] is True for call in client.person_image.await_args_list)
    assert record["credits"]["cast"][0]["profile_path"] == record["credits"]["crew"][0]["profile_path"]
    assert "profile_path" not in record["credits"]["cast"][1]
    assert record["guest_stars"][0]["profile_path"] == "/known.jpg"


def test_tmdb_person_uses_gallery_and_exact_tvdb_link(monkeypatch):
    client = tmdb.TMDBClient.__new__(tmdb.TMDBClient)
    monkeypatch.setattr(tmdb.TMDBClient, "_ttl", lambda _: 3600)
    client._get = AsyncMock(return_value={"images": {"profiles": [{"file_path": "/gallery.jpg"}]}})
    assert asyncio.run(client.person_image(1)) == "https://image.tmdb.org/t/p/w500/gallery.jpg"

    client._get.return_value = {"external_ids": {"imdb_id": "nm0000001"}}
    other = tvdb.TVDBClient.__new__(tvdb.TVDBClient)
    other._get = AsyncMock(return_value={"data": [{"people": {"id": 22, "image": "https://images.example/person.jpg"}}]})
    monkeypatch.setattr(tvdb.TVDBClient, "_ttl", lambda _: 3600)
    monkeypatch.setattr(tvdb, "get_client", lambda: other)
    monkeypatch.setattr(tmdb, "effective_tvdb_credentials", lambda: ("test-key", None))
    assert asyncio.run(client.person_image(1, force=True)) == "https://images.example/person.jpg"
    assert other._get.call_args.args == ("/search/remoteid/nm0000001",)
    assert client._get.call_args.kwargs["force"] is True
    assert other._get.call_args.kwargs["force"] is True
    other._get.return_value = {"data": [{"people": {"id": 22}}, {"people": {"id": 23}}]}
    assert asyncio.run(client.person_image(1)) is None
    client._get.return_value = {"external_ids": {"imdb_id": "nm1/../../movies/2"}}
    other._get.reset_mock()
    assert asyncio.run(client.person_image(1)) is None
    other._get.assert_not_awaited()


def test_tvdb_person_uses_exact_imdb_link_for_tmdb_portrait(monkeypatch):
    client = tvdb.TVDBClient.__new__(tvdb.TVDBClient)
    monkeypatch.setattr(tvdb.TVDBClient, "_ttl", lambda _: 3600)
    client._get = AsyncMock(side_effect=[{"data": {"image": None}}, {"data": {
        "remoteIds": [{"sourceName": "IMDB", "id": "nm0000001"}],
    }}])
    other = tmdb.TMDBClient.__new__(tmdb.TMDBClient)
    other._get = AsyncMock(return_value={"person_results": [{"id": 4, "profile_path": "/person.jpg"}]})
    monkeypatch.setattr(tmdb.TMDBClient, "_ttl", lambda _: 3600)
    monkeypatch.setattr(tmdb, "get_client", lambda: other)
    monkeypatch.setattr(tvdb, "effective_tmdb_credentials", lambda: "test-key")
    assert asyncio.run(client.person_image(1)) == "https://image.tmdb.org/t/p/w500/person.jpg"
    assert other._get.call_args.args == ("/find/nm0000001",)
    assert other._get.call_args.kwargs["params"] == {"external_source": "imdb_id"}


@pytest.mark.parametrize("build", [nfo.build_series_nfo_tmdb, nfo.build_movie_nfo_tmdb, nfo.build_episode_nfo_tmdb])
def test_tmdb_nfo_preserves_cast_guests_and_crew_roles(build):
    guest = {"name": "Guest", "character": "Visitor", "profile_path": "/guest.jpg"}
    record = {"id": 7, "name": "Title", "credits": {
        "cast": [{"name": "Lead", "character": "Hero", "profile_path": "/lead.jpg"}],
        "guest_stars": [guest],
        "crew": [{"name": "Director", "job": "Director"}, {"name": "Writer", "job": "Screenplay"},
                 {"name": "Writer", "department": "Writing", "job": "Story"}],
    }, "guest_stars": [guest], "external_ids": {"imdb_id": "tt0000007", "tvdb_id": 8}}
    root = ET.fromstring(build(record, language="eng", fallbacks=[]))
    assert [actor.findtext("name") for actor in root.findall("actor")] == ["Lead", "Guest"]
    assert root.findtext("actor/role") == "Hero"
    assert root.findall("actor")[1].findtext("thumb") == "https://image.tmdb.org/t/p/w500/guest.jpg"
    assert root.findall("actor")[1].findtext("order") == "1"
    assert [element.text for element in root.findall("director")] == ["Director"]
    assert [element.text for element in root.findall("credits")] == ["Writer"]
    assert root.findtext("uniqueid[@type='imdb']") == "tt0000007"


@pytest.mark.parametrize("build", [nfo.build_series_nfo, nfo.build_movie_nfo, nfo.build_episode_nfo])
def test_tvdb_nfo_does_not_mislabel_crew_as_cast(build):
    record = {"id": 1, "name": "Title", "characters": [
        {"personName": "Lead", "name": "Hero", "peopleType": "Actor", "personImgURL": "https://images.example/lead.jpg"},
        {"personName": "Director", "peopleType": "Director"},
        {"personName": "Writer", "peopleType": "Writer"},
        {"personName": "Producer", "peopleType": "Producer"},
        {"personName": "Guest", "peopleType": "Guest Star"},
    ]}
    root = ET.fromstring(build(record, language="eng", fallbacks=[]))
    assert [actor.findtext("name") for actor in root.findall("actor")] == ["Lead", "Guest"]
    assert root.findtext("actor/thumb") == "https://images.example/lead.jpg"
    assert root.findtext("director") == "Director"
    assert root.findtext("credits") == "Writer"


def test_tmdb_episode_fetch_includes_credits_and_episode_ids(monkeypatch):
    client = tmdb.TMDBClient.__new__(tmdb.TMDBClient)
    client._get = AsyncMock(return_value={"id": 12, "credits": {}, "external_ids": {}})
    monkeypatch.setattr(tmdb.TMDBClient, "_ttl", lambda _: 3600)
    assert asyncio.run(client.tv_episode(5, 2, 4, force=True))["id"] == 12
    assert client._get.call_args.args == ("/tv/5/season/2/episode/4",)
    assert client._get.call_args.kwargs["params"]["append_to_response"] == "external_ids,credits"
    assert client._get.call_args.kwargs["force"] is True


@pytest.mark.parametrize("client_class", [tmdb.TMDBClient, tvdb.TVDBClient])
def test_person_paths_reject_invalid_ids(client_class):
    client = client_class.__new__(client_class)
    client._get = AsyncMock()
    for ident in ("../1", "1?api_key=bad", "nm1", 0, -1, "１２"):
        assert asyncio.run(client.person_image(ident)) is None
    client._get.assert_not_awaited()
