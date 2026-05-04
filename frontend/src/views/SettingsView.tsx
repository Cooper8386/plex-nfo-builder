import { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function SettingsView() {
  const [s, setS] = useState<any>(null);
  const [apiKey, setApiKey] = useState("");
  const [pin, setPin] = useState("");
  const [tmdbKey, setTmdbKey] = useState("");
  const [fanartKey, setFanartKey] = useState("");
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
          if (apiKey) body.tvdb_api_key = apiKey;
          if (pin) body.tvdb_pin = pin;
          if (tmdbKey) body.tmdb_api_key = tmdbKey;
          if (fanartKey) body.fanart_api_key = fanartKey;
          await api.settings.set(body);
          // Re-fetch to refresh masked status
          const fresh = await api.settings.get();
          setS(fresh);
          setApiKey(""); setPin(""); setTmdbKey(""); setFanartKey("");
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
