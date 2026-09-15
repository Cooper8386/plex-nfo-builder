import asyncio

from app.config import UserSettings
from app.services import artwork_resolver


def test_series_artwork_preferences_resolve_each_slot(monkeypatch):
    class Tmdb:
        async def tv_images(self, *_args, **_kwargs):
            return {
                "posters": [{"file_path": "/poster.jpg"}],
                "backdrops": [{"file_path": "/background.jpg"}],
                "logos": [{"file_path": "/logo.png"}],
            }

        async def tv_details(self, *_args, **_kwargs):
            return {"original_language": "en"}

        async def tv_season_images(self, *_args, **_kwargs):
            raise AssertionError("TMDB seasons must not be fetched when TVDB is preferred")

    monkeypatch.setattr(artwork_resolver, "effective_tmdb_credentials", lambda: "key")
    monkeypatch.setattr(artwork_resolver, "effective_tvdb_credentials", lambda: ("key", None))
    monkeypatch.setattr(artwork_resolver, "get_tmdb_client", lambda: Tmdb())

    settings = UserSettings(
        preferred_poster_source="tmdb",
        preferred_background_source="tmdb",
        preferred_clearlogo_source="tmdb",
        preferred_season_source="tvdb",
    )
    result = asyncio.run(
        artwork_resolver.resolve_preferred_artwork_series(
            settings=settings,
            bound_provider="tvdb",
            tvdb_data={
                "id": "tvdb-1",
                "remoteIds": [{"sourceName": "TMDB", "id": "tmdb-1"}],
                "artworks": [{"type": 7, "seasonNumber": 1, "image": "/season.jpg", "score": 1}],
            },
            local_season_numbers=[1],
        )
    )

    assert result == {
        "poster": "https://image.tmdb.org/t/p/original/poster.jpg",
        "background": "https://image.tmdb.org/t/p/original/background.jpg",
        "clearlogo": "https://image.tmdb.org/t/p/original/logo.png",
        "season-01-poster": "https://artworks.thetvdb.com/season.jpg",
    }


def test_tmdb_movie_auto_resolves_a_clearlogo(monkeypatch):
    class Tmdb:
        async def movie_images(self, *_args, **_kwargs):
            return {"posters": [], "backdrops": [], "logos": [{"file_path": "/logo.png"}]}

    monkeypatch.setattr(artwork_resolver, "effective_tmdb_credentials", lambda: "key")
    monkeypatch.setattr(artwork_resolver, "get_tmdb_client", lambda: Tmdb())
    result = asyncio.run(
        artwork_resolver.resolve_preferred_artwork_movie(
            settings=UserSettings(),
            bound_provider="tmdb",
            tmdb_mv={"id": 1, "original_language": "en"},
        )
    )
    assert result == {"clearlogo": "https://image.tmdb.org/t/p/original/logo.png"}
