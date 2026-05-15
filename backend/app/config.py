"""Application configuration via env vars and a JSON settings file in /config."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvSettings(BaseSettings):
    """Container-level configuration. Read once at startup."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    media_root: str = Field(default="/media")
    config_dir: str = Field(default="/config")
    tvdb_api_key: Optional[str] = Field(default=None)
    tvdb_pin: Optional[str] = Field(default=None)
    tmdb_api_key: Optional[str] = Field(default=None)
    fanart_api_key: Optional[str] = Field(default=None)
    log_level: str = Field(default="INFO")
    listen_host: str = Field(default="0.0.0.0")
    listen_port: int = Field(default=8000)
    # v0.12.0: filesystem watcher defaults. The user-editable settings below
    # can override these at runtime; env values are the boot defaults applied
    # when the user has never touched the toggle.
    watcher_enabled: bool = Field(default=True)
    watcher_debounce_seconds: int = Field(default=30)


class UserSettings(BaseModel):
    """User-editable settings persisted to /config/settings.json."""

    preferred_language: str = "eng"  # 3-letter TVDB language code
    fallback_languages: List[str] = ["eng"]
    include_original_title: bool = True
    cache_ttl_hours: int = 24 * 7
    overwrite_foreign_nfo: bool = False
    tvdb_api_key: Optional[str] = None  # overrides env
    tvdb_pin: Optional[str] = None
    tmdb_api_key: Optional[str] = None  # overrides env
    fanart_api_key: Optional[str] = None  # overrides env
    auto_match_threshold: int = 85
    # v0.5.0: alternate metadata + artwork sources
    metadata_source: str = "tvdb"   # tvdb | tmdb — primary source for matching/NFOs
    fanart_enabled: bool = True
    tmdb_artwork_enabled: bool = True
    # v0.5.8: which provider's artwork wins by default (independent of metadata source)
    preferred_artwork_source: str = "auto"
    # v0.6.0: Plex Media Server integration.
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None
    plex_auto_refresh: bool = False
    plex_refresh_delay_seconds: int = 5
    plex_path_mappings: List[dict] = []
    # v0.11.0: Sonarr/Radarr-compatible file-rename templates.
    rename_episode_template: str = (
        "{Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} "
        "{[Custom Formats]}{[Quality Full]}{[MediaInfo VideoDynamicRangeType]}"
        "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
        "{[MediaInfo VideoCodec]}{-Release Group}"
    )
    rename_daily_template: str = (
        "{Series TitleYear} - {Air-Date} - {Episode CleanTitle} "
        "{[Custom Formats]}{[Quality Full]}{[MediaInfo VideoDynamicRangeType]}"
        "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
        "{[MediaInfo VideoCodec]}{-Release Group}"
    )
    rename_anime_template: str = (
        "{Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} "
        "{[Custom Formats]}{[Quality Full]}{[MediaInfo VideoDynamicRangeType]}"
        "[{MediaInfo VideoBitDepth}bit]{[MediaInfo VideoCodec]}"
        "[{Mediainfo AudioCodec} { Mediainfo AudioChannels}]"
        "{MediaInfo AudioLanguages}{-Release Group}"
    )
    rename_series_folder_template: str = "{Series TitleYear} {tvdb-{TvdbId}}"
    rename_season_folder_template: str = "Season {season:00}"
    rename_movie_template: str = (
        "{Movie CleanTitle} {(Release Year)} {tmdb-{TmdbId}} {edition-{Edition Tags}} "
        "{[Custom Formats]}{[Quality Full]}{[MediaInfo 3D]}{[MediaInfo VideoDynamicRangeType]}"
        "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
        "{[Mediainfo VideoCodec]}{-Release Group}"
    )
    rename_movie_folder_template: str = (
        "{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}"
    )
    rename_enabled: bool = True
    auto_sweep_orphans: bool = True
    # v0.11.12: per-provider artwork language filters.
    tvdb_artwork_languages: List[str] = []
    tvdb_artwork_allow_null_language: bool = True
    tmdb_artwork_languages: List[str] = []
    tmdb_artwork_allow_null_language: bool = True

    # v0.12.0: filesystem watcher. Both fields are nullable so the UI can
    # explicitly say "follow the env default" (None) vs. "I set this to X".
    # ``effective_watcher_*`` helpers below resolve None to the EnvSettings
    # value so every consumer can call one accessor instead of branching.
    watcher_enabled: Optional[bool] = None
    watcher_debounce_seconds: Optional[int] = None

    @classmethod
    def load(cls, path: Path) -> "UserSettings":
        if path.exists():
            try:
                return cls(**json.loads(path.read_text()))
            except Exception:
                pass
        return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))


env = EnvSettings()
CONFIG_DIR = Path(env.config_dir)
MEDIA_ROOT = Path(env.media_root)
SETTINGS_PATH = CONFIG_DIR / "settings.json"
LIBS_PATH = CONFIG_DIR / "libraries.json"
DB_PATH = CONFIG_DIR / "app.db"
LOG_DIR = CONFIG_DIR / "logs"
CUSTOM_ARTWORK_DIR = CONFIG_DIR / "custom-artwork"


def get_user_settings() -> UserSettings:
    return UserSettings.load(SETTINGS_PATH)


def save_user_settings(s: UserSettings) -> None:
    s.save(SETTINGS_PATH)


def effective_tvdb_credentials() -> tuple[Optional[str], Optional[str]]:
    s = get_user_settings()
    api_key = s.tvdb_api_key or env.tvdb_api_key or os.environ.get("TVDB_API_KEY")
    pin = s.tvdb_pin or env.tvdb_pin or os.environ.get("TVDB_PIN")
    return api_key, pin


def effective_tmdb_credentials() -> Optional[str]:
    s = get_user_settings()
    return s.tmdb_api_key or env.tmdb_api_key or os.environ.get("TMDB_API_KEY")


def effective_fanart_credentials() -> Optional[str]:
    s = get_user_settings()
    return s.fanart_api_key or env.fanart_api_key or os.environ.get("FANART_API_KEY")


def effective_metadata_source(library_name: Optional[str] = None) -> str:
    """Return the active metadata source for a given library.

    Resolution order:
      1. Per-library override (libraries.metadata_source) when set to tvdb/tmdb.
      2. Global UserSettings.metadata_source.
      3. Hardcoded fallback "tvdb".
    Imported lazily to avoid a circular import (db -> config -> db).
    """
    s = get_user_settings()
    global_src = (s.metadata_source or "tvdb").strip().lower() or "tvdb"
    if library_name:
        try:
            from . import db as _db  # local import to avoid cycle at module load
            row = _db.get_library(library_name)
            if row is not None:
                try:
                    override = row["metadata_source"]
                except (IndexError, KeyError):
                    override = None
                if override:
                    o = str(override).strip().lower()
                    if o in ("tvdb", "tmdb"):
                        return o
        except Exception:
            # Defensive: never let library lookup break a build.
            pass
    return global_src if global_src in ("tvdb", "tmdb") else "tvdb"


def effective_watcher_enabled() -> bool:
    """Resolve the runtime watcher enable flag.

    User setting wins when explicitly set (True/False); otherwise the
    container env default applies. Default-default is True (the watcher is
    on out of the box; users can disable from Settings → Watcher or by
    setting ``WATCHER_ENABLED=false`` in the container).
    """
    s = get_user_settings()
    if s.watcher_enabled is not None:
        return bool(s.watcher_enabled)
    return bool(env.watcher_enabled)


def effective_watcher_debounce_seconds() -> int:
    """Resolve the active debounce window in seconds.

    Clamped to [1, 3600] — anything shorter than 1s defeats the point and
    anything longer than an hour is almost certainly a typo. The default
    is 30s which matches typical Sonarr/Radarr post-import settling.
    """
    s = get_user_settings()
    raw = s.watcher_debounce_seconds if s.watcher_debounce_seconds is not None else env.watcher_debounce_seconds
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = 30
    return max(1, min(3600, v))
