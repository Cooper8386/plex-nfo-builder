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
