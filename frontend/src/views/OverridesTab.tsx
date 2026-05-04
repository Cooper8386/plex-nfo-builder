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
              <option value="tvdb">TheTVDB</option>
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

      {/* Episodes (series only) */}
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
