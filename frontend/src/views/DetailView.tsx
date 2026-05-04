import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import ArtworkPicker from "./ArtworkPicker";
import EpisodeMapper from "./EpisodeMapper";
import OverridesTab from "./OverridesTab";

type Tab = "overview" | "artwork" | "episodes" | "overrides";

export default function DetailView({ path, onBack }: { path: string; onBack: () => void }) {
  const qc = useQueryClient();
  const detail = useQuery({ queryKey: ["detail", path], queryFn: () => api.items.detail(path) });
  const [searchQuery, setSearchQuery] = useState("");
  const [matchKind, setMatchKind] = useState<"series" | "movie">("series");
  const [matchProvider, setMatchProvider] = useState<"tvdb" | "tmdb">("tvdb");
  const [matches, setMatches] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [plexConfigured, setPlexConfigured] = useState(false);
  useEffect(() => {
    api.health().then((h: any) => setPlexConfigured(!!h.plex_configured)).catch(() => {});
  }, []);

  if (!detail.data) return <div className="p-6 text-slate-500">Loading…</div>;
  const { state, binding, artwork_files, provider_episode_count, provider_used } = detail.data as any;
  const kind: "series" | "movie" = state?.kind === "movie" ? "movie" : "series";
  const providerLabel = (provider_used ?? binding?.provider ?? "tvdb").toUpperCase();
  const cacheBust = state?.last_built ?? 0;
  const filesByName: Record<string, string> = {};
  const seasonPosters: { season: string; path: string }[] = [];
  for (const f of (artwork_files ?? []) as string[]) {
    const name = f.split("/").pop() ?? f;
    filesByName[name] = f;
    const m = name.match(/^Season(\d+)-poster\.jpg$/i);
    if (m) seasonPosters.push({ season: m[1], path: f });
  }
  seasonPosters.sort((a, b) => Number(a.season) - Number(b.season));
  const slot = (name: string) => filesByName[name];
  const fileSrc = (p: string) => `${api.artwork.fileUrl(p)}&t=${cacheBust}`;

  return (
    <div className="p-6">
      <button
        onClick={onBack}
        className="text-indigo-400 text-sm mb-4 inline-flex items-center gap-1 hover:text-indigo-300"
      >
        ← back
      </button>

      <div className="flex flex-wrap items-baseline gap-3 mb-1">
        <h2 className="text-2xl font-semibold tracking-tight">{state?.title ?? path}</h2>
        {state?.year && <span className="text-slate-500">({state.year})</span>}
        {state?.nfo_status && (
          <span
            className={`text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wide ${
              state.nfo_status === "complete"
                ? "bg-emerald-700 text-emerald-100"
                : state.nfo_status === "partial" || state.nfo_status === "stale"
                ? "bg-amber-700 text-amber-100"
                : "bg-slate-700 text-slate-200"
            }`}
          >
            {state.nfo_status}
          </span>
        )}
      </div>
      <div className="text-xs text-slate-500 mb-4 font-mono break-all">{path}</div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-5">
        <Stat label="Episodes (local)" value={state?.episode_count_local ?? "—"} />
        <Stat
          label={kind === "series" ? `Episodes (${providerLabel})` : "Episodes (matched)"}
          value={
            kind === "series"
              ? binding
                ? provider_episode_count ?? "—"
                : "—"
              : "—"
          }
        />
        <Stat
          label="Binding"
          value={binding ? `${binding.provider}-${binding.external_id}` : "unmatched"}
        />
      </div>

      <div className="flex flex-wrap gap-2 mb-6">
        <button
          disabled={busy}
          title="Generate NFO files and download artwork. Uses cached metadata when available."
          className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded text-sm disabled:opacity-50"
          onClick={async () => {
            setBusy(true);
            setMsg("Build started…");
            await api.build(path, kind, false);
            setMsg("Build queued. Check Jobs view for progress.");
            setBusy(false);
            setTimeout(() => qc.invalidateQueries({ queryKey: ["detail", path] }), 2000);
          }}
        >
          Build NFOs
        </button>
        <button
          disabled={busy}
          title="Same as Build NFOs but bypasses the local metadata cache and re-fetches everything from TVDB/TMDB. Use this when upstream data was updated and you want the freshest copy."
          className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-sm disabled:opacity-50"
          onClick={async () => {
            setBusy(true);
            setMsg("Force rebuild…");
            await api.build(path, kind, true);
            setBusy(false);
            setMsg("Force rebuild queued.");
          }}
        >
          Force rebuild
        </button>
        <button
          disabled={busy}
          title="Delete every NFO and artwork file generated by the app, leaving season folders and media files alone. Useful when you want to start fresh."
          className="px-3 py-1.5 bg-amber-900/40 hover:bg-amber-900/70 border border-amber-800 rounded text-sm text-amber-200 disabled:opacity-50"
          onClick={async () => {
            try {
              setBusy(true);
              setMsg("Listing files to remove…");
              const preview = await api.items.clean({ folder_path: path, dry_run: true });
              const files = preview.files ?? [];
              if (files.length === 0) {
                setMsg("Nothing to clean — no NFOs or artwork found.");
                setBusy(false);
                return;
              }
              const head = files.slice(0, 12).join("\n  • ");
              const more = files.length > 12 ? `\n  … and ${files.length - 12} more` : "";
              const ok = window.confirm(
                `Wipe NFOs & artwork for "${state?.title ?? path}"?\n\n` +
                  `${files.length} file${files.length === 1 ? "" : "s"} will be deleted from disk:\n  • ${head}${more}\n\n` +
                  `Season folders and media files (.mkv/.mp4/etc.) are NOT touched. ` +
                  `The .plex-nfo-builder.json sidecar is preserved so your binding and overrides stay intact.`
              );
              if (!ok) {
                setMsg("Cancelled.");
                setBusy(false);
                return;
              }
              setMsg("Cleaning…");
              const res = await api.items.clean({ folder_path: path, dry_run: false });
              setMsg(
                `Cleaned: ${res.nfo_deleted ?? 0} NFO file${res.nfo_deleted === 1 ? "" : "s"}, ` +
                  `${res.artwork_deleted ?? 0} artwork file${res.artwork_deleted === 1 ? "" : "s"} removed.`
              );
              await qc.invalidateQueries({ queryKey: ["detail", path] });
              await qc.invalidateQueries({ queryKey: ["items"] });
            } catch (e: any) {
              setMsg(`Failed: ${e?.message ?? e}`);
            } finally {
              setBusy(false);
            }
          }}
        >
          Wipe NFOs & artwork
        </button>
        {plexConfigured && (
          <button
            disabled={busy}
            title="Ask your Plex server to rescan this folder right now."
            className="px-3 py-1.5 bg-emerald-900/40 hover:bg-emerald-900/70 border border-emerald-800 rounded text-sm text-emerald-200 disabled:opacity-50"
            onClick={async () => {
              setBusy(true);
              setMsg("Asking Plex to refresh…");
              try {
                const r = await api.plex.refresh(path, 0);
                if (r.refreshed && r.strategy === "metadata-refresh") {
                  setMsg(
                    `Plex re-reading metadata for \"${r.item_title || r.section_title}\" (ratingKey ${r.rating_key}). Updated NFO and artwork should appear in a moment.`,
                  );
                } else if (r.refreshed) {
                  setMsg(
                    `Plex partial scan queued for \"${r.section_title}\" but no item matched ${r.translated_path ?? path}. ${r.error || "Plex hasn't indexed this folder yet \u2014 wait for the scan to finish, then click Refresh in Plex again to force the NFO re-read."}`,
                  );
                } else {
                  setMsg(`Plex refresh failed: ${r.error || "unknown error"}`);
                }
              } catch (e: any) {
                setMsg(`Plex refresh failed: ${e?.message ?? e}`);
              } finally {
                setBusy(false);
              }
            }}
          >
            Refresh in Plex
          </button>
        )}
        <div className="flex-1" />
        <button
          disabled={busy}
          className="px-3 py-1.5 bg-rose-900/40 hover:bg-rose-900/70 border border-rose-800 rounded text-sm text-rose-200 disabled:opacity-50"
          title="Remove this item from the library database. Files on disk are not touched."
          onClick={async () => {
            const ok = window.confirm(
              `Remove "${state?.title ?? path}" from the library?\n\nThis only forgets it in the database — no files are deleted. Use this when you've already deleted the folder on disk.`
            );
            if (!ok) return;
            setBusy(true);
            setMsg("Removing from library…");
            try {
              await api.items.remove(path);
              setMsg("Removed. Returning to library.");
              await qc.invalidateQueries({ queryKey: ["items"] });
              setTimeout(() => onBack(), 600);
            } catch (e: any) {
              setMsg(`Failed: ${e?.message ?? e}`);
            } finally {
              setBusy(false);
            }
          }}
        >
          Remove from library
        </button>
      </div>
      {msg && <div className="text-xs text-slate-400 mb-3">{msg}</div>}

      <div className="border-b border-slate-800 mb-4 flex gap-1">
        {(["overview", "artwork", ...(kind === "series" ? (["episodes"] as Tab[]) : []), "overrides"] as Tab[]).map(
          (t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm capitalize border-b-2 transition -mb-px ${
                tab === t
                  ? "border-indigo-500 text-white"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              {t}
            </button>
          ),
        )}
      </div>

      {tab === "overview" && (
        <div>
          <h3 className="font-semibold mt-2 mb-2">Manual match</h3>
          <div className="flex flex-wrap gap-2 mb-2">
            <select
              value={matchProvider}
              onChange={(e) => setMatchProvider(e.target.value as any)}
              className="bg-slate-800 px-2 py-1 rounded text-sm border border-slate-700"
              title="Metadata provider to search"
            >
              <option value="tvdb">TheTVDB</option>
              <option value="tmdb">TMDB</option>
            </select>
            <select
              value={matchKind}
              onChange={(e) => setMatchKind(e.target.value as any)}
              className="bg-slate-800 px-2 py-1 rounded text-sm border border-slate-700"
            >
              <option value="series">Series</option>
              <option value="movie">Movie</option>
            </select>
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={`${matchProvider === "tmdb" ? "TMDB" : "TVDB"} title`}
              className="bg-slate-800 px-2 py-1 rounded text-sm flex-1 min-w-[12rem] border border-slate-700"
              onKeyDown={async (e) => {
                if (e.key === "Enter") {
                  const r = await api.match.search(searchQuery, matchKind, undefined, undefined, matchProvider);
                  setMatches(r.results);
                }
              }}
            />
            <button
              className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded text-sm"
              onClick={async () => {
                const r = await api.match.search(searchQuery, matchKind, undefined, undefined, matchProvider);
                setMatches(r.results);
              }}
            >
              Search
            </button>
          </div>
          <div className="max-h-72 overflow-auto border border-slate-800 rounded">
            {matches.length === 0 && (
              <div className="p-4 text-xs text-slate-500">No results yet.</div>
            )}
            {matches.map((m) => {
              const provider = (m.provider as "tvdb" | "tmdb") || matchProvider;
              const externalId = String(m.tvdb_id || m.id);
              return (
                <div
                  key={`${provider}-${externalId}`}
                  className="p-2 flex items-center gap-3 border-b border-slate-800 last:border-0"
                >
                  {m.image_url && (
                    <img src={m.image_url} className="w-10 h-14 object-cover rounded" />
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate">
                      {m.name} {m.year ? `(${m.year})` : ""}
                    </div>
                    <div className="text-xs text-slate-500 truncate">
                      <span className={`mr-1 px-1 rounded ${provider === "tmdb" ? "bg-emerald-800/60 text-emerald-100" : "bg-blue-800/60 text-blue-100"}`}>
                        {provider}
                      </span>
                      {provider}-{externalId}
                    </div>
                  </div>
                  <button
                    className="px-2 py-1 bg-indigo-600 hover:bg-indigo-500 rounded text-xs"
                    onClick={async () => {
                      await api.match.bind({
                        folder_path: path,
                        kind: matchKind,
                        provider,
                        external_id: externalId,
                        title: m.name,
                        year: m.year,
                      });
                      qc.invalidateQueries({ queryKey: ["detail", path] });
                    }}
                  >
                    Bind
                  </button>
                </div>
              );
            })}
          </div>

          <h3 className="font-semibold mt-6 mb-2">Current artwork</h3>
          <p className="text-xs text-slate-500 mb-3">
            The active local files Plex reads from the folder. Rebuilds overwrite these in place.
          </p>
          {artwork_files && artwork_files.length > 0 ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <ArtSlot
                  label="Poster"
                  filename="poster.jpg"
                  src={slot("poster.jpg") ? fileSrc(slot("poster.jpg")!) : null}
                  aspect="aspect-[2/3]"
                />
                <ArtSlot
                  label="Background"
                  filename="background.jpg"
                  src={slot("background.jpg") ? fileSrc(slot("background.jpg")!) : null}
                  aspect="aspect-[16/9]"
                />
                <ArtSlot
                  label="Banner"
                  filename="banner.jpg"
                  src={slot("banner.jpg") ? fileSrc(slot("banner.jpg")!) : null}
                  aspect="aspect-[758/140]"
                />
                <ArtSlot
                  label="Clearlogo"
                  filename="clearlogo.png"
                  src={slot("clearlogo.png") ? fileSrc(slot("clearlogo.png")!) : null}
                  aspect="aspect-[16/9]"
                  contain
                />
              </div>
              {seasonPosters.length > 0 && (
                <div>
                  <div className="text-xs uppercase tracking-wide text-slate-500 mb-2">
                    Season posters
                  </div>
                  <div className="grid grid-cols-3 sm:grid-cols-6 md:grid-cols-8 gap-2">
                    {seasonPosters.map((sp) => (
                      <ArtSlot
                        key={sp.path}
                        label={`Season ${Number(sp.season)}`}
                        filename={sp.path.split("/").pop() ?? ""}
                        src={fileSrc(sp.path)}
                        aspect="aspect-[2/3]"
                        compact
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="text-xs text-slate-500">
              No artwork downloaded yet. Run a build.
            </div>
          )}
        </div>
      )}

      {tab === "artwork" && (
        <div>
          {!binding ? (
            <div className="text-sm text-slate-400">
              Bind this folder to a TVDB or TMDB title from the Overview tab to pick artwork.
            </div>
          ) : (
            <ArtworkPicker path={path} kind={kind} />
          )}
        </div>
      )}

      {tab === "episodes" && kind === "series" && (
        <div>
          {!binding ? (
            <div className="text-sm text-slate-400">
              Bind this folder to a TVDB series from the Overview tab first.
            </div>
          ) : (
            <EpisodeMapper path={path} />
          )}
        </div>
      )}

      {tab === "overrides" && (
        <OverridesTab path={path} kind={kind} binding={binding} />
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: any }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded px-3 py-2">
      <div className="text-[10px] uppercase text-slate-500 tracking-wide">{label}</div>
      <div className="text-base mt-0.5 truncate">{value}</div>
    </div>
  );
}

function ArtSlot({
  label,
  filename,
  src,
  aspect,
  contain,
  compact,
}: {
  label: string;
  filename: string;
  src: string | null;
  aspect: string;
  contain?: boolean;
  compact?: boolean;
}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded overflow-hidden flex flex-col">
      <div
        className={`${aspect} bg-slate-950 flex items-center justify-center text-slate-600`}
        style={contain ? { backgroundImage: "linear-gradient(45deg, #0f172a 25%, #111827 25%, #111827 50%, #0f172a 50%, #0f172a 75%, #111827 75%)", backgroundSize: "16px 16px" } : undefined}
      >
        {src ? (
          <img
            src={src}
            alt={label}
            className={`w-full h-full ${contain ? "object-contain p-2" : "object-cover"}`}
          />
        ) : (
          <span className="text-[10px] uppercase tracking-wide">missing</span>
        )}
      </div>
      <div className={`px-2 py-1 ${compact ? "" : "border-t border-slate-800"}`}>
        <div className={`${compact ? "text-[10px]" : "text-xs"} font-medium text-slate-200 truncate`}>
          {label}
        </div>
        {!compact && (
          <div className="text-[10px] text-slate-500 font-mono truncate">{filename}</div>
        )}
      </div>
    </div>
  );
}
