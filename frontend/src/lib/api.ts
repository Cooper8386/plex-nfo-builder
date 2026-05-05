export type Library = {
  name: string;
  kind: string;
  enabled: number;
  detected_at: number;
  /**
   * Per-library override for the metadata source. `null`/missing means the
   * library follows the global setting. v0.7.0+.
   */
  metadata_source?: string | null;
  /** Resolved source after applying the override (if any). v0.7.0+. */
  effective_metadata_source?: string;
};

export type ArtworkProvider = "tvdb" | "tmdb" | "fanart" | "custom";

export type ArtworkCandidate = {
  id: number | string | null;
  url: string;
  thumb: string;
  language: string | null;
  score: number;
  type: number | null;
  seasonNumber: number | null;
  provider?: ArtworkProvider;
  origin?: string;
  source?: "upload" | "url";
  slot?: string | null;
};

export type LocalEpisode = {
  file_path: string;
  file_name: string;
  parsed_season: number;
  parsed_episode: number;
  /** v0.10.0: season/episode after applying any per-file user override. */
  effective_season: number | null;
  effective_episode: number | null;
  override_episode_id: string | null;
  matched_episode_id: string | null;
  matched_season: number | null;
  matched_number: number | null;
  matched_title: string | null;
  /** True when the parser couldn't extract season/episode from the filename. */
  unparsed?: boolean;
  /** True when the user has set a per-file override for this row. */
  has_file_override?: boolean;
};

export type RenamePlanItem = {
  src: string;
  dst: string;
  src_name: string;
  dst_name: string;
  season: number | null;
  episode: number | null;
  matched_title: string | null;
  conflict: "exists" | "duplicate" | null;
  unchanged: boolean;
};

export type TvdbEpisode = {
  id: string;
  season: number | null;
  number: number | null;
  name: string | null;
  aired: string | null;
  image: string | null;
};

export type ScheduleAction =
  | "scan_only"
  | "match_only"
  | "build_only"
  | "match_and_build"
  | "full";

export type Schedule = {
  id: number;
  library: string | null;
  cron: string;
  action: ScheduleAction;
  enabled: number;
  last_run: number | null;
  last_status: "ok" | "error" | "running" | null;
  last_message: string | null;
  created_at: number;
  updated_at: number;
};

export type Item = {
  folder_path: string;
  library: string;
  kind: "series" | "movie";
  title: string;
  year: number | null;
  external_id: string | null;
  provider: string | null;
  nfo_status: "none" | "partial" | "complete" | "stale" | "foreign" | "mixed" | null;
  episode_count_local: number | null;
  episode_count_tvdb: number | null;
  poster_path: string | null;
  last_built?: number | null;
};

const J = <T,>(p: Promise<Response>): Promise<T> =>
  p.then(async (r) => {
    if (!r.ok) throw new Error(await r.text());
    return r.json() as Promise<T>;
  });

export const api = {
  health: () => J<{ ok: boolean; tvdb_configured: boolean }>(fetch("/api/health")),
  settings: {
    get: () => J<any>(fetch("/api/settings")),
    set: (body: any) =>
      J<{ ok: true }>(
        fetch("/api/settings", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
  },
  browse: (path?: string) =>
    J<{ path: string; parent: string | null; items: any[] }>(
      fetch(`/api/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`)
    ),
  libraries: {
    list: () => J<{ libraries: Library[] }>(fetch("/api/libraries")),
    detect: () => J<{ libraries: any[] }>(fetch("/api/libraries/detect", { method: "POST" })),
    update: (name: string, body: any) =>
      J(
        fetch(`/api/libraries/${encodeURIComponent(name)}`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
    scan: (name: string) =>
      J(fetch(`/api/libraries/${encodeURIComponent(name)}/scan`, { method: "POST" })),
    remove: (name: string) =>
      J<{ ok: true; items: number; bindings: number }>(
        fetch(`/api/libraries/${encodeURIComponent(name)}`, { method: "DELETE" })
      ),
  },
  items: {
    list: (params: { library?: string; q?: string }) => {
      const qs = new URLSearchParams();
      if (params.library) qs.set("library", params.library);
      if (params.q) qs.set("q", params.q);
      return J<{ items: Item[] }>(fetch(`/api/items?${qs}`));
    },
    detail: (path: string) =>
      J<{
        path: string;
        binding: any;
        state: any;
        artwork_files: string[];
        overrides: Record<string, Record<string, string>>;
        provider_episode_count: number | null;
        provider_used: string | null;
        tags: { tvdb: string[]; tmdb: string[]; custom: string[] };
      }>(fetch(`/api/items/detail?path=${encodeURIComponent(path)}`)),
    tags: {
      add: (folder_path: string, tag: string) =>
        J<{ ok: true; added: boolean; tags: string[] }>(
          fetch("/api/items/tags", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ folder_path, tag }),
          })
        ),
      remove: (folder_path: string, tag: string) =>
        J<{ ok: true; removed: number; tags: string[] }>(
          fetch(
            `/api/items/tags?folder_path=${encodeURIComponent(folder_path)}&tag=${encodeURIComponent(tag)}`,
            { method: "DELETE" }
          )
        ),
    },
    remove: (folder_path: string) =>
      J<{ ok: true; removed: number }>(
        fetch("/api/items/remove", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ folder_path }),
        })
      ),
    clean: (body: {
      folder_path: string;
      dry_run?: boolean;
      keep_sidecar?: boolean;
      rescan?: boolean;
    }) =>
      J<{
        ok: true;
        dry_run?: boolean;
        files?: string[];
        nfo_deleted?: number;
        artwork_deleted?: number;
        sidecar_deleted?: number;
      }>(
        fetch("/api/items/clean", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
    prune: (body: { library?: string; dry_run?: boolean }) =>
      J<{
        ok: true;
        checked: number;
        missing: number;
        removed: number;
        items: { folder_path: string; library: string | null; title: string | null }[];
      }>(
        fetch("/api/items/prune", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
  },
  match: {
    search: (
      q: string,
      type: "series" | "movie",
      year?: number,
      language?: string,
      provider?: "tvdb" | "tmdb"
    ) => {
      const qs = new URLSearchParams({ q, type });
      if (year) qs.set("year", String(year));
      if (language) qs.set("language", language);
      if (provider) qs.set("provider", provider);
      return J<{ results: any[]; provider: string }>(fetch(`/api/match/search?${qs}`));
    },
    bind: (body: any) =>
      J(
        fetch("/api/match/bind", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
    setSource: (body: {
      folder_path: string;
      provider: "tvdb" | "tmdb";
      external_id?: string | null;
      locked: boolean;
      kind?: "series" | "movie";
      title?: string;
      year?: number | null;
    }) =>
      J<{ ok: true }>(
        fetch("/api/match/source", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
    autoBulk: (body: {
      folder_paths?: string[];
      library?: string;
      only_unmatched?: boolean;
      language?: string;
    }) =>
      J<{ ok: true; total: number; matched: number; results: any[] }>(
        fetch("/api/match/auto-bulk", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
  },
  build: (folder_path: string, kind?: "series" | "movie", force = false, language?: string) =>
    J<{ ok: true; job: string }>(
      fetch("/api/build", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ folder_path, kind, force, language }),
      })
    ),
  buildBulk: (body: {
    folder_paths?: string[];
    library?: string;
    only_unbuilt?: boolean;
    force?: boolean;
    language?: string;
  }) =>
    J<{ ok: true; queued: number; jobs: any[] }>(
      fetch("/api/build/bulk", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      })
    ),
  jobs: {
    list: () => J<{ jobs: any[] }>(fetch("/api/jobs")),
  },
  artwork: {
    fileUrl: (path: string) => `/api/artwork/file?path=${encodeURIComponent(path)}`,
    candidates: (path: string, kind: "series" | "movie" = "series") =>
      J<{
        path: string;
        kind: string;
        slots: Record<string, ArtworkCandidate[]>;
        selections: Record<string, { url: string; language: string | null; score: number | null }>;
        binding_provider?: string;
      }>(
        fetch(
          `/api/artwork/candidates?path=${encodeURIComponent(path)}&kind=${kind}`
        )
      ),
    select: (body: { folder_path: string; slot: string; url: string; language?: string; score?: number }) =>
      J<{ ok: true }>(
        fetch("/api/artwork/select", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
    clear: (body: { folder_path: string; slot?: string }) =>
      J<{ ok: true; cleared: number }>(
        fetch("/api/artwork/clear", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
    upload: (folder_path: string, file: File, slot?: string) => {
      const fd = new FormData();
      fd.append("folder_path", folder_path);
      if (slot) fd.append("slot", slot);
      fd.append("file", file);
      return J<{ ok: true; id: string; url: string; slot: string | null; origin: string; size: number }>(
        fetch("/api/artwork/upload", { method: "POST", body: fd })
      );
    },
    addUrl: (body: { folder_path: string; url: string; slot?: string }) =>
      J<{ ok: true; id: string; url: string; slot: string | null }>(
        fetch("/api/artwork/custom-url", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
    deleteCustom: (id: string) =>
      J<{ ok: true; deleted: number }>(
        fetch(`/api/artwork/custom/${encodeURIComponent(id)}`, { method: "DELETE" })
      ),
    listCustom: (folder_path: string) =>
      J<{ items: any[] }>(
        fetch(`/api/artwork/custom?folder_path=${encodeURIComponent(folder_path)}`)
      ),
  },
  episodes: {
    list: (path: string) =>
      J<{
        path: string;
        provider: "tvdb" | "tmdb";
        locals: LocalEpisode[];
        tvdb_episodes: TvdbEpisode[];
      }>(fetch(`/api/episodes?path=${encodeURIComponent(path)}`)),
    override: (body: {
      folder_path: string;
      season: number;
      episode: number;
      tvdb_episode_id: string | null;
    }) =>
      J<{ ok: true }>(
        fetch("/api/episodes/override", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
    /** v0.10.0 — per-file override anchored to the actual file path. */
    overrideFile: (body: {
      folder_path: string;
      file_path: string;
      season?: number | null;
      episode?: number | null;
      external_id?: string | null;
      clear?: boolean;
    }) =>
      J<{ ok: true }>(
        fetch("/api/episodes/override-file", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
    rename: {
      preview: (body: { folder_path: string; template?: string }) =>
        J<{ folder_path: string; template: string; items: RenamePlanItem[] }>(
          fetch("/api/episodes/rename/preview", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body),
          })
        ),
      apply: (body: {
        folder_path: string;
        template?: string;
        only_src?: string[];
      }) =>
        J<{
          ok: true;
          renamed: { src: string; dst: string }[];
          skipped: { src: string; dst?: string; reason: string }[];
          failed: { src: string; dst?: string; reason: string }[];
        }>(
          fetch("/api/episodes/rename/apply", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body),
          })
        ),
    },
  },
  overrides: {
    get: (path: string) =>
      J<{ path: string; overrides: Record<string, Record<string, string>> }>(
        fetch(`/api/overrides?path=${encodeURIComponent(path)}`)
      ),
    set: (body: { folder_path: string; scope: string; field: string; value: string }) =>
      J<{ ok: true }>(
        fetch("/api/overrides", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
    clear: (body: { folder_path: string; scope?: string; field?: string }) =>
      J<{ ok: true; cleared: number }>(
        fetch("/api/overrides/clear", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
  },
  schedules: {
    list: () =>
      J<{ schedules: Schedule[] }>(fetch("/api/schedules")),
    create: (body: { library?: string | null; cron: string; action: ScheduleAction; enabled?: boolean }) =>
      J<{ ok: true; schedule: Schedule }>(
        fetch("/api/schedules", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
    update: (id: number, body: { library?: string | null; cron?: string; action?: ScheduleAction; enabled?: boolean }) =>
      J<{ ok: true; schedule: Schedule }>(
        fetch(`/api/schedules/${id}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        })
      ),
    remove: (id: number) =>
      J<{ ok: true; deleted: number }>(
        fetch(`/api/schedules/${id}`, { method: "DELETE" })
      ),
    run: (id: number) =>
      J<{ ok: true; started: boolean }>(
        fetch(`/api/schedules/${id}/run`, { method: "POST" })
      ),
  },
  logs: () => J<{ lines: string[] }>(fetch("/api/logs/app?tail=400")),
  tvdb: {
    series: (id: string) => J<any>(fetch(`/api/tvdb/series/${encodeURIComponent(id)}`)),
    movie: (id: string) => J<any>(fetch(`/api/tvdb/movie/${encodeURIComponent(id)}`)),
    clearCache: () => J(fetch("/api/tvdb/cache/clear", { method: "POST" })),
  },
  plex: {
    test: () =>
      J<{
        ok: boolean;
        error?: string;
        identity?: { friendly_name?: string; version?: string; machine_identifier?: string };
        sections?: { id: string; key: string; title: string; type: string; locations: string[] }[];
      }>(fetch("/api/plex/test")),
    sections: () =>
      J<{ sections: { id: string; key: string; title: string; type: string; locations: string[] }[] }>(
        fetch("/api/plex/sections")
      ),
    refresh: (path: string, delay_seconds = 0) =>
      J<{
        requested_local_path: string;
        translated_path: string | null;
        section_id: string | null;
        section_title: string | null;
        rating_key: string | null;
        item_title: string | null;
        strategy: "metadata-refresh" | "partial-scan-only" | null;
        refreshed: boolean;
        error: string | null;
      }>(
        fetch("/api/plex/refresh", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ path, delay_seconds }),
        })
      ),
  },
};
