import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

type Overrides = Record<string, Record<string, string>>;

const FIELDS: { key: string; label: string; multiline?: boolean }[] = [
  { key: "title", label: "Title" },
  { key: "sorttitle", label: "Sort title" },
  { key: "originaltitle", label: "Original title" },
  { key: "tagline", label: "Tagline" },
  { key: "plot", label: "Plot", multiline: true },
];

export default function OverridesTab({
  path,
  kind,
  binding,
}: {
  path: string;
  kind: "series" | "movie";
  binding: any;
}) {
  const qc = useQueryClient();
  const overridesQ = useQuery({
    queryKey: ["overrides", path],
    queryFn: () => api.overrides.get(path),
  });
  const episodesQ = useQuery({
    queryKey: ["episodes", path],
    queryFn: () => api.episodes.list(path),
    enabled: kind === "series" && !!binding,
  });

  const overrides: Overrides = overridesQ.data?.overrides ?? {};

  // Provider override state
  const [provider, setProvider] = useState<"tvdb" | "tmdb">(
    (binding?.provider as any) || "tvdb",
  );
  const [locked, setLocked] = useState<boolean>(
    !!(binding && Number(binding.source_locked || 0) === 1),
  );
  const [savingSrc, setSavingSrc] = useState(false);
  const [srcMsg, setSrcMsg] = useState<string | null>(null);

  useEffect(() => {
    setProvider((binding?.provider as any) || "tvdb");
    setLocked(!!(binding && Number(binding.source_locked || 0) === 1));
  }, [binding?.provider, binding?.source_locked]);

  const localSeasons = useMemo(() => {
    const set = new Set<number>();
    for (const e of episodesQ.data?.locals ?? []) {
      if (typeof e.parsed_season === "number" && e.parsed_season > 0) {
        set.add(e.parsed_season);
      }
    }
    // Also include any season scopes present in overrides
    for (const scope of Object.keys(overrides)) {
      const m = scope.match(/^season-(\d{2})$/);
      if (m) set.add(Number(m[1]));
    }
    return Array.from(set).sort((a, b) => a - b);
  }, [episodesQ.data, overrides]);

  const matchedEpisodes = useMemo(() => {
    const arr = (episodesQ.data?.locals ?? []).filter(
      (e) => !!e.matched_episode_id,
    );
    arr.sort((a, b) => {
      const sa = a.matched_season ?? a.parsed_season ?? 0;
      const sb = b.matched_season ?? b.parsed_season ?? 0;
      if (sa !== sb) return sa - sb;
      const na = a.matched_number ?? a.parsed_episode ?? 0;
      const nb = b.matched_number ?? b.parsed_episode ?? 0;
      return na - nb;
    });
    return arr;
  }, [episodesQ.data]);

  return (
    <div className="space-y-6">
      {/* Provider override */}
      <section className="bg-slate-900 border border-slate-800 rounded p-4">
        <h3 className="font-semibold mb-1">Metadata source</h3>
        <p className="text-xs text-slate-500 mb-3">
          Switch which provider this folder uses, independent of the global default.
          Lock it to prevent auto-match from changing it later.
        </p>
        {!binding ? (
          <div className="text-sm text-slate-400">
            Bind this folder from the Overview tab first to choose a provider.
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value as any)}
              className="bg-slate-800 px-2 py-1 rounded text-sm border border-slate-700"
            >
              <option value="tvdb">TVDB</option>
              <option value="tmdb">TMDB</option>
            </select>
            <label className="text-xs text-slate-300 inline-flex items-center gap-2">
              <input
                type="checkbox"
                checked={locked}
                onChange={(e) => setLocked(e.target.checked)}
              />
              Lock for this show (auto-match won't change it)
            </label>
            <button
              disabled={savingSrc}
              className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded text-xs disabled:opacity-50"
              onClick={async () => {
                setSavingSrc(true);
                setSrcMsg(null);
                try {
                  const sameProvider = provider === binding.provider;
                  await api.match.setSource({
                    folder_path: path,
                    provider,
                    external_id: sameProvider ? binding.external_id : undefined,
                    locked,
                    kind: binding.kind,
                    title: binding.title,
                    year: binding.year,
                  });
                  setSrcMsg("Saved.");
                  qc.invalidateQueries({ queryKey: ["detail", path] });
                } catch (e: any) {
                  setSrcMsg(`Failed: ${e?.message ?? e}`);
                } finally {
                  setSavingSrc(false);
                }
              }}
            >
              Save
            </button>
            {srcMsg && <span className="text-xs text-slate-400">{srcMsg}</span>}
          </div>
        )}
        {binding && provider !== binding.provider && (
          <div className="text-[11px] text-amber-300 mt-2">
            Switching providers requires the new provider's external id. If you've
            never matched this folder to {provider.toUpperCase()}, use the Overview tab
            to search and bind first.
          </div>
        )}
      </section>

      {/* Main scope */}
      <ScopeBlock
        title={kind === "series" ? "Series fields" : "Movie fields"}
        description="Override what gets written into the main NFO. Empty fields fall back to the source provider."
        path={path}
        scope={kind === "series" ? "series" : "movie"}
        values={overrides[kind === "series" ? "series" : "movie"] ?? {}}
        onChanged={() => qc.invalidateQueries({ queryKey: ["overrides", path] })}
      />

      {/* Seasons (series only) */}
      {kind === "series" && (
        <section>
          <h3 className="font-semibold mb-2">Seasons</h3>
          {localSeasons.length === 0 ? (
            <div className="text-xs text-slate-500">
              No seasons detected yet. Run a scan or build first.
            </div>
          ) : (
            <div className="space-y-2">
              {localSeasons.map((s) => {
                const scope = `season-${String(s).padStart(2, "0")}`;
                return (
                  <Collapsible
                    key={scope}
                    title={`Season ${s}`}
                    badge={hasAny(overrides[scope]) ? "edited" : null}
                  >
                    <ScopeBlock
                      title=""
                      description=""
                      path={path}
                      scope={scope}
                      values={overrides[scope] ?? {}}
                      onChanged={() =>
                        qc.invalidateQueries({ queryKey: ["overrides", path] })
                      }
                      compact
                    />
                  </Collapsible>
                );
              })}
            </div>
          )}
        </section>
      )}

      {/* Episodes (series only).
          v0.11.9 — each episode collapsible now also contains an inline
          thumbnail picker (TMDB ships multiple stills per episode; the user
          asked to be able to pick which one gets written as <stem>-thumb.jpg).
          The flat "Episode thumbnails" gallery from v0.11.8 has been removed
          because the per-episode picker covers the same provider-vs-on-disk
          comparison while also letting the user act on it. */}
      {kind === "series" && (
        <section>
          <h3 className="font-semibold mb-2">Episodes</h3>
          {!binding ? (
            <div className="text-xs text-slate-500">
              Bind first to override individual episodes.
            </div>
          ) : matchedEpisodes.length === 0 ? (
            <div className="text-xs text-slate-500">
              No matched local episodes yet. Run a scan or build to populate matches.
            </div>
          ) : (
            <div className="space-y-1.5">
              {matchedEpisodes.map((e) => {
                const eid = e.matched_episode_id!;
                const scope = `episode-${eid}`;
                const s = e.matched_season ?? e.parsed_season ?? 0;
                const n = e.matched_number ?? e.parsed_episode ?? 0;
                const code = `S${String(s).padStart(2, "0")}E${String(n).padStart(2, "0")}`;
                return (
                  <Collapsible
                    key={scope}
                    title={`${code} — ${e.matched_title ?? e.file_name}`}
                    badge={hasAny(overrides[scope]) ? "edited" : null}
                    dense
                  >
                    <ScopeBlock
                      title=""
                      description=""
                      path={path}
                      scope={scope}
                      values={overrides[scope] ?? {}}
                      onChanged={() =>
                        qc.invalidateQueries({ queryKey: ["overrides", path] })
                      }
                      compact
                    />
                    <EpisodeThumbPicker
                      path={path}
                      season={s}
                      episode={n}
                      localThumbPath={e.local_thumb ?? null}
                    />
                  </Collapsible>
                );
              })}
            </div>
          )}
        </section>
      )}

      <div className="text-[11px] text-slate-500">
        Overrides are saved into both the database and a sidecar file
        (<code className="font-mono">.plex-nfo-builder.json</code>) inside the folder, so
        they survive a database wipe. Run "Force rebuild" on the Overview tab to apply
        changes to the NFO files on disk.
      </div>
    </div>
  );
}

/**
 * v0.11.9 — inline per-episode thumbnail picker rendered inside each
 * episode's collapsible. TMDB ships multiple stills per episode; the picker
 * grids them out and lets the user select which one gets written as
 * ``<stem>-thumb.jpg`` on the next build. TVDB only ships a single still per
 * episode, so for TVDB-bound shows the picker degrades to a one-tile grid
 * plus an explanatory note.
 *
 * Selections persist in the existing ``artwork_selections`` table under slot
 * ``episode-thumb-{external_id}`` (no schema change), so:
 *   - renames don't reset the choice (key is the provider id, not file path),
 *   - the choice round-trips through ``.plex-nfo-builder.json``,
 *   - a DB wipe is recoverable from disk.
 */
function EpisodeThumbPicker({
  path,
  season,
  episode,
  localThumbPath,
}: {
  path: string;
  season: number;
  episode: number;
  localThumbPath: string | null;
}) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["thumb-candidates", path, season, episode],
    queryFn: () => api.episodes.thumbCandidates(path, season, episode),
  });

  const localThumbUrl = localThumbPath ? api.artwork.fileUrl(localThumbPath) : null;

  async function pick(url: string | null) {
    const eid = q.data?.external_id;
    if (!eid) return;
    await api.episodes.thumbSelect({
      folder_path: path,
      external_id: eid,
      url,
    });
    await qc.invalidateQueries({ queryKey: ["thumb-candidates", path, season, episode] });
  }

  return (
    <div className="mt-3 pt-3 border-t border-slate-800">
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <div className="text-xs font-semibold text-slate-300">Episode thumbnail</div>
        <div className="text-[10px] text-slate-500">
          Saved to <code className="font-mono">&lt;file&gt;-thumb.jpg</code> on next build
        </div>
      </div>
      {q.isLoading && (
        <div className="text-[11px] text-slate-500">Loading candidates…</div>
      )}
      {q.isError && (
        <div className="text-[11px] text-amber-300">
          Failed to load: {(q.error as any)?.message ?? String(q.error)}
        </div>
      )}
      {q.data && (
        <>
          {q.data.note && (
            <div className="text-[11px] text-amber-300 mb-2">{q.data.note}</div>
          )}
          {/* Top row: "On disk" preview so the user can compare what Plex
              currently sees against what they're about to pick. */}
          {localThumbUrl && (
            <div className="mb-3">
              <div className="text-[10px] text-slate-500 uppercase tracking-wide mb-1">
                Currently on disk
              </div>
              <div className="aspect-video w-48 bg-slate-800 rounded overflow-hidden">
                <img
                  src={localThumbUrl}
                  alt="on-disk thumb"
                  className="w-full h-full object-cover"
                  loading="lazy"
                />
              </div>
            </div>
          )}
          {q.data.candidates.length === 0 ? (
            <div className="text-[11px] text-slate-500">
              No thumbnail candidates available for this episode.
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
              {/* Auto / clear-override tile. "Auto" wins when the user has
                  not picked anything explicitly — the builder uses the
                  provider's default still. */}
              <button
                onClick={() => pick(null)}
                disabled={q.data.current_selection === null}
                title="Use the provider default"
                className={`aspect-video rounded border flex flex-col items-center justify-center text-[11px] font-semibold ${
                  q.data.current_selection === null
                    ? "border-indigo-400 bg-indigo-900/30 text-indigo-200 ring-2 ring-indigo-500"
                    : "border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800"
                }`}
              >
                <span>Auto</span>
                <span className="text-[9px] font-normal text-slate-400">
                  use provider default
                </span>
              </button>
              {q.data.candidates.map((c) => (
                <button
                  key={c.url}
                  onClick={() => pick(c.url)}
                  className={`relative aspect-video rounded overflow-hidden border ${
                    c.selected
                      ? "border-indigo-400 ring-2 ring-indigo-500"
                      : "border-slate-700 hover:border-slate-500"
                  }`}
                  title={
                    [
                      c.width && c.height ? `${c.width}×${c.height}` : null,
                      c.language ? `lang ${c.language}` : null,
                      c.is_default ? "default still" : null,
                    ]
                      .filter(Boolean)
                      .join(" · ") || undefined
                  }
                >
                  <img
                    src={c.thumb ?? c.url}
                    alt=""
                    loading="lazy"
                    className="w-full h-full object-cover"
                  />
                  {c.is_default && (
                    <span className="absolute top-1 left-1 text-[9px] px-1 py-0.5 rounded bg-slate-900/80 text-slate-200 uppercase tracking-wide">
                      default
                    </span>
                  )}
                  {c.language && (
                    <span className="absolute top-1 right-1 text-[9px] px-1 py-0.5 rounded bg-slate-900/80 text-slate-200 uppercase tracking-wide">
                      {c.language}
                    </span>
                  )}
                  {c.selected && (
                    <span className="absolute bottom-1 right-1 text-[9px] px-1.5 py-0.5 rounded bg-indigo-600 text-white font-semibold uppercase tracking-wide">
                      Selected
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function hasAny(rec: Record<string, string> | undefined) {
  if (!rec) return false;
  return Object.values(rec).some((v) => (v ?? "").trim() !== "");
}

function Collapsible({
  title,
  badge,
  children,
  dense,
}: {
  title: string;
  badge?: string | null;
  children: any;
  dense?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="bg-slate-900 border border-slate-800 rounded">
      <button
        onClick={() => setOpen((v) => !v)}
        className={`w-full flex items-center gap-2 px-3 ${dense ? "py-1.5" : "py-2"} text-left hover:bg-slate-800/60`}
      >
        <span className="text-slate-500 text-xs">{open ? "▾" : "▸"}</span>
        <span className={`flex-1 ${dense ? "text-xs" : "text-sm"} truncate`}>{title}</span>
        {badge && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-700/40 text-indigo-200 uppercase tracking-wide">
            {badge}
          </span>
        )}
      </button>
      {open && <div className="p-3 border-t border-slate-800">{children}</div>}
    </div>
  );
}

function ScopeBlock({
  title,
  description,
  path,
  scope,
  values,
  onChanged,
  compact,
}: {
  title?: string;
  description?: string;
  path: string;
  scope: string;
  values: Record<string, string>;
  onChanged: () => void;
  compact?: boolean;
}) {
  return (
    <section className={compact ? "" : "bg-slate-900 border border-slate-800 rounded p-4"}>
      {title && <h3 className="font-semibold mb-1">{title}</h3>}
      {description && <p className="text-xs text-slate-500 mb-3">{description}</p>}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {FIELDS.map((f) => (
          <FieldRow
            key={f.key}
            field={f.key}
            label={f.label}
            multiline={!!f.multiline}
            value={values[f.key] ?? ""}
            path={path}
            scope={scope}
            onChanged={onChanged}
          />
        ))}
      </div>
    </section>
  );
}

function FieldRow({
  field,
  label,
  multiline,
  value,
  path,
  scope,
  onChanged,
}: {
  field: string;
  label: string;
  multiline?: boolean;
  value: string;
  path: string;
  scope: string;
  onChanged: () => void;
}) {
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const lastSavedRef = useRef<string>(value);

  // Sync external value (e.g. after refetch) when it differs and user hasn't typed
  useEffect(() => {
    if (draft === lastSavedRef.current) {
      setDraft(value);
      lastSavedRef.current = value;
    }
  }, [value]);

  const dirty = draft !== lastSavedRef.current;

  async function save() {
    setSaving(true);
    try {
      const trimmed = draft;
      if (trimmed.trim() === "") {
        await api.overrides.clear({ folder_path: path, scope, field });
      } else {
        await api.overrides.set({
          folder_path: path,
          scope,
          field,
          value: trimmed,
        });
      }
      lastSavedRef.current = trimmed;
      setSavedAt(Date.now());
      onChanged();
    } finally {
      setSaving(false);
    }
  }

  async function reset() {
    setDraft("");
    setSaving(true);
    try {
      await api.overrides.clear({ folder_path: path, scope, field });
      lastSavedRef.current = "";
      setSavedAt(Date.now());
      onChanged();
    } finally {
      setSaving(false);
    }
  }

  const overridden = (lastSavedRef.current ?? "").trim() !== "";

  return (
    <div className={multiline ? "md:col-span-2" : ""}>
      <div className="flex items-center justify-between mb-1">
        <label className="text-xs text-slate-400 inline-flex items-center gap-2">
          {label}
          {overridden && (
            <span className="text-[9px] px-1 py-0.5 rounded bg-indigo-700/40 text-indigo-200 uppercase tracking-wide">
              override
            </span>
          )}
        </label>
        <div className="flex items-center gap-2">
          {savedAt && !dirty && !saving && (
            <span className="text-[10px] text-emerald-400">saved</span>
          )}
          {overridden && (
            <button
              onClick={reset}
              disabled={saving}
              className="text-[10px] text-slate-400 hover:text-slate-200 underline disabled:opacity-50"
            >
              reset to source
            </button>
          )}
        </div>
      </div>
      {multiline ? (
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => {
            if (dirty) save();
          }}
          rows={4}
          placeholder="(use source value)"
          className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm font-sans"
        />
      ) : (
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => {
            if (dirty) save();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          }}
          placeholder="(use source value)"
          className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm"
        />
      )}
    </div>
  );
}
