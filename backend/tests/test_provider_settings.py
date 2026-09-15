import asyncio

from app import config
from app.routes import settings


def test_omdb_key_is_write_only_and_blank_preserves_saved_value(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(config.env, "omdb_api_key", None)
    monkeypatch.delenv("OMDB_API_KEY", raising=False)

    async def run():
        await settings.update_settings(settings.SettingsIn(omdb_api_key="saved-omdb-key"))
        payload = await settings.get_settings()
        assert payload["omdb_api_key_configured"] is True
        assert "omdb_api_key" not in payload
        assert "saved-omdb-key" not in str(payload)

        await settings.update_settings(settings.SettingsIn(omdb_api_key=""))
        assert config.get_user_settings().omdb_api_key == "saved-omdb-key"

        await settings.update_settings(settings.SettingsIn(omdb_api_key=None))
        monkeypatch.setenv("OMDB_API_KEY", "environment-omdb-key")
        payload = await settings.get_settings()
        assert payload["omdb_api_key_configured"] is True
        assert "environment-omdb-key" not in str(payload)

    asyncio.run(run())


def test_legacy_artwork_source_populates_each_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text('{"preferred_artwork_source":"tmdb"}', encoding="utf-8")

    loaded = config.get_user_settings()
    assert loaded.preferred_poster_source == "tmdb"
    assert loaded.preferred_background_source == "tmdb"
    assert loaded.preferred_clearlogo_source == "tmdb"
    assert loaded.preferred_season_source == "tmdb"

    async def run():
        await settings.update_settings(settings.SettingsIn(preferred_season_source="tvdb"))
        saved = config.get_user_settings()
        assert saved.preferred_poster_source == "tmdb"
        assert saved.preferred_season_source == "tvdb"

    asyncio.run(run())
