import { useEffect, useMemo, useState } from "react";
import { api, Library, Schedule, ScheduleAction } from "../lib/api";
import { useConfirm } from "../components/ConfirmDialog";

type SectionKey =
  | "metadata"
  | "providers"
  | "artwork"
  | "plex"
  | "renaming"
  | "schedules"
  | "about";

const SECTIONS: { key: SectionKey; label: string; description: string }[] = [
  { key: "metadata", label: "Metadata", description: "Source, language, matching" },
  { key: "providers", label: "Providers", description: "TVDB, TMDB, fanart.tv keys" },
  { key: "artwork", label: "Artwork", description: "Which provider's images win" },
  { key: "plex", label: "Plex", description: "Server URL, token, auto-refresh" },
  { key: "renaming", label: "Renaming", description: "Sonarr/Radarr-style templates" },
  { key: "schedules", label: "Schedules", description: "Recurring scan/match/build" },
  { key: "about", label: "About", description: "Version & links" },
];

export default function SettingsView() {
  const [s, setS] = useState<any>(null);
  const [section, setSection] = useState<SectionKey>(() => {
    try {
      const v = localStorage.getItem("pnb.settings.section") as SectionKey | null;
      if (v && SECTIONS.find((x) => x.key === v)) return v;
    } catch {}
    return "metadata";
  });

  // Field state shared across panes (pending secrets typed by the user).
  const [apiKey, setApiKey] = useState("");
  const [pin, setPin] = useState("");
  const [tmdbKey, setTmdbKey] = useState("");
  const [fanartKey, setFanartKey] = useState("");
  const [plexToken, setPlexToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  useEffect(() => {
    api.settings.get().then(setS);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem("pnb.settings.section", section);
    } catch {}
  }, [section]);

  if (!s) return <div className="p-6 text-slate-500">Loading…</div>;

  const update = (k: string, v: any) => setS({ ...s, [k]: v });

  const dirty =
    !!apiKey || !!pin || !!tmdbKey || !!fanartKey || !!plexToken; // settings object itself is always sent on Save

  const saveAll = async () => {
    setSaving(true);
    try {
      const body: any = { ...s };
      delete body.tvdb_api_key_configured;
      delete body.tvdb_pin_configured;
      delete body.tmdb_api_key_configured;
      delete body.fanart_api_key_configured;
      delete body.plex_token_configured;
      if (apiKey) body.tvdb_api_key = apiKey;
      if (pin) body.tvdb_pin = pin;
      if (tmdbKey) body.tmdb_api_key = tmdbKey;
      if (fanartKey) body.fanart_api_key = fanartKey;
      if (plexToken) body.plex_token = plexToken;
      await api.settings.set(body);
      const fresh = await api.settings.get();
      setS(fresh);
      setApiKey("");
      setPin("");
      setTmdbKey("");
      setFanartKey("");
      setPlexToken("");
      setSavedMsg("Saved.");
      setTimeout(() => setSavedMsg(null), 1800);
    } catch (e: any) {
      setSavedMsg(`Save failed: ${e?.message || e}`);
      setTimeout(() => setSavedMsg(null), 4000);
    } finally {
      setSaving(false);
    }
  };

  // Schedules has its own UI and doesn't need the save bar.
  const showSaveBar = section !== "schedules" && section !== "about";

  return (
    <div className="flex h-full min-h-0">
      {/* Left rail */}
      <aside className="w-56 shrink-0 border-r border-slate-800 bg-slate-950/40 overflow-auto">
        <div className="px-4 py-4">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 mb-3">
            Settings
          </div>
          <nav className="flex flex-col gap-1">
            {SECTIONS.map((sec) => {
              const active = sec.key === section;
              return (
                <button
                  key={sec.key}
                  onClick={() => setSection(sec.key)}
                  className={`text-left px-3 py-2 rounded-md transition border ${
                    active
                      ? "bg-indigo-600/15 border-indigo-600/50 text-white"
                      : "bg-transparent border-transparent text-slate-300 hover:bg-slate-900 hover:text-white"
                  }`}
                >
                  <div className="text-sm font-medium">{sec.label}</div>
                  <div className="text-[11px] text-slate-500">{sec.description}</div>
                </button>
              );
            })}
          </nav>
        </div>
      </aside>

      {/* Pane */}
      <div className="flex-1 min-w-0 flex flex-col">
        <div className="flex-1 min-h-0 overflow-auto">
          <div className="p-6 max-w-3xl">
            {section === "metadata" && (
              <MetadataPane s={s} update={update} />
            )}
            {section === "providers" && (
              <ProvidersPane
                s={s}
                update={update}
                apiKey={apiKey}
                setApiKey={setApiKey}
                pin={pin}
                setPin={setPin}
                tmdbKey={tmdbKey}
                setTmdbKey={setTmdbKey}
                fanartKey={fanartKey}
                setFanartKey={setFanartKey}
                onClearCache={async () => {
                  await api.tvdb.clearCache();
                  setSavedMsg("Cache cleared.");
                  setTimeout(() => setSavedMsg(null), 1500);
                }}
              />
            )}
            {section === "artwork" && <ArtworkPane s={s} update={update} />}
            {section === "plex" && (
              <PlexPane
                s={s}
                setS={setS}
                update={update}
                plexToken={plexToken}
                setPlexToken={setPlexToken}
              />
            )}
            {section === "renaming" && <RenamingPane s={s} setS={setS} update={update} />}
            {section === "schedules" && <SchedulesSection />}
            {section === "about" && <AboutPane />}
          </div>
        </div>
        {showSaveBar && (
          <div className="border-t border-slate-800 bg-slate-950/80 backdrop-blur px-6 py-3 flex items-center gap-3">
            <button
              onClick={saveAll}
              disabled={saving}
              className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded text-sm disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save changes"}
            </button>
            {dirty && !saving && (
              <span className="text-xs text-amber-400">Unsaved secrets pending.</span>
            )}
            {savedMsg && <span className="text-xs text-emerald-400">{savedMsg}</span>}
            <div className="flex-1" />
            <span className="text-[11px] text-slate-500">
              Settings apply immediately to scans and builds after save.
            </span>
          </div>
        )}
        {section === "about" && savedMsg && (
          <div className="border-t border-slate-800 px-6 py-2 text-xs text-emerald-400">
            {savedMsg}
          </div>
        )}
      </div>
    </div>
  );
}

/* ----------------------- Section panes ----------------------- */

function PaneHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="mb-5">
      <h2 className="text-xl font-semibold">{title}</h2>
      {subtitle && <p className="text-sm text-slate-500 mt-1">{subtitle}</p>}
    </div>
  );
}

function MetadataPane({ s, update }: { s: any; update: (k: string, v: any) => void }) {
  return (
    <>
      <PaneHeader
        title="Metadata"
        subtitle="Which provider drives titles, descriptions, cast, and how aggressively the matcher binds shows."
      />
      <Field label="Primary metadata source">
        <select
          className="bg-slate-800 px-2 py-1 rounded"
          value={s.metadata_source || "tvdb"}
          onChange={(e) => update("metadata_source", e.target.value)}
        >
          <option value="tvdb">TVDB</option>
          <option value="tmdb">TMDB</option>
        </select>
      </Field>
      <Field label="Preferred language (3-letter)">
        <input
          className="bg-slate-800 px-2 py-1 rounded w-32"
          value={s.preferred_language}
          onChange={(e) => update("preferred_language", e.target.value)}
        />
      </Field>
      <Field label="Fallback languages (comma-separated)">
        <input
          className="bg-slate-800 px-2 py-1 rounded w-64"
          value={(s.fallback_languages || []).join(",")}
          onChange={(e) =>
            update(
              "fallback_languages",
              e.target.value.split(",").map((x: string) => x.trim())
            )
          }
        />
      </Field>
      <Field label="Cache TTL (hours)">
        <input
          type="number"
          className="bg-slate-800 px-2 py-1 rounded w-24"
          value={s.cache_ttl_hours}
          onChange={(e) => update("cache_ttl_hours", parseInt(e.target.value || "0"))}
        />
      </Field>
      <Field label="Auto-match threshold (0-100)">
        <input
          type="number"
          className="bg-slate-800 px-2 py-1 rounded w-24"
          value={s.auto_match_threshold}
          onChange={(e) => update("auto_match_threshold", parseInt(e.target.value || "0"))}
        />
      </Field>
      <Field label="Overwrite foreign NFOs">
        <input
          type="checkbox"
          checked={!!s.overwrite_foreign_nfo}
          onChange={(e) => update("overwrite_foreign_nfo", e.target.checked)}
        />
      </Field>
      <Field label="Auto-sweep orphaned sidecars">
        <div className="flex items-start gap-2">
          <input
            type="checkbox"
            checked={s.auto_sweep_orphans !== false}
            onChange={(e) => update("auto_sweep_orphans", e.target.checked)}
            className="mt-1"
          />
          <span className="text-[11px] text-slate-500 max-w-xl leading-relaxed">
            After every build, automatically delete orphaned{" "}
            <code className="text-slate-300">&lt;stem&gt;.nfo</code> and{" "}
            <code className="text-slate-300">&lt;stem&gt;-thumb.*</code> files
            left behind when Sonarr/Radarr swapped a release. Stops Plex from
            creating duplicate library entries for the same show. Live videos
            and show/season-level artwork are always preserved.
          </span>
        </div>
      </Field>
    </>
  );
}

function ProvidersPane({
  s,
  update,
  apiKey,
  setApiKey,
  pin,
  setPin,
  tmdbKey,
  setTmdbKey,
  fanartKey,
  setFanartKey,
  onClearCache,
}: {
  s: any;
  update: (k: string, v: any) => void;
  apiKey: string;
  setApiKey: (v: string) => void;
  pin: string;
  setPin: (v: string) => void;
  tmdbKey: string;
  setTmdbKey: (v: string) => void;
  fanartKey: string;
  setFanartKey: (v: string) => void;
  onClearCache: () => void;
}) {
  return (
    <>
      <PaneHeader
        title="Providers"
        subtitle="API keys for the metadata and artwork providers."
      />

      <SubHeader>TVDB</SubHeader>
      <Field label={`TVDB API key${s.tvdb_api_key_configured ? " (configured)" : ""}`}>
        <input
          className="bg-slate-800 px-2 py-1 rounded w-80"
          value={apiKey}
          placeholder={s.tvdb_api_key_configured ? "leave blank to keep current" : "paste API key"}
          onChange={(e) => setApiKey(e.target.value)}
        />
      </Field>
      <Field label={`TVDB PIN${s.tvdb_pin_configured ? " (configured)" : ""}`}>
        <input
          className="bg-slate-800 px-2 py-1 rounded w-32"
          value={pin}
          placeholder={s.tvdb_pin_configured ? "leave blank to keep current" : "subscriber PIN"}
          onChange={(e) => setPin(e.target.value)}
        />
      </Field>

      <Divider />
      <SubHeader>TMDB</SubHeader>
      <Field label={`TMDB API key${s.tmdb_api_key_configured ? " (configured)" : ""}`}>
        <input
          className="bg-slate-800 px-2 py-1 rounded w-80"
          value={tmdbKey}
          placeholder={s.tmdb_api_key_configured ? "leave blank to keep current" : "v3 API key"}
          onChange={(e) => setTmdbKey(e.target.value)}
        />
      </Field>
      <Field label="Use TMDB artwork as additional source">
        <input
          type="checkbox"
          checked={s.tmdb_artwork_enabled !== false}
          onChange={(e) => update("tmdb_artwork_enabled", e.target.checked)}
        />
      </Field>

      <Divider />
      <SubHeader>fanart.tv</SubHeader>
      <Field label={`fanart.tv API key${s.fanart_api_key_configured ? " (configured)" : ""}`}>
        <input
          className="bg-slate-800 px-2 py-1 rounded w-80"
          value={fanartKey}
          placeholder={s.fanart_api_key_configured ? "leave blank to keep current" : "personal API key"}
          onChange={(e) => setFanartKey(e.target.value)}
        />
      </Field>
      <Field label="Pull artwork from fanart.tv">
        <input
          type="checkbox"
          checked={s.fanart_enabled !== false}
          onChange={(e) => update("fanart_enabled", e.target.checked)}
        />
      </Field>

      <Divider />
      <div className="ml-64 pl-3">
        <button
          className="text-xs text-amber-400 hover:underline"
          onClick={onClearCache}
        >
          Clear metadata cache
        </button>
        <p className="text-[11px] text-slate-500 mt-1">
          Forces the next scan/match/build to re-fetch from TVDB and TMDB.
        </p>
      </div>
    </>
  );
}

function ArtworkPane({ s, update }: { s: any; update: (k: string, v: any) => void }) {
  const [langs, setLangs] = useState<{
    tvdb: { code: string; name: string; native_name: string | null }[];
    tmdb: { code: string; name: string; native_name: string | null }[];
  } | null>(null);
  const [langsErr, setLangsErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.artwork
      .languages()
      .then((d) => {
        if (alive) setLangs(d);
      })
      .catch((e) => {
        if (alive) setLangsErr(e?.message || "Failed to load languages");
      });
    return () => {
      alive = false;
    };
  }, []);

  const tvdbSelected: string[] = Array.isArray(s.tvdb_artwork_languages)
    ? s.tvdb_artwork_languages
    : [];
  const tmdbSelected: string[] = Array.isArray(s.tmdb_artwork_languages)
    ? s.tmdb_artwork_languages
    : [];

  return (
    <>
      <PaneHeader
        title="Artwork"
        subtitle="Choose which provider's images win during a build, and filter by the language the art is tagged with. Independent of the metadata source — e.g. use TVDB for descriptions and TMDB for posters."
      />
      <Field label="Preferred artwork source">
        <select
          className="bg-slate-800 px-2 py-1 rounded"
          value={s.preferred_artwork_source || "auto"}
          onChange={(e) => update("preferred_artwork_source", e.target.value)}
        >
          <option value="auto">Auto (match metadata source)</option>
          <option value="tvdb">Prefer TVDB artwork</option>
          <option value="tmdb">Prefer TMDB artwork</option>
        </select>
      </Field>
      <p className="text-xs text-slate-500 ml-64 pl-3 max-w-xl mb-6">
        Applies to posters, backgrounds, and season posters. Your per-show
        manual picks always override this. When the preferred provider can't
        be reached for a show, the metadata source's own artwork is used.
      </p>

      <div className="border-t border-slate-800 pt-4">
        <h3 className="text-sm font-semibold text-slate-200 mb-1">
          Language filter
        </h3>
        <p className="text-xs text-slate-500 mb-4 max-w-2xl">
          Whitelist which languages artwork can be tagged with. Leave the
          list empty to accept every language (the default). Codes differ
          per provider: TVDB tags with 3-letter ISO 639-2 (eng, fra, jpn);
          TMDB tags with 2-letter ISO 639-1 (en, fr, ja). When the filter
          would leave a show with no poster, the app falls back to the
          unfiltered best pick so no show ever ends up artless — the filter
          is a preference, not a guarantee.
        </p>

        {langsErr && (
          <div className="text-xs text-amber-400 mb-3">
            Could not load languages from providers: {langsErr}
          </div>
        )}

        <LanguagePicker
          title="TVDB artwork languages"
          emptyHint="No TVDB credentials configured or the API is unreachable. Add a TVDB API key under Providers to enable the picker."
          options={langs?.tvdb || []}
          selected={tvdbSelected}
          onChange={(next) => update("tvdb_artwork_languages", next)}
          allowNull={s.tvdb_artwork_allow_null_language !== false}
          onAllowNullChange={(b) => update("tvdb_artwork_allow_null_language", b)}
          codeLabel="3-letter"
        />

        <div className="h-4" />

        <LanguagePicker
          title="TMDB artwork languages"
          emptyHint="No TMDB credentials configured or the API is unreachable. Add a TMDB API key under Providers to enable the picker."
          options={langs?.tmdb || []}
          selected={tmdbSelected}
          onChange={(next) => update("tmdb_artwork_languages", next)}
          allowNull={s.tmdb_artwork_allow_null_language !== false}
          onAllowNullChange={(b) => update("tmdb_artwork_allow_null_language", b)}
          codeLabel="2-letter"
        />
      </div>
    </>
  );
}

function LanguagePicker({
  title,
  emptyHint,
  options,
  selected,
  onChange,
  allowNull,
  onAllowNullChange,
  codeLabel,
}: {
  title: string;
  emptyHint: string;
  options: { code: string; name: string; native_name: string | null }[];
  selected: string[];
  onChange: (next: string[]) => void;
  allowNull: boolean;
  onAllowNullChange: (b: boolean) => void;
  codeLabel: string;
}) {
  const [query, setQuery] = useState("");

  const selectedSet = useMemo(() => new Set(selected.map((x) => x.toLowerCase())), [selected]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) =>
        o.code.toLowerCase().includes(q) ||
        o.name.toLowerCase().includes(q) ||
        (o.native_name || "").toLowerCase().includes(q),
    );
  }, [options, query]);

  const toggle = (code: string) => {
    const lc = code.toLowerCase();
    if (selectedSet.has(lc)) {
      onChange(selected.filter((x) => x.toLowerCase() !== lc));
    } else {
      onChange([...selected, lc]);
    }
  };

  // Show selected codes that aren't in the (possibly empty) catalogue so users
  // keep visibility on codes they entered before the providers came online.
  const orphanSelections = selected.filter(
    (code) => !options.some((o) => o.code.toLowerCase() === code.toLowerCase()),
  );

  return (
    <div className="bg-slate-900/40 border border-slate-800 rounded-md p-4">
      <div className="flex items-start justify-between gap-4 mb-3">
        <div>
          <div className="text-sm font-medium text-slate-100">{title}</div>
          <div className="text-[11px] text-slate-500">
            {codeLabel} codes · {options.length ? `${options.length} available` : "no options loaded"}
            {selected.length > 0 && ` · ${selected.length} selected`}
          </div>
        </div>
        <label className="flex items-center gap-2 text-xs text-slate-300 shrink-0">
          <input
            type="checkbox"
            checked={allowNull}
            onChange={(e) => onAllowNullChange(e.target.checked)}
          />
          Include artwork with no language tag
        </label>
      </div>

      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {selected.map((code) => {
            const match = options.find((o) => o.code.toLowerCase() === code.toLowerCase());
            return (
              <button
                key={code}
                type="button"
                onClick={() => toggle(code)}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-indigo-600/20 border border-indigo-600/50 text-indigo-200 text-xs hover:bg-indigo-600/30"
                title={`Remove ${code}`}
              >
                <span className="font-mono">{code}</span>
                {match?.name ? <span className="text-indigo-300/70">· {match.name}</span> : null}
                <span aria-hidden className="text-indigo-300/70">×</span>
              </button>
            );
          })}
          <button
            type="button"
            onClick={() => onChange([])}
            className="px-2 py-0.5 rounded border border-slate-700 text-slate-400 text-xs hover:bg-slate-800"
          >
            Clear all
          </button>
        </div>
      )}

      {options.length === 0 ? (
        <div className="text-xs text-slate-500">
          {emptyHint}
          {orphanSelections.length > 0 && (
            <div className="mt-2 text-slate-400">
              Codes saved on this profile: {orphanSelections.join(", ")}
            </div>
          )}
        </div>
      ) : (
        <>
          <input
            className="bg-slate-800 px-2 py-1 rounded w-full text-sm mb-2"
            placeholder="Filter by name or code…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <div className="max-h-56 overflow-auto border border-slate-800 rounded">
            {filtered.length === 0 ? (
              <div className="text-xs text-slate-500 p-3">No languages match that filter.</div>
            ) : (
              <ul className="divide-y divide-slate-800">
                {filtered.map((o) => {
                  const isOn = selectedSet.has(o.code.toLowerCase());
                  return (
                    <li key={o.code}>
                      <button
                        type="button"
                        onClick={() => toggle(o.code)}
                        className={`w-full text-left px-3 py-1.5 flex items-center gap-3 text-sm ${
                          isOn
                            ? "bg-indigo-600/15 text-indigo-100"
                            : "text-slate-200 hover:bg-slate-800/60"
                        }`}
                      >
                        <input
                          type="checkbox"
                          readOnly
                          checked={isOn}
                          className="pointer-events-none"
                        />
                        <span className="font-mono text-xs text-slate-400 w-10">{o.code}</span>
                        <span className="flex-1 truncate">{o.name}</span>
                        {o.native_name && o.native_name !== o.name && (
                          <span className="text-xs text-slate-500 truncate">{o.native_name}</span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function PlexPane({
  s,
  setS,
  update,
  plexToken,
  setPlexToken,
}: {
  s: any;
  setS: (v: any) => void;
  update: (k: string, v: any) => void;
  plexToken: string;
  setPlexToken: (v: string) => void;
}) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<
    | null
    | {
        ok: boolean;
        error?: string;
        identity?: { friendly_name?: string; version?: string };
        sections?: { id: string; title: string; type: string; locations: string[] }[];
      }
  >(null);

  return (
    <>
      <PaneHeader
        title="Plex"
        subtitle="After a build, the app can ask Plex to rescan the show or movie folder so changes appear without manual refresh. Path mappings translate the app's view of disk (e.g. /media) to whatever Plex sees (e.g. /data)."
      />
      <Field label="Plex base URL">
        <input
          className="bg-slate-800 px-2 py-1 rounded w-80"
          value={s.plex_url || ""}
          placeholder="http://192.168.1.10:32400"
          onChange={(e) => update("plex_url", e.target.value)}
        />
      </Field>
      <Field label={`Plex token${s.plex_token_configured ? " (configured)" : ""}`}>
        <input
          type="password"
          className="bg-slate-800 px-2 py-1 rounded w-80"
          value={plexToken}
          placeholder={s.plex_token_configured ? "leave blank to keep current" : "X-Plex-Token"}
          onChange={(e) => setPlexToken(e.target.value)}
        />
      </Field>
      <Field label="Auto-refresh after each build">
        <input
          type="checkbox"
          checked={!!s.plex_auto_refresh}
          onChange={(e) => update("plex_auto_refresh", e.target.checked)}
        />
      </Field>
      <Field label="Refresh delay (seconds)">
        <input
          type="number"
          min={0}
          max={600}
          className="bg-slate-800 px-2 py-1 rounded w-24"
          value={s.plex_refresh_delay_seconds ?? 5}
          onChange={(e) =>
            update("plex_refresh_delay_seconds", parseInt(e.target.value || "0"))
          }
        />
      </Field>
      <div className="flex items-start gap-3 mb-3">
        <label className="text-sm text-slate-300 w-64 mt-1">Path mappings</label>
        <div className="flex-1">
          {(s.plex_path_mappings || []).length === 0 && (
            <div className="text-xs text-slate-500 mb-2">
              No mappings — the app's paths are sent to Plex as-is.
            </div>
          )}
          {(s.plex_path_mappings || []).map((m: any, i: number) => (
            <div key={i} className="flex items-center gap-2 mb-2">
              <input
                className="bg-slate-800 px-2 py-1 rounded w-48"
                placeholder="/media"
                value={m.from || ""}
                onChange={(e) => {
                  const next = [...(s.plex_path_mappings || [])];
                  next[i] = { ...next[i], from: e.target.value };
                  update("plex_path_mappings", next);
                }}
              />
              <span className="text-slate-500 text-sm">→</span>
              <input
                className="bg-slate-800 px-2 py-1 rounded w-48"
                placeholder="/data"
                value={m.to || ""}
                onChange={(e) => {
                  const next = [...(s.plex_path_mappings || [])];
                  next[i] = { ...next[i], to: e.target.value };
                  update("plex_path_mappings", next);
                }}
              />
              <button
                className="text-xs text-rose-400"
                onClick={() => {
                  const next = [...(s.plex_path_mappings || [])];
                  next.splice(i, 1);
                  update("plex_path_mappings", next);
                }}
              >
                remove
              </button>
            </div>
          ))}
          <button
            className="text-xs text-indigo-400"
            onClick={() =>
              update("plex_path_mappings", [
                ...(s.plex_path_mappings || []),
                { from: "", to: "" },
              ])
            }
          >
            + add mapping
          </button>
        </div>
      </div>
      <div className="flex items-center gap-3 mb-3">
        <label className="text-sm text-slate-300 w-64"></label>
        <button
          className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded text-sm disabled:opacity-50"
          disabled={testing}
          onClick={async () => {
            setTesting(true);
            setResult(null);
            try {
              if (plexToken || s.plex_url !== undefined) {
                const body: any = {
                  plex_url: s.plex_url || null,
                  plex_path_mappings: s.plex_path_mappings || [],
                };
                if (plexToken) body.plex_token = plexToken;
                await api.settings.set(body);
                if (plexToken) setPlexToken("");
                const fresh = await api.settings.get();
                setS(fresh);
              }
              const r = await api.plex.test();
              setResult(r);
            } catch (e: any) {
              setResult({ ok: false, error: String(e?.message || e) });
            } finally {
              setTesting(false);
            }
          }}
        >
          {testing ? "Testing…" : "Test connection"}
        </button>
      </div>
      {result && (
        <div className="ml-64 pl-3 mb-2">
          <div
            className={`rounded-md border p-3 text-xs ${
              result.ok
                ? "bg-emerald-900/20 border-emerald-800 text-emerald-100"
                : "bg-rose-900/20 border-rose-800 text-rose-200"
            }`}
          >
            {result.ok ? (
              <>
                <div className="font-semibold text-emerald-300">
                  Connected to {result.identity?.friendly_name || "Plex"}
                  {result.identity?.version ? ` (v${result.identity.version})` : ""}
                </div>
                {result.sections && result.sections.length > 0 ? (
                  <div className="mt-2">
                    <div className="mb-1 text-emerald-200/80">Sections:</div>
                    <ul className="list-disc ml-5 space-y-1 text-slate-300">
                      {result.sections.map((sec) => (
                        <li key={sec.id}>
                          <span className="text-slate-100">{sec.title}</span>{" "}
                          <span className="text-slate-500">({sec.type})</span>
                          {sec.locations.length > 0 && (
                            <span className="text-slate-500">
                              {" — "}
                              {sec.locations.join(", ")}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <div className="text-slate-400 mt-1">No sections found.</div>
                )}
              </>
            ) : (
              <div>{result.error || "Connection failed."}</div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function RenamingPane({
  s,
  setS,
  update,
}: {
  s: any;
  setS: (v: any) => void;
  update: (k: string, v: any) => void;
}) {
  return (
    <>
      <PaneHeader
        title="Renaming"
        subtitle="Sonarr/Radarr-compatible token grammar. Defaults match the recommended Trash Guides schemes. Quality, codec, channels, HDR type, bit depth, and audio languages are pulled from the file via ffprobe at preview time."
      />
      <Field label="Renaming enabled">
        <input
          type="checkbox"
          checked={s.rename_enabled !== false}
          onChange={(e) => update("rename_enabled", e.target.checked)}
        />
      </Field>

      <SubHeader>Episodes</SubHeader>
      <RenameTemplateField
        label="Standard episode"
        value={s.rename_episode_template || ""}
        defaultValue={DEFAULT_TEMPLATES.standard}
        onChange={(v) => update("rename_episode_template", v)}
      />
      <RenameTemplateField
        label="Daily episode"
        value={s.rename_daily_template || ""}
        defaultValue={DEFAULT_TEMPLATES.daily}
        onChange={(v) => update("rename_daily_template", v)}
      />
      <RenameTemplateField
        label="Anime episode"
        value={s.rename_anime_template || ""}
        defaultValue={DEFAULT_TEMPLATES.anime}
        onChange={(v) => update("rename_anime_template", v)}
      />

      <Divider />
      <SubHeader>Folders</SubHeader>
      <RenameTemplateField
        label="Series folder"
        hint="Stored for reference; folder renames are not yet applied automatically."
        value={s.rename_series_folder_template || ""}
        defaultValue={DEFAULT_TEMPLATES.seriesFolder}
        onChange={(v) => update("rename_series_folder_template", v)}
      />
      <RenameTemplateField
        label="Season folder"
        hint="Stored for reference; folder renames are not yet applied automatically."
        value={s.rename_season_folder_template || ""}
        defaultValue={DEFAULT_TEMPLATES.seasonFolder}
        onChange={(v) => update("rename_season_folder_template", v)}
      />

      <Divider />
      <SubHeader>Movies</SubHeader>
      <RenameTemplateField
        label="Movie"
        value={s.rename_movie_template || ""}
        defaultValue={DEFAULT_TEMPLATES.movie}
        onChange={(v) => update("rename_movie_template", v)}
      />
      <RenameTemplateField
        label="Movie folder"
        hint="Stored for reference; folder renames are not yet applied automatically."
        value={s.rename_movie_folder_template || ""}
        defaultValue={DEFAULT_TEMPLATES.movieFolder}
        onChange={(v) => update("rename_movie_folder_template", v)}
      />

      <div className="ml-64 pl-3 mb-3 mt-2">
        <button
          type="button"
          className="text-xs px-2 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded"
          onClick={() =>
            setS({
              ...s,
              rename_episode_template: DEFAULT_TEMPLATES.standard,
              rename_daily_template: DEFAULT_TEMPLATES.daily,
              rename_anime_template: DEFAULT_TEMPLATES.anime,
              rename_series_folder_template: DEFAULT_TEMPLATES.seriesFolder,
              rename_season_folder_template: DEFAULT_TEMPLATES.seasonFolder,
              rename_movie_template: DEFAULT_TEMPLATES.movie,
              rename_movie_folder_template: DEFAULT_TEMPLATES.movieFolder,
            })
          }
        >
          Reset all to Sonarr/Radarr recommended defaults
        </button>
      </div>

      <details className="ml-64 pl-3 mb-3 text-xs text-slate-400 max-w-2xl">
        <summary className="cursor-pointer text-slate-300 mb-2">
          Token reference
        </summary>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1">
          <div><code className="text-slate-200">{"{Series TitleYear}"}</code> Severance (2022)</div>
          <div><code className="text-slate-200">{"{Episode CleanTitle}"}</code> the matched episode title</div>
          <div><code className="text-slate-200">{"{season:00}"}</code> / <code className="text-slate-200">{"{episode:00}"}</code> zero-padded</div>
          <div><code className="text-slate-200">{"{Air-Date}"}</code> 2024-05-08</div>
          <div><code className="text-slate-200">{"{Quality Full}"}</code> WEBDL-1080p / Bluray-2160p</div>
          <div><code className="text-slate-200">{"{MediaInfo VideoCodec}"}</code> x264 / x265 / AV1</div>
          <div><code className="text-slate-200">{"{MediaInfo VideoBitDepth}"}</code> 8 / 10</div>
          <div><code className="text-slate-200">{"{MediaInfo VideoDynamicRangeType}"}</code> HDR10 / DV / HLG</div>
          <div><code className="text-slate-200">{"{MediaInfo AudioCodec}"}</code> EAC3 Atmos / DTS-HD MA</div>
          <div><code className="text-slate-200">{"{MediaInfo AudioChannels}"}</code> 5.1 / 7.1 / 2.0</div>
          <div><code className="text-slate-200">{"{MediaInfo AudioLanguages}"}</code> [EN] / [EN+JA]</div>
          <div><code className="text-slate-200">{"{Release Group}"}</code> / <code className="text-slate-200">{"{-Release Group}"}</code></div>
          <div><code className="text-slate-200">{"{TvdbId}"}</code>, <code className="text-slate-200">{"{TmdbId}"}</code>, <code className="text-slate-200">{"{ImdbId}"}</code></div>
          <div><code className="text-slate-200">{"{Movie CleanTitle}"}</code>, <code className="text-slate-200">{"{(Release Year)}"}</code></div>
          <div><code className="text-slate-200">{"{[Token]}"}</code> wraps in [..] when present, drops otherwise</div>
        </div>
        <p className="mt-2 text-slate-500">
          Old v0.10.0 simple tokens (<code>{"{title}"}</code>, <code>{"{year}"}</code>,{" "}
          <code>{"{episode_title}"}</code>, <code>{"{ext}"}</code>,{" "}
          <code>{"{quality}"}</code>) still work as fallbacks.
        </p>
        <p className="mt-2 text-slate-500">
          See the{" "}
          <a
            href="https://trash-guides.info/Sonarr/Sonarr-recommended-naming-scheme/"
            target="_blank"
            rel="noreferrer"
            className="text-indigo-400 hover:underline"
          >
            Trash Guides naming scheme
          </a>{" "}
          for context on each token.
        </p>
      </details>
    </>
  );
}

function AboutPane() {
  const [info, setInfo] = useState<{ version: string; name: string; repo: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .version()
      .then(setInfo)
      .catch((e) => setErr(String(e?.message || e)));
  }, []);

  return (
    <>
      <PaneHeader title="About" subtitle="Build identity for the running container." />
      {err && <div className="text-xs text-rose-400">{err}</div>}
      {!info && !err && <div className="text-xs text-slate-500">Loading…</div>}
      {info && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-xl">
          <Card>
            <CardLabel>App</CardLabel>
            <div className="text-sm text-slate-100">{info.name}</div>
          </Card>
          <Card>
            <CardLabel>Backend version</CardLabel>
            <div className="text-sm font-mono text-slate-100">v{info.version}</div>
          </Card>
          <Card>
            <CardLabel>Repository</CardLabel>
            <a
              href={info.repo}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-indigo-400 hover:underline break-all"
            >
              {info.repo}
            </a>
          </Card>
          <Card>
            <CardLabel>Container</CardLabel>
            <div className="text-sm font-mono text-slate-100 break-all">
              ghcr.io/cooper8386/plex-nfo-builder:v{info.version}
            </div>
            <div className="text-[11px] text-slate-500 mt-1">
              The <code className="text-slate-300">:latest</code> tag tracks the
              newest release; the version chip in the top bar shows what's actually
              running.
            </div>
          </Card>
        </div>
      )}
    </>
  );
}

/* ----------------------- Atoms ----------------------- */

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-slate-800 bg-slate-900/40 px-3 py-2.5">
      {children}
    </div>
  );
}

function CardLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-0.5">
      {children}
    </div>
  );
}

function SubHeader({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2 mt-1">
      {children}
    </h3>
  );
}

function Divider() {
  return <hr className="my-5 border-slate-800" />;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 mb-3">
      <label className="text-sm text-slate-300 w-64">{label}</label>
      {children}
    </div>
  );
}

const DEFAULT_TEMPLATES = {
  standard:
    "{Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} " +
    "{[Custom Formats]}{[Quality Full]}{[MediaInfo VideoDynamicRangeType]}" +
    "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}" +
    "{[MediaInfo VideoCodec]}{-Release Group}",
  daily:
    "{Series TitleYear} - {Air-Date} - {Episode CleanTitle} " +
    "{[Custom Formats]}{[Quality Full]}{[MediaInfo VideoDynamicRangeType]}" +
    "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}" +
    "{[MediaInfo VideoCodec]}{-Release Group}",
  anime:
    "{Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} " +
    "{[Custom Formats]}{[Quality Full]}{[MediaInfo VideoDynamicRangeType]}" +
    "[{MediaInfo VideoBitDepth}bit]{[MediaInfo VideoCodec]}" +
    "[{Mediainfo AudioCodec} { Mediainfo AudioChannels}]" +
    "{MediaInfo AudioLanguages}{-Release Group}",
  seriesFolder: "{Series TitleYear} {tvdb-{TvdbId}}",
  seasonFolder: "Season {season:00}",
  movie:
    "{Movie CleanTitle} {(Release Year)} {tmdb-{TmdbId}} {edition-{Edition Tags}} " +
    "{[Custom Formats]}{[Quality Full]}{[MediaInfo 3D]}{[MediaInfo VideoDynamicRangeType]}" +
    "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}" +
    "{[Mediainfo VideoCodec]}{-Release Group}",
  movieFolder: "{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}",
} as const;

function RenameTemplateField({
  label,
  value,
  defaultValue,
  onChange,
  hint,
}: {
  label: string;
  value: string;
  defaultValue: string;
  onChange: (v: string) => void;
  hint?: string;
}) {
  return (
    <div className="flex items-start gap-3 mb-3">
      <label className="text-sm text-slate-300 w-64 mt-1">{label}</label>
      <div className="flex-1 max-w-2xl">
        <textarea
          className="bg-slate-800 px-2 py-1 rounded w-full font-mono text-xs leading-relaxed"
          rows={2}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={defaultValue}
          spellCheck={false}
        />
        <div className="flex items-center gap-2 mt-1">
          <button
            type="button"
            className="text-[11px] text-indigo-400 hover:underline"
            onClick={() => onChange(defaultValue)}
            title={defaultValue}
          >
            Reset to default
          </button>
          {hint && <span className="text-[11px] text-slate-500">{hint}</span>}
        </div>
      </div>
    </div>
  );
}

const ACTION_LABELS: Record<ScheduleAction, string> = {
  scan_only: "Scan only",
  match_only: "Match only",
  build_only: "Build only",
  match_and_build: "Match + Build",
  full: "Full (scan + match + build)",
};

const CRON_PRESETS: { label: string; cron: string }[] = [
  { label: "Daily 3am UTC", cron: "0 3 * * *" },
  { label: "Sunday 3am UTC", cron: "0 3 * * 0" },
  { label: "Every 6 hours", cron: "0 */6 * * *" },
  { label: "Hourly", cron: "0 * * * *" },
];

function fmtTimestamp(ts: number | null): string {
  if (!ts) return "never";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

function SchedulesSection() {
  const confirmDlg = useConfirm();
  const [items, setItems] = useState<Schedule[] | null>(null);
  const [libs, setLibs] = useState<Library[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<{
    library: string;
    cron: string;
    action: ScheduleAction;
    enabled: boolean;
  }>({ library: "", cron: "0 3 * * *", action: "match_and_build", enabled: true });

  const reload = async () => {
    try {
      const [s, l] = await Promise.all([api.schedules.list(), api.libraries.list()]);
      setItems(s.schedules);
      setLibs(l.libraries);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    }
  };

  useEffect(() => {
    reload();
  }, []);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.schedules.create({
        library: draft.library || null,
        cron: draft.cron.trim(),
        action: draft.action,
        enabled: draft.enabled,
      });
      setDraft({ ...draft, library: "", cron: "0 3 * * *" });
      await reload();
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const update = async (id: number, body: Parameters<typeof api.schedules.update>[1]) => {
    setBusy(true);
    setError(null);
    try {
      await api.schedules.update(id, body);
      await reload();
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    const ok = await confirmDlg({
      title: "Delete this schedule?",
      message: "The recurring run is removed immediately. You can recreate it later from this same panel.",
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.schedules.remove(id);
      await reload();
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const runNow = async (id: number) => {
    setBusy(true);
    try {
      await api.schedules.run(id);
      setTimeout(reload, 1500);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PaneHeader
        title="Schedules"
        subtitle="Periodically scan, auto-match, and build NFOs for new or changed items. Cron expressions are evaluated in UTC. A schedule with no library applies to every enabled library."
      />

      <div className="bg-slate-900/60 border border-slate-800 rounded-md p-3 mb-4">
        <div className="text-xs uppercase tracking-wide text-slate-500 mb-2">
          New schedule
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Library
            <select
              className="bg-slate-800 px-2 py-1 rounded text-sm text-slate-100"
              value={draft.library}
              onChange={(e) => setDraft({ ...draft, library: e.target.value })}
            >
              <option value="">All libraries</option>
              {libs.map((l) => (
                <option key={l.name} value={l.name}>
                  {l.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Action
            <select
              className="bg-slate-800 px-2 py-1 rounded text-sm text-slate-100"
              value={draft.action}
              onChange={(e) =>
                setDraft({ ...draft, action: e.target.value as ScheduleAction })
              }
            >
              {Object.entries(ACTION_LABELS).map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400 sm:col-span-2">
            Cron (UTC, 5 fields)
            <input
              className="bg-slate-800 px-2 py-1 rounded text-sm font-mono text-slate-100"
              value={draft.cron}
              onChange={(e) => setDraft({ ...draft, cron: e.target.value })}
              placeholder="0 3 * * *"
            />
            <div className="flex flex-wrap gap-1.5 mt-1">
              {CRON_PRESETS.map((p) => (
                <button
                  key={p.cron}
                  type="button"
                  onClick={() => setDraft({ ...draft, cron: p.cron })}
                  className="text-[11px] px-2 py-0.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </label>
        </div>
        <div className="flex items-center gap-3 mt-3">
          <label className="text-xs text-slate-300 inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
              className="accent-indigo-500"
            />
            Enabled
          </label>
          <button
            onClick={create}
            disabled={busy || !draft.cron.trim()}
            className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 rounded text-xs disabled:opacity-50"
          >
            Add schedule
          </button>
          {error && <span className="text-xs text-rose-400">{error}</span>}
        </div>
      </div>

      {items === null ? (
        <div className="text-xs text-slate-500">Loading schedules…</div>
      ) : items.length === 0 ? (
        <div className="text-xs text-slate-500">No schedules configured.</div>
      ) : (
        <div className="space-y-2">
          {items.map((sch) => (
            <ScheduleRow
              key={sch.id}
              libs={libs}
              sch={sch}
              busy={busy}
              onUpdate={(body) => update(sch.id, body)}
              onRemove={() => remove(sch.id)}
              onRun={() => runNow(sch.id)}
            />
          ))}
        </div>
      )}
    </>
  );
}

function ScheduleRow({
  libs,
  sch,
  busy,
  onUpdate,
  onRemove,
  onRun,
}: {
  libs: Library[];
  sch: Schedule;
  busy: boolean;
  onUpdate: (body: { library?: string | null; cron?: string; action?: ScheduleAction; enabled?: boolean }) => void;
  onRemove: () => void;
  onRun: () => void;
}) {
  const [cron, setCron] = useState(sch.cron);
  const dirty = cron !== sch.cron;

  const statusBadge = useMemo(() => {
    const status = sch.last_status;
    if (status === "running") {
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-700 text-amber-100 uppercase tracking-wide">
          running
        </span>
      );
    }
    if (status === "ok") {
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-700 text-emerald-100 uppercase tracking-wide">
          ok
        </span>
      );
    }
    if (status === "error") {
      return (
        <span
          className="text-[10px] px-1.5 py-0.5 rounded bg-rose-700 text-rose-100 uppercase tracking-wide"
          title={sch.last_message ?? undefined}
        >
          error
        </span>
      );
    }
    return null;
  }, [sch.last_status, sch.last_message]);

  return (
    <div className="bg-slate-900/40 border border-slate-800 rounded-md p-3">
      <div className="flex flex-wrap items-center gap-3 mb-2">
        <span className="text-xs uppercase text-slate-500">#{sch.id}</span>
        <select
          className="bg-slate-800 px-2 py-0.5 rounded text-xs text-slate-100"
          value={sch.library ?? ""}
          onChange={(e) => onUpdate({ library: e.target.value || null })}
          disabled={busy}
        >
          <option value="">All libraries</option>
          {libs.map((l) => (
            <option key={l.name} value={l.name}>
              {l.name}
            </option>
          ))}
        </select>
        <select
          className="bg-slate-800 px-2 py-0.5 rounded text-xs text-slate-100"
          value={sch.action}
          onChange={(e) => onUpdate({ action: e.target.value as ScheduleAction })}
          disabled={busy}
        >
          {Object.entries(ACTION_LABELS).map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
        <label className="text-xs text-slate-300 inline-flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={!!sch.enabled}
            onChange={(e) => onUpdate({ enabled: e.target.checked })}
            disabled={busy}
            className="accent-indigo-500"
          />
          Enabled
        </label>
        {statusBadge}
        <span className="text-[11px] text-slate-500">
          last run: {fmtTimestamp(sch.last_run)}
        </span>
        <div className="flex-1" />
        <button
          onClick={onRun}
          disabled={busy}
          className="text-xs px-2 py-0.5 bg-indigo-700 hover:bg-indigo-600 rounded disabled:opacity-50"
        >
          Run now
        </button>
        <button
          onClick={onRemove}
          disabled={busy}
          className="text-xs px-2 py-0.5 bg-rose-900/40 hover:bg-rose-900/70 border border-rose-800 text-rose-200 rounded disabled:opacity-50"
        >
          Delete
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          className="bg-slate-800 px-2 py-1 rounded text-xs font-mono text-slate-100 w-44"
          value={cron}
          onChange={(e) => setCron(e.target.value)}
          disabled={busy}
        />
        <button
          onClick={() => onUpdate({ cron: cron.trim() })}
          disabled={busy || !dirty}
          className="text-xs px-2 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded disabled:opacity-30"
        >
          Save cron
        </button>
        {sch.last_message && sch.last_status === "error" && (
          <span className="text-[11px] text-rose-300 truncate" title={sch.last_message}>
            {sch.last_message}
          </span>
        )}
      </div>
    </div>
  );
}
