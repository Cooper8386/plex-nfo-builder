import { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function SettingsView() {
  const [s, setS] = useState<any>(null);
  const [apiKey, setApiKey] = useState("");
  const [pin, setPin] = useState("");
  const [tmdbKey, setTmdbKey] = useState("");
  const [fanartKey, setFanartKey] = useState("");
  const [plexToken, setPlexToken] = useState("");
  const [plexTesting, setPlexTesting] = useState(false);
  const [plexResult, setPlexResult] = useState<
    | null
    | {
        ok: boolean;
        error?: string;
        identity?: { friendly_name?: string; version?: string };
        sections?: { id: string; title: string; type: string; locations: string[] }[];
      }
  >(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    api.settings.get().then(setS);
  }, []);

  if (!s) return <div className="p-6 text-slate-500">Loading…</div>;

  const update = (k: string, v: any) => setS({ ...s, [k]: v });

  return (
    <div className="p-6 max-w-2xl">
      <h2 className="text-xl font-semibold mb-4">Settings</h2>

      <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-2">Metadata</h3>
      <Field label="Primary metadata source">
        <select
          className="bg-slate-800 px-2 py-1 rounded"
          value={s.metadata_source || "tvdb"}
          onChange={(e) => update("metadata_source", e.target.value)}
        >
          <option value="tvdb">TheTVDB</option>
          <option value="tmdb">TheMovieDB (TMDB)</option>
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
          onChange={(e) => update("fallback_languages", e.target.value.split(",").map((x) => x.trim()))}
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

      <hr className="my-4 border-slate-800" />
      <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-2">TVDB</h3>
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

      <hr className="my-4 border-slate-800" />
      <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-2">TMDB</h3>
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

      <hr className="my-4 border-slate-800" />
      <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-2">Artwork</h3>
      <Field label="Preferred artwork source">
        <select
          className="bg-slate-800 px-2 py-1 rounded"
          value={s.preferred_artwork_source || "auto"}
          onChange={(e) => update("preferred_artwork_source", e.target.value)}
          title="Which provider's images win during a build. Independent of the metadata source — e.g. use TVDB for descriptions/cast and TMDB for artwork."
        >
          <option value="auto">Auto (match metadata source)</option>
          <option value="tvdb">Prefer TheTVDB artwork</option>
          <option value="tmdb">Prefer TheMovieDB artwork</option>
        </select>
      </Field>
      <div className="text-xs text-slate-500 mb-3 ml-64 pl-3">
        Applies to posters, backgrounds, and season posters. Your per-show
        manual picks always override this. When the preferred provider can't
        be reached for a show, the metadata source's own artwork is used.
      </div>

      <hr className="my-4 border-slate-800" />
      <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-2">Plex</h3>
      <div className="text-xs text-slate-500 mb-3">
        After a successful build, the app can ask your Plex server to rescan
        the show or movie folder so changes appear without manually clicking
        refresh in Plex. Path mappings translate the app's view of the disk
        (e.g. <code className="text-slate-400">/media</code>) to whatever Plex
        sees (e.g. <code className="text-slate-400">/data</code>).
      </div>
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
          className="px-3 py-1 bg-slate-700 rounded text-sm disabled:opacity-50"
          disabled={plexTesting}
          onClick={async () => {
            setPlexTesting(true);
            setPlexResult(null);
            try {
              // Save first if user typed a token or changed url so test uses live values.
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
              setPlexResult(r);
            } catch (e: any) {
              setPlexResult({ ok: false, error: String(e?.message || e) });
            } finally {
              setPlexTesting(false);
            }
          }}
        >
          {plexTesting ? "Testing…" : "Test connection"}
        </button>
      </div>
      {plexResult && (
        <div className="ml-64 pl-3 mb-4 text-xs">
          {plexResult.ok ? (
            <div>
              <div className="text-emerald-400">
                Connected to {plexResult.identity?.friendly_name || "Plex"}
                {plexResult.identity?.version ? ` (v${plexResult.identity.version})` : ""}
              </div>
              {plexResult.sections && plexResult.sections.length > 0 ? (
                <div className="mt-2 text-slate-400">
                  <div className="mb-1">Sections:</div>
                  <ul className="list-disc ml-5 space-y-1">
                    {plexResult.sections.map((sec) => (
                      <li key={sec.id}>
                        <span className="text-slate-200">{sec.title}</span>{" "}
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
                <div className="text-slate-500 mt-1">No sections found.</div>
              )}
            </div>
          ) : (
            <div className="text-rose-400">{plexResult.error || "Connection failed."}</div>
          )}
        </div>
      )}

      <hr className="my-4 border-slate-800" />
      <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-2">fanart.tv</h3>
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

      <hr className="my-4 border-slate-800" />
      <button
        className="px-4 py-1 bg-indigo-600 rounded text-sm"
        onClick={async () => {
          const body: any = { ...s };
          // Strip masked confirmation booleans
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
          // Re-fetch to refresh masked status
          const fresh = await api.settings.get();
          setS(fresh);
          setApiKey(""); setPin(""); setTmdbKey(""); setFanartKey(""); setPlexToken("");
          setSaved("Saved.");
          setTimeout(() => setSaved(null), 1500);
        }}
      >
        Save
      </button>
      {saved && <span className="ml-3 text-emerald-400 text-xs">{saved}</span>}
      <hr className="my-4 border-slate-800" />
      <button
        className="text-xs text-amber-400"
        onClick={async () => {
          await api.tvdb.clearCache();
          setSaved("Cache cleared.");
          setTimeout(() => setSaved(null), 1500);
        }}
      >
        Clear metadata cache
      </button>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 mb-3">
      <label className="text-sm text-slate-300 w-64">{label}</label>
      {children}
    </div>
  );
}
