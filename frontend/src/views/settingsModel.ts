/** Editable configuration. Secret values are write-only; GET reports presence. */
export type Settings = {
  preferred_language: string;
  fallback_languages: string[];
  include_original_title: boolean;
  cache_ttl_hours: number;
  overwrite_foreign_nfo: boolean;
  auto_match_threshold: number;
  metadata_source: string;
  fanart_enabled: boolean;
  tmdb_artwork_enabled: boolean;
  preferred_artwork_source: string;
  plex_url: string | null;
  plex_auto_refresh: boolean;
  plex_refresh_delay_seconds: number;
  plex_path_mappings: { from: string; to: string }[];
  rename_episode_template: string;
  rename_daily_template: string;
  rename_anime_template: string;
  rename_series_folder_template: string;
  rename_season_folder_template: string;
  rename_movie_template: string;
  rename_movie_folder_template: string;
  rename_enabled: boolean;
  auto_sweep_orphans: boolean;
  tvdb_artwork_languages: string[];
  tvdb_artwork_allow_null_language: boolean;
  tmdb_artwork_languages: string[];
  tmdb_artwork_allow_null_language: boolean;
  watcher_enabled: boolean | null;
  watcher_debounce_seconds: number | null;
  tvdb_api_key_configured: boolean;
  tvdb_pin_configured: boolean;
  tmdb_api_key_configured: boolean;
  omdb_api_key_configured?: boolean;
  fanart_api_key_configured: boolean;
  plex_token_configured: boolean;
};

export type UpdateSetting = <K extends keyof Settings>(
  key: K,
  value: Settings[K],
) => void;

/** Send only edits, so separate watcher controls and other clients retain changes. */
export function settingsPatch(
  saved: Settings,
  draft: Settings,
): Partial<Settings> {
  return Object.fromEntries(
    Object.entries(draft).filter(
      ([key, value]) =>
        !key.endsWith("_configured") &&
        JSON.stringify(value) !== JSON.stringify(saved[key as keyof Settings]),
    ),
  );
}
