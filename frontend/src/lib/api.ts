export type Library = { name: string; kind: string; enabled: number; detected_at: number };

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
  override_episode_id: string | null;
  matched_episode_id: string | null;
  matched_season: number | null;
  matched_number: number | null;
  matched_title: string | null;
};

export type TvdbEpisode = {
  id: string;
  season: number | null;
  number: number | null;
  name: string | null;
  aired: string | null;
  image: string | null;
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
  },
  items: {
    list: (params: { library?: string; status?: string; q?: string; hide_organized?: boolean }) => {
      const qs = new URLSearchParams();
      if (params.library) qs.set("library", params.library);
      if (params.status) qs.set("status", params.status);
      if (params.q) qs.set("q", params.q);
      if (params.hide_organized) qs.set("hide_organized", "true");
      return J<{ items: Item[] }>(fetch(`/api/items?${qs}`));
    },
    detail: (path: string) =>
      J<any>(fetch(`/api/items/detail?path=${encodeURIComponent(path)}`)),
    remove: (folder_path: string) =>
      J<{ ok: true; removed: number }>(
        fetch("/api/items/remove", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ folder_path }),
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
  logs: () => J<{ lines: string[] }>(fetch("/api/logs/app?tail=400")),
  tvdb: {
    series: (id: string) => J<any>(fetch(`/api/tvdb/series/${encodeURIComponent(id)}`)),
    movie: (id: string) => J<any>(fetch(`/api/tvdb/movie/${encodeURIComponent(id)}`)),
    clearCache: () => J(fetch("/api/tvdb/cache/clear", { method: "POST" })),
  },
};
