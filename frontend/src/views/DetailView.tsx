import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import ArtworkPicker from "./ArtworkPicker";
import EpisodeMapper from "./EpisodeMapper";
import OverridesTab from "./OverridesTab";

type Tab = "overview" | "artwork" | "episodes" | "overrides";

/** Build a public-facing URL for a TVDB/TMDB record so users can jump from
 * the detail header straight to the source page. Returns null when we don't
 * have enough info to build one. */
function providerPageUrl(
  provider: string | null | undefined,
  externalId: string | number | null | undefined,
  kind: "series" | "movie"
): string | null {
  if (!provider || externalId === null || externalId === undefined || externalId === "") {
    return null;
  }
  const id = String(externalId);
  const p = provider.toLowerCase();
  if (p === "tvdb") {
    return kind === "movie"
      ? `https://www.thetvdb.com/?tab=movie&id=${id}`
      : `https://www.thetvdb.com/?tab=series&id=${id}`;
  }
  if (p === "tmdb") {
    return kind === "movie"
      ? `https://www.themoviedb.org/movie/${id}`
      : `https://www.themoviedb.org/tv/${id}`;
  }
  return null;
}

export default function DetailView({ path, onBack }: { path: string; onBack: () => void }) {
  const qc = useQueryClient();
  const detail = useQuery({ queryKey: ["detail", path], queryFn: () => api.items.detail(path) });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [plexConfigured, setPlexConfigured] = useState(false);
  const [showMatcher, setShowMatcher] = useState(false);
  useEffect(() => {
    api.health().then((h: any) => setPlexConfigured(!!h.plex_configured)).catch(() => {});
  }, []);

  if (!detail.data) return <div className="p-6 text-slate-500">Loading…</div>;
  const { state, binding, artwork_files, provider_episode_count, provider_used, tags } =
    detail.data as any;
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

  const doBuild = async (force: boolean) => {
    setBusy(true);
    setMsg(force ? "Force rebuild…" : "Build started…");
    await api.build(path, kind, force);
    setMsg(force ? "Force rebuild queued." : "Build queued. Check Jobs view for progress.");
    setBusy(false);
    setTimeout(() => qc.invalidateQueries({ queryKey: ["detail", path] }), 2000);
  };

  const doWipe = async () => {
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
  };

  const doPlexRefresh = async () => {
    setBusy(true);
    setMsg("Asking Plex to refresh…");
    try {
      const r = await api.plex.refresh(path, 0);
      if (r.refreshed && r.strategy === "metadata-refresh") {
        setMsg(
          `Plex re-reading metadata for "${r.item_title || r.section_title}" (ratingKey ${r.rating_key}). Updated NFO and artwork should appear in a moment.`
        );
      } else if (r.refreshed) {
        setMsg(
          `Plex partial scan queued for "${r.section_title}" but no item matched ${r.translated_path ?? path}. ${r.error || "Plex hasn't indexed this folder yet — wait for the scan to finish, then click Refresh in Plex again to force the NFO re-read."}`
        );
      } else {
        setMsg(`Plex refresh failed: ${r.error || "unknown error"}`);
      }
    } catch (e: any) {
      setMsg(`Plex refresh failed: ${e?.message ?? e}`);
    } finally {
      setBusy(false);
    }
  };

  const doRemove = async () => {
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
  };

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
        {(() => {
          const url = providerPageUrl(
            (binding?.provider as string | undefined) ?? null,
            binding?.external_id ?? null,
            kind
          );
          if (!url) return null;
          const label = (binding?.provider ?? "").toLowerCase() === "tmdb" ? "TMDB" : "TVDB";
          return (
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="text-xs px-2 py-0.5 rounded border border-indigo-700 text-indigo-300 hover:bg-indigo-700/30"
              title={`Open on ${label}`}
            >
              {label} ↗
            </a>
          );
        })()}
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

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
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

      <TagsPanel
        path={path}
        tags={tags ?? { tvdb: [], tmdb: [], custom: [] }}
        bindingProvider={(binding?.provider as string | undefined) ?? null}
        onChanged={() => qc.invalidateQueries({ queryKey: ["detail", path] })}
      />

      {/* Action row — primary actions inline, everything secondary tucked in overflow menu */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button
          disabled={busy}
          title="Generate NFO files and download artwork. Uses cached metadata when available."
          className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded text-sm disabled:opacity-50"
          onClick={() => doBuild(false)}
        >
          Build NFOs
        </button>
        <button
          disabled={busy}
          title="Same as Build NFOs but bypasses the local metadata cache and re-fetches everything from TVDB/TMDB."
          className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-sm disabled:opacity-50"
          onClick={() => doBuild(true)}
        >
          Force rebuild
        </button>
        {binding && (
          <button
            disabled={busy}
            title="Change which TVDB/TMDB title this folder is bound to."
            className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded text-sm disabled:opacity-50"
            onClick={() => {
              setShowMatcher((v) => !v);
              setTab("overview");
            }}
          >
            {showMatcher ? "Hide match panel" : "Change match"}
          </button>
        )}
        <div className="flex-1" />
        <OverflowMenu
          disabled={busy}
          items={[
            { label: "Wipe NFOs & artwork", tone: "warn", onClick: doWipe },
            ...(plexConfigured
              ? [{ label: "Refresh in Plex", tone: "ok" as const, onClick: doPlexRefresh }]
              : []),
            { label: "Remove from library", tone: "danger", onClick: doRemove },
          ]}
        />
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
          {!binding ? (
            <BindEmptyState
              path={path}
              detectedKind={kind}
              onBound={() => qc.invalidateQueries({ queryKey: ["detail", path] })}
            />
          ) : showMatcher ? (
            <MatchPanel
              path={path}
              detectedKind={kind}
              onBound={() => {
                qc.invalidateQueries({ queryKey: ["detail", path] });
                setShowMatcher(false);
              }}
            />
          ) : null}

          {binding && (
            <SecondarySourcePanel
              path={path}
              kind={kind}
              primaryProvider={(binding.provider as "tvdb" | "tmdb") ?? "tvdb"}
              secondaryProvider={(binding.secondary_provider as string | null) ?? null}
              secondaryExternalId={(binding.secondary_external_id as string | null) ?? null}
              onChanged={() => qc.invalidateQueries({ queryKey: ["detail", path] })}
            />
          )}

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
              Bind this folder to a TVDB or TMDB series from the Overview tab first.
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

/** Compact "•••" menu for secondary destructive/utility actions. */
function OverflowMenu({
  disabled,
  items,
}: {
  disabled?: boolean;
  items: Array<{ label: string; tone?: "warn" | "danger" | "ok"; onClick: () => void }>;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  const toneClass = (t?: "warn" | "danger" | "ok") =>
    t === "danger"
      ? "text-rose-300 hover:bg-rose-900/30"
      : t === "warn"
      ? "text-amber-200 hover:bg-amber-900/30"
      : t === "ok"
      ? "text-emerald-200 hover:bg-emerald-900/30"
      : "text-slate-200 hover:bg-slate-800";
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        title="More actions"
        className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded text-sm disabled:opacity-50"
      >
        •••
      </button>
      {open && (
        <div className="absolute right-0 mt-1 z-20 min-w-[12rem] bg-slate-900 border border-slate-700 rounded-md shadow-xl py-1">
          {items.map((it, i) => (
            <button
              key={i}
              type="button"
              onClick={() => {
                setOpen(false);
                it.onClick();
              }}
              className={`w-full text-left px-3 py-1.5 text-sm ${toneClass(it.tone)}`}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Prominent empty state shown when a folder has no binding yet. */
function BindEmptyState({
  path,
  detectedKind,
  onBound,
}: {
  path: string;
  detectedKind: "series" | "movie";
  onBound: () => void;
}) {
  return (
    <div className="bg-indigo-950/30 border border-indigo-800/60 rounded-md p-4 mb-6">
      <div className="text-sm font-semibold text-indigo-100 mb-1">
        This folder isn't bound yet
      </div>
      <p className="text-xs text-indigo-200/80 mb-3">
        Bind it to a TVDB or TMDB title to download artwork, generate NFOs, and map episodes.
      </p>
      <MatchPanel path={path} detectedKind={detectedKind} onBound={onBound} initialOpen />
    </div>
  );
}

/** Search & bind UI. Used both as the empty-state body and the "Change match" panel. */
function MatchPanel({
  path,
  detectedKind,
  onBound,
  initialOpen,
}: {
  path: string;
  detectedKind: "series" | "movie";
  onBound: () => void;
  initialOpen?: boolean;
}) {
  const [matchKind, setMatchKind] = useState<"series" | "movie">(detectedKind);
  const [matchProvider, setMatchProvider] = useState<"tvdb" | "tmdb">("tvdb");
  const [searchQuery, setSearchQuery] = useState("");
  const [matches, setMatches] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);

  const runSearch = async () => {
    if (!searchQuery.trim()) return;
    setBusy(true);
    try {
      const r = await api.match.search(searchQuery, matchKind, undefined, undefined, matchProvider);
      setMatches(r.results);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={initialOpen ? "" : "bg-slate-900/40 border border-slate-800 rounded-md p-3 mb-6"}>
      {!initialOpen && <h3 className="font-semibold mb-2">Change match</h3>}
      <div className="flex flex-wrap gap-2 mb-2">
        <select
          value={matchProvider}
          onChange={(e) => setMatchProvider(e.target.value as any)}
          className="bg-slate-800 px-2 py-1 rounded text-sm border border-slate-700"
          title="Metadata provider to search"
        >
          <option value="tvdb">TVDB</option>
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
          onKeyDown={(e) => {
            if (e.key === "Enter") runSearch();
          }}
        />
        <button
          disabled={busy}
          className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded text-sm disabled:opacity-50"
          onClick={runSearch}
        >
          Search
        </button>
      </div>
      <div className="max-h-72 overflow-auto border border-slate-800 rounded">
        {matches.length === 0 && (
          <div className="p-4 text-xs text-slate-500">
            Type a title and press Enter to search.
          </div>
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
                  <span
                    className={`mr-1 px-1 rounded ${
                      provider === "tmdb"
                        ? "bg-emerald-800/60 text-emerald-100"
                        : "bg-blue-800/60 text-blue-100"
                    }`}
                  >
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
                  onBound();
                }}
              >
                Bind
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Lets the user pin a manual TMDB id on a TVDB-bound show (or vice versa)
 * when the primary metadata record doesn't include a cross-reference. The
 * stored secondary id is used by the cross-provider artwork resolver, the
 * fanart.tv lookup, and the NFO ``<uniqueid>`` block. Persists to the sidecar
 * so it survives a DB wipe.
 */
function SecondarySourcePanel({
  path,
  kind,
  primaryProvider,
  secondaryProvider,
  secondaryExternalId,
  onChanged,
}: {
  path: string;
  kind: "series" | "movie";
  primaryProvider: "tvdb" | "tmdb";
  secondaryProvider: string | null;
  secondaryExternalId: string | null;
  onChanged: () => void;
}) {
  const otherProvider: "tvdb" | "tmdb" = primaryProvider === "tvdb" ? "tmdb" : "tvdb";
  const otherLabel = otherProvider.toUpperCase();
  const hasSecondary =
    !!secondaryProvider && !!secondaryExternalId && secondaryProvider.toLowerCase() !== primaryProvider;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [pasteId, setPasteId] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [matches, setMatches] = useState<any[]>([]);

  const linkUrl = providerPageUrl(secondaryProvider, secondaryExternalId, kind);

  const save = async (provider: "tvdb" | "tmdb" | null, externalId: string | null) => {
    setBusy(true);
    setMsg(null);
    try {
      await api.match.setSecondary({
        folder_path: path,
        provider,
        external_id: externalId,
      });
      setOpen(false);
      setPasteId("");
      setSearchQuery("");
      setMatches([]);
      onChanged();
    } catch (e: any) {
      setMsg(`Failed: ${e?.message ?? e}`);
    } finally {
      setBusy(false);
    }
  };

  const savePaste = async () => {
    const id = pasteId.trim();
    if (!id) {
      setMsg(`Enter a ${otherLabel} id (numbers only).`);
      return;
    }
    if (!/^\d+$/.test(id)) {
      setMsg(`${otherLabel} id should be all numbers.`);
      return;
    }
    await save(otherProvider, id);
  };

  const runSearch = async () => {
    if (!searchQuery.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.match.search(
        searchQuery,
        kind,
        undefined,
        undefined,
        otherProvider,
      );
      setMatches(r.results || []);
    } catch (e: any) {
      setMsg(`Search failed: ${e?.message ?? e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-slate-900/40 border border-slate-800 rounded-md p-3 mb-6">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold mr-1">Secondary source</h3>
        {hasSecondary ? (
          <>
            <span className="text-xs px-2 py-0.5 rounded bg-slate-800 border border-slate-700 font-mono">
              {(secondaryProvider ?? "").toLowerCase()}-{secondaryExternalId}
            </span>
            {linkUrl && (
              <a
                href={linkUrl}
                target="_blank"
                rel="noreferrer"
                className="text-xs px-2 py-0.5 rounded border border-indigo-700 text-indigo-300 hover:bg-indigo-700/30"
                title={`Open on ${(secondaryProvider ?? "").toUpperCase()}`}
              >
                {(secondaryProvider ?? "").toUpperCase()} ↗
              </a>
            )}
            <button
              disabled={busy}
              className="text-xs px-2 py-0.5 rounded border border-slate-700 hover:bg-slate-800 disabled:opacity-50"
              onClick={() => setOpen((v) => !v)}
            >
              {open ? "Hide" : "Edit"}
            </button>
            <button
              disabled={busy}
              className="text-xs px-2 py-0.5 rounded border border-yellow-600/60 text-yellow-300 hover:bg-yellow-600/20 disabled:opacity-50"
              onClick={() => save(null, null)}
              title="Remove the manual secondary id"
            >
              Clear
            </button>
          </>
        ) : (
          <>
            <span className="text-xs text-slate-500">
              No manual {otherLabel} id linked.
            </span>
            <button
              disabled={busy}
              className="text-xs px-2 py-0.5 rounded border border-slate-700 hover:bg-slate-800 disabled:opacity-50"
              onClick={() => setOpen((v) => !v)}
            >
              {open ? "Hide" : `Add ${otherLabel} id`}
            </button>
          </>
        )}
      </div>
      <p className="text-xs text-slate-500 mt-2">
        Pin a {otherLabel} id when {primaryProvider.toUpperCase()}'s record doesn't cross-reference
        {" "}{otherLabel}. Used for cross-provider artwork, fanart.tv lookups, and the NFO
        {" "}<code className="text-slate-400">&lt;uniqueid&gt;</code> tag. Persists in the sidecar.
      </p>

      {open && (
        <div className="mt-3 border-t border-slate-800 pt-3 space-y-3">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
              Paste {otherLabel} id
            </div>
            <div className="flex flex-wrap gap-2">
              <input
                value={pasteId}
                onChange={(e) => setPasteId(e.target.value)}
                placeholder={`${otherLabel} id (e.g. ${otherProvider === "tmdb" ? "12345" : "81189"})`}
                className="bg-slate-800 px-2 py-1 rounded text-sm flex-1 min-w-[12rem] border border-slate-700 font-mono"
                onKeyDown={(e) => {
                  if (e.key === "Enter") savePaste();
                }}
              />
              <button
                disabled={busy || !pasteId.trim()}
                className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 rounded text-sm disabled:opacity-50"
                onClick={savePaste}
              >
                Save
              </button>
            </div>
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
              Or search {otherLabel}
            </div>
            <div className="flex flex-wrap gap-2 mb-2">
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={`${otherLabel} title`}
                className="bg-slate-800 px-2 py-1 rounded text-sm flex-1 min-w-[12rem] border border-slate-700"
                onKeyDown={(e) => {
                  if (e.key === "Enter") runSearch();
                }}
              />
              <button
                disabled={busy || !searchQuery.trim()}
                className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded text-sm disabled:opacity-50"
                onClick={runSearch}
              >
                Search
              </button>
            </div>
            {matches.length > 0 && (
              <div className="max-h-72 overflow-auto border border-slate-800 rounded">
                {matches.map((m) => {
                  const externalId = String(m.tvdb_id || m.id);
                  return (
                    <div
                      key={`${otherProvider}-${externalId}`}
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
                          <span
                            className={`mr-1 px-1 rounded ${
                              otherProvider === "tmdb"
                                ? "bg-emerald-800/60 text-emerald-100"
                                : "bg-blue-800/60 text-blue-100"
                            }`}
                          >
                            {otherProvider}
                          </span>
                          {otherProvider}-{externalId}
                        </div>
                      </div>
                      <button
                        disabled={busy}
                        className="px-2 py-1 bg-indigo-600 hover:bg-indigo-500 rounded text-xs disabled:opacity-50"
                        onClick={() => save(otherProvider, externalId)}
                      >
                        Link
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
      {msg && <div className="text-xs text-amber-400 mt-2">{msg}</div>}
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

type TagSource = "tvdb" | "tmdb" | "custom";

function TagsPanel({
  path,
  tags,
  bindingProvider,
  onChanged,
}: {
  path: string;
  tags: { tvdb: string[]; tmdb: string[]; custom: string[] };
  bindingProvider: string | null;
  onChanged: () => void;
}) {
  const initialSource: TagSource =
    bindingProvider === "tmdb"
      ? "tmdb"
      : bindingProvider === "tvdb"
        ? "tvdb"
        : tags.tvdb.length
          ? "tvdb"
          : tags.tmdb.length
            ? "tmdb"
            : "custom";
  const [source, setSource] = useState<TagSource>(initialSource);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const list =
    source === "tvdb" ? tags.tvdb : source === "tmdb" ? tags.tmdb : tags.custom;

  const addTag = async () => {
    const value = draft.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      await api.items.tags.add(path, value);
      setDraft("");
      onChanged();
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const removeTag = async (tag: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.items.tags.remove(path, tag);
      onChanged();
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-slate-900/60 border border-slate-800 rounded-md p-3 mb-4">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <div className="text-[10px] uppercase tracking-wider text-slate-500">
          Tags
        </div>
        <div className="flex bg-slate-900 border border-slate-800 rounded-md p-0.5">
          {(["tvdb", "tmdb", "custom"] as TagSource[]).map((s) => (
            <button
              key={s}
              onClick={() => setSource(s)}
              className={`px-2.5 py-1 text-xs uppercase rounded transition ${
                source === s
                  ? "bg-indigo-600 text-white"
                  : "text-slate-400 hover:text-white hover:bg-slate-800"
              }`}
            >
              {s}
              <span className="ml-1 text-[10px] text-slate-400">
                {s === "tvdb"
                  ? tags.tvdb.length
                  : s === "tmdb"
                    ? tags.tmdb.length
                    : tags.custom.length}
              </span>
            </button>
          ))}
        </div>
        <span className="text-[11px] text-slate-500">
          {source === "custom"
            ? "Custom tags are appended to the metadata-source genres in your NFO."
            : `Read-only — fetched from ${source.toUpperCase()}.`}
        </span>
      </div>

      {list.length === 0 ? (
        <div className="text-xs text-slate-500 py-1">
          {source === "custom"
            ? "No custom tags yet."
            : `No tags from ${source.toUpperCase()} for this item.`}
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {list.map((tag) => (
            <span
              key={`${source}-${tag}`}
              className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full border ${
                source === "custom"
                  ? "bg-indigo-900/40 border-indigo-700 text-indigo-100"
                  : "bg-slate-800 border-slate-700 text-slate-200"
              }`}
            >
              {tag}
              {source === "custom" && (
                <button
                  type="button"
                  onClick={() => removeTag(tag)}
                  disabled={busy}
                  title={`Remove ${tag}`}
                  className="text-indigo-300 hover:text-white disabled:opacity-50"
                >
                  ×
                </button>
              )}
            </span>
          ))}
        </div>
      )}

      {source === "custom" && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addTag();
              }
            }}
            placeholder="Add custom tag…"
            disabled={busy}
            className="bg-slate-950 border border-slate-800 px-2 py-1 rounded text-sm text-slate-100 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
          />
          <button
            onClick={addTag}
            disabled={busy || draft.trim() === ""}
            className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 rounded text-xs disabled:opacity-50"
          >
            Add
          </button>
          {error && <span className="text-xs text-rose-400">{error}</span>}
        </div>
      )}
    </div>
  );
}
