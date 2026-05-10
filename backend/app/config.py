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


class UserSettings(BaseModel):
    """User-editable settings persisted to /config/settings.json."""

    preferred_language: str = "eng"  # 3-letter TVDB language code
    fallback_languages: List[str] = ["eng"]
    include_original_title: bool = True
    cache_ttl_hours: int = 24 * 7
    overwrite_foreign_nfo: bool = False
    tvdb_api_key: Optional[str] = None  # overrides env
    tvdb_pin: Optional[str] = None
    auto_match_threshold: int = 85
    # v0.5.0: alternate metadata + artwork sources
    metadata_source: str = "tvdb"   # tvdb | tmdb — primary source for matching/NFOs
    tmdb_api_key: Optional[str] = None  # overrides env
    fanart_api_key: Optional[str] = None  # overrides env
    fanart_enabled: bool = True
    tmdb_artwork_enabled: bool = True
    # v0.5.8: which provider's artwork wins by default (independent of metadata source)
    #   "auto" — whichever provider the show is bound to (plus supplements)
    #   "tvdb" — always prefer TVDB images when available
    #   "tmdb" — always prefer TMDB images when available
    # User per-folder selections always override this.
    preferred_artwork_source: str = "auto"
    # v0.6.0: Plex Media Server integration. When configured, the app can
    # ask Plex to rescan a show/movie folder right after a build completes
    # so the changes show up without having to refresh manually.
    plex_url: Optional[str] = None              # e.g. http://192.168.1.10:32400
    plex_token: Optional[str] = None            # X-Plex-Token
    plex_auto_refresh: bool = False             # auto-refresh after each successful build
    plex_refresh_delay_seconds: int = 5         # seconds to wait before refreshing
    # Mappings to translate the app's local paths to Plex's view of the same
    # folder. Each item is {"from": "/media", "to": "/data"}. The longest
    # matching prefix wins. Empty list = no translation needed.
    plex_path_mappings: List[dict] = []
    # v0.11.0: Sonarr/Radarr-compatible file-rename templates.
    # Tokens use Sonarr/Radarr grammar (case-insensitive):
    #   {Series TitleYear}, {Series CleanTitle}, {Episode CleanTitle}
    #   {season:00}, {episode:00}  — zero-padded to width of format spec
    #   {Air-Date}, {Release Year}, {(Release Year)}
    #   {Quality Full}, {Custom Formats}
    #   {MediaInfo VideoCodec}, {MediaInfo VideoBitDepth}, {MediaInfo VideoDynamicRangeType}
    #   {MediaInfo AudioCodec}, {MediaInfo AudioChannels}, {MediaInfo AudioLanguages}, {MediaInfo 3D}
    #   {Release Group}, {-Release Group}
    #   {TvdbId}, {TmdbId}, {ImdbId}, {Edition Tags}
    # Conditional groups: {[Token]}, {[Token1}{ Token2]}, [{Token}suffix], {-Token}, {tvdb-{TvdbId}}
    # Old v0.10.0 simple tokens ({title}, {year}, {episode_title}, {ext}, {quality}) still work.
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
    # When True, builds offer a "Rename to scheme" affordance and the API will
    # accept rename requests. Set to False to keep the codepath dormant for
    # users who only ever want NFO/artwork generation.
    rename_enabled: bool = True
    # v0.11.10: at the end of every successful build, sweep orphaned NFO
    # and thumbnail companions left behind by Sonarr/Radarr file upgrades.
    # See ``services/orphans.py`` for the full rationale — in short, when a
    # release group changes, Sonarr swaps the .mkv but leaves the old
    # ``<stem>.nfo`` and ``<stem>-thumb.jpg`` orphaned. Plex's NFO agent then
    # reads those orphans and creates a duplicate library entry for the show.
    # The sweep is video-driven (only deletes companions whose stem doesn't
    # match a live video file) and never touches show-level artwork or
    # ``tvshow.nfo`` / ``season.nfo``.
    auto_sweep_orphans: bool = True
    # v0.11.12: per-provider artwork language filters.
    #
    # Each list is a whitelist of language codes that artwork must be
    # tagged with to be considered during a build. TVDB uses 3-letter
    # ISO 639-2 codes ("eng", "jpn", "fra"); TMDB uses 2-letter ISO 639-1
    # codes ("en", "ja", "fr"). An empty list means "no filter" — every
    # language is allowed (the legacy behaviour).
    #
    # The companion ``*_artwork_allow_null_language`` flags control
    # whether artwork uploaded *without* a language tag is allowed
    # through. TVDB uses an empty/null ``language`` field; TMDB uses
    # ``iso_639_1: null``. Many of the cleanest, text-free posters fall
    # in this bucket so it's enabled by default.
    #
    # When the filter would rule out *every* candidate for a slot, the
    # builder falls back to the unfiltered best pick so the show still
    # gets a poster — the filter is a preference, not a guarantee.
    tvdb_artwork_languages: List[str] = []
    tvdb_artwork_allow_null_language: bool = True
    tmdb_artwork_languages: List[str] = []
    tmdb_artwork_allow_null_language: bool = True

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
