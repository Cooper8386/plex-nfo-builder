import { errorMessage } from "../lib/errors";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, Item } from "../lib/api";
import ArtworkPicker from "./ArtworkPicker";
import EpisodeMapper from "./EpisodeMapper";
import OverridesTab from "./OverridesTab";
import { useConfirm } from "../components/confirm";

import {
  BindEmptyState,
  MatchPanel,
  SecondarySourcePanel,
} from "./SourcePanels";
import { OrphansPanel, WhyStatusPanel } from "./ItemDiagnostics";
import { providerPageUrl } from "./mediaLinks";
import RenameModal from "./RenameModal";

type Detail = {
  state: (Item & { orphan_count?: number | null }) | null;
  binding: {
    provider: "tvdb" | "tmdb";
    external_id: string;
    kind: "series" | "movie";
    title: string;
    year: number | null;
    source_locked: number;
    secondary_provider?: string | null;
    secondary_external_id?: string | null;
  } | null;
  artwork_files: string[];
  provider_episode_count: number | null;
  provider_used: string | null;
  library_kind?: string | null;
  metadata_source?: "tvdb" | "tmdb";
  tags: { tvdb: string[]; tmdb: string[]; custom: string[] };
};

type Tab = "overview" | "artwork" | "episodes" | "overrides";

/** Item workspace: source, file diagnostics and editing flows share one context. */
export default function DetailView({
  path,
  onBack,
}: {
  path: string;
  onBack: () => void;
}) {
  const qc = useQueryClient();
  const confirmDlg = useConfirm();
  const detail = useQuery<Detail>({
    queryKey: ["detail", path],
    queryFn: () => api.items.detail(path),
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    staleTime: 60_000,
  });
  const plexConfigured = !!health.data?.plex_configured;
  const [showRename, setShowRename] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => (jobId ? api.jobs.get(jobId) : Promise.resolve(null)),
    enabled: !!jobId,
    refetchInterval: jobId ? 1500 : false,
  });
  const currentJob = job.data;
  useEffect(() => {
    if (
      !jobId ||
      currentJob === undefined ||
      (currentJob && ["queued", "running"].includes(currentJob.status))
    )
      return;
    setJobId(null);
    setMsg(
      currentJob === null
        ? "Build status is no longer available. The server may have restarted. Check local files and Activity before starting another build."
        : currentJob.status === "cancelled"
          ? "Build interrupted. Some files may already have changed. Review local files and Activity before retrying."
          : ["error", "failed"].includes(currentJob.status)
            ? `Build failed. ${currentJob.messages[currentJob.messages.length - 1] ?? "Open Activity for details."}`
            : "Build complete. Local metadata and artwork refreshed.",
    );
    for (const key of ["detail", "episodes", "nfo-explain", "orphans"])
      void qc.invalidateQueries({ queryKey: [key, path] });
    void qc.invalidateQueries({ queryKey: ["items"] });
  }, [currentJob, jobId, path, qc]);
  const controlsBusy = busy || !!jobId;
  const [tab, setTab] = useState<Tab>("overview");
  const [showMatcher, setShowMatcher] = useState(false);
  // v0.11.7 — "Why partial?" diagnostic panel. Closed by default; the user
  // clicks the status pill to open it. Lazily fetched.
  const [showExplain, setShowExplain] = useState(false);

  if (detail.error && !detail.data)
    return (
      <div className="p-6">
        <button className="btn mb-4" onClick={onBack}>
          ← Library
        </button>
        <div role="alert" className="panel p-5 text-rose-300">
          Could not load this item. {detail.error.message}
          <button className="btn ml-3" onClick={() => detail.refetch()}>
            Retry
          </button>
        </div>
      </div>
    );
  if (!detail.data)
    return (
      <div role="status" className="p-6 space-y-4 animate-pulse">
        <div className="h-32 panel" />
        <div className="h-12 panel" />
        <p className="text-sm text-slate-400">Loading media details…</p>
      </div>
    );
  const {
    state,
    binding,
    artwork_files,
    provider_episode_count,
    provider_used,
    tags,
    library_kind,
  } = detail.data;
  const kind: "series" | "movie" =
    binding?.kind ?? (state?.kind === "movie" ? "movie" : "series");
  // v0.11.8: when offering manual matching, default the dropdown to whatever
  // the parent library was detected as ("tv" → series, "movies" → movie). For
  // empty / freshly-downloaded folders the per-folder scanner can't tell what
  // kind it is yet, and the previous code hard-coded "series" which in
  // practice flipped to "movie" whenever the scanner had bucketed a
  // single-video folder as a movie inside a TV library. The match panel now
  // honours the library kind unless the user has already bound the folder.
  const libraryDefaultKind: "series" | "movie" =
    library_kind === "movies"
      ? "movie"
      : library_kind === "tv"
        ? "series"
        : kind;
  const matchDefaultKind: "series" | "movie" = binding
    ? (binding.kind as "series" | "movie")
    : libraryDefaultKind;
  const providerLabel = (
    provider_used ??
    binding?.provider ??
    "tvdb"
  ).toUpperCase();
  const cacheBust = state?.last_built ?? 0;
  const filesByName: Record<string, string> = {};
  const seasonPosters: { season: string; path: string }[] = [];
  for (const f of (artwork_files ?? []) as string[]) {
    const name = f.split(/[\\/]/).pop() ?? f;
    filesByName[name] = f;
    const m = name.match(/^Season(\d+)-poster\.jpg$/i);
    if (m) seasonPosters.push({ season: m[1], path: f });
  }
  seasonPosters.sort((a, b) => Number(a.season) - Number(b.season));
  const slot = (name: string) => filesByName[name];
  const fileSrc = (p: string) => `${api.artwork.fileUrl(p)}&t=${cacheBust}`;

  const doAutoMatch = async () => {
    setBusy(true);
    setMsg("Matching source…");
    try {
      const result = await api.match.autoBulk({ folder_paths: [path] });
      const match = result.results[0];
      setMsg(result.matched ? "Source matched. No NFOs or artwork built." :
        match?.error ? `Match failed: ${match.error}` :
        "No confident match found. Search for the title below.");
      setTab("overview");
      setShowMatcher(!result.matched);
      for (const key of ["detail", "episodes", "artwork-candidates", "nfo-explain"])
        await qc.invalidateQueries({ queryKey: [key, path] });
      await qc.invalidateQueries({ queryKey: ["items"] });
    } catch (error) {
      setMsg(`Match failed: ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  };

  const doBuild = async (force: boolean) => {
    setBusy(true);
    setMsg(force ? "Force rebuild…" : "Build started…");
    try {
      const result = await api.build(path, kind, force);
      setJobId(result.job);
      setMsg("Build queued. Open Activity to follow progress.");
      await qc.invalidateQueries({ queryKey: ["jobs"] });
    } catch (cause) {
      setMsg(
        `Build could not start: ${cause instanceof Error ? cause.message : String(cause)}`,
      );
    } finally {
      setBusy(false);
    }
  };

  const doWipe = async () => {
    try {
      setBusy(true);
      setMsg("Listing files to remove…");
      const preview = await api.items.clean({
        folder_path: path,
        dry_run: true,
      });
      const files = preview.files ?? [];
      if (files.length === 0) {
        setMsg("Nothing to clean — no NFOs or artwork found.");
        setBusy(false);
        return;
      }
      const head = files.slice(0, 12).join("\n  • ");
      const more =
        files.length > 12 ? `\n  … and ${files.length - 12} more` : "";
      const ok = await confirmDlg({
        title: `Wipe NFOs & artwork for “${state?.title ?? path}”?`,
        message:
          `${files.length} file${files.length === 1 ? "" : "s"} will be deleted from disk:\n  • ${head}${more}\n\n` +
          `Season folders and media files (.mkv/.mp4/etc.) are NOT touched. ` +
          `The .plex-nfo-builder.json sidecar is preserved so your binding and overrides stay intact.`,
        confirmLabel: "Wipe",
        tone: "danger",
      });
      if (!ok) {
        setMsg("Cancelled.");
        setBusy(false);
        return;
      }
      setMsg("Cleaning…");
      const res = await api.items.clean({ folder_path: path, dry_run: false });
      setMsg(
        `Cleaned: ${res.nfo_deleted ?? 0} NFO file${res.nfo_deleted === 1 ? "" : "s"}, ` +
          `${res.artwork_deleted ?? 0} artwork file${res.artwork_deleted === 1 ? "" : "s"} removed.`,
      );
      await qc.invalidateQueries({ queryKey: ["detail", path] });
      await qc.invalidateQueries({ queryKey: ["items"] });
    } catch (e: unknown) {
      setMsg(`Failed: ${errorMessage(e)}`);
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
          `Plex re-reading metadata for "${r.item_title || r.section_title}" (ratingKey ${r.rating_key}). Updated NFO and artwork should appear in a moment.`,
        );
      } else if (r.refreshed) {
        setMsg(
          `Plex partial scan queued for "${r.section_title}" but no item matched ${r.translated_path ?? path}. ${r.error || "Plex hasn't indexed this folder yet — wait for the scan to finish, then click Refresh in Plex again to force the NFO re-read."}`,
        );
      } else {
        setMsg(`Plex refresh failed: ${r.error || "unknown error"}`);
      }
    } catch (e: unknown) {
      setMsg(`Plex refresh failed: ${errorMessage(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const doRemove = async () => {
    const ok = await confirmDlg({
      title: `Remove “${state?.title ?? path}” from the library?`,
      message: `This only forgets it in the database — no files are deleted. Use this when you've already deleted the folder on disk.`,
      confirmLabel: "Remove",
      tone: "danger",
    });
    if (!ok) return;
    setBusy(true);
    setMsg("Removing from library…");
    try {
      await api.items.remove(path);
      setMsg("Removed. Returning to library.");
      await qc.invalidateQueries({ queryKey: ["items"] });
      setTimeout(() => onBack(), 600);
    } catch (e: unknown) {
      setMsg(`Failed: ${errorMessage(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="p-4 sm:p-6 max-w-[1440px] mx-auto">
      <button
        onClick={onBack}
        className="text-indigo-400 text-sm mb-4 inline-flex items-center gap-1 hover:text-indigo-300"
      >
        ← Back to library
      </button>

      <header className="flex gap-4 sm:gap-5 pb-5 mb-4 border-b border-slate-800">
        <div className="w-20 sm:w-24 shrink-0 aspect-[2/3] rounded overflow-hidden bg-slate-900 border border-slate-800 self-start">
          {slot("poster.jpg") ? (
            <img
              src={fileSrc(slot("poster.jpg")!)}
              alt=""
              className="w-full h-full object-cover"
            />
          ) : (
            <div className="h-full flex items-center justify-center text-slate-500 text-xs">
              No poster
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[10px] uppercase tracking-widest text-amber-400 mb-2">
            {state?.library ?? "Media library"} /{" "}
            {kind === "series" ? "Series" : "Movie"}
          </p>
          <div className="flex flex-wrap items-baseline gap-2 mb-2">
            <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight break-words">
              {state?.title ?? binding?.title ?? path.split(/[\\/]/).pop()}
            </h1>
            {state?.year && (
              <span className="text-slate-500">({state.year})</span>
            )}
            {(() => {
              const url = providerPageUrl(
                (binding?.provider as string | undefined) ?? null,
                binding?.external_id ?? null,
                kind,
              );
              if (!url) return null;
              const label =
                (binding?.provider ?? "").toLowerCase() === "tmdb"
                  ? "TMDB"
                  : "TVDB";
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
              <button
                type="button"
                aria-expanded={showExplain}
                onClick={() => setShowExplain((v) => !v)}
                title={
                  showExplain
                    ? "Hide the breakdown of why this status was assigned"
                    : "Click to see exactly which files / NFOs led to this status"
                }
                className={`text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wide cursor-pointer transition ring-1 ring-transparent hover:ring-indigo-400 ${
                  state.nfo_status === "complete"
                    ? "bg-emerald-700 text-emerald-100"
                    : state.nfo_status === "partial" ||
                        state.nfo_status === "stale"
                      ? "bg-amber-700 text-amber-100"
                      : "bg-slate-700 text-slate-200"
                }`}
              >
                {state.nfo_status}
                <span className="ml-1 opacity-80">
                  {showExplain ? "▴" : "▾"}
                </span>
              </button>
            )}
          </div>
          <p className="text-xs text-slate-400 font-mono break-all">{path}</p>
          <p className="text-xs text-slate-400 mt-3">
            {binding
              ? `${providerLabel} · ${binding.external_id}${binding.source_locked ? " · Source locked" : ""}`
              : "Match this folder to unlock provider metadata and artwork."}
            {state?.last_built
              ? ` · Built ${new Date(state.last_built * 1000).toLocaleString()}`
              : " · No completed build"}
          </p>
        </div>
      </header>

      {showExplain && (
        <WhyStatusPanel path={path} onClose={() => setShowExplain(false)} />
      )}

      <OrphansPanel
        path={path}
        title={state?.title ?? path}
        cachedOrphanCount={state?.orphan_count ?? null}
      />

      {kind === "series" && (
        <div className="flex flex-wrap gap-x-6 gap-y-2 mb-4 text-xs text-slate-400">
          <span>
            <b className="text-slate-100">
              {state?.episode_count_local ?? "—"}
            </b>{" "}
            local episodes
          </span>
          <span>
            <b className="text-slate-100">
              {binding ? (provider_episode_count ?? "—") : "—"}
            </b>{" "}
            episodes matched on {providerLabel}
          </span>
        </div>
      )}

      {/* Action row — primary actions inline, everything secondary tucked in overflow menu */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button
          disabled={controlsBusy || !!binding?.source_locked}
          title={binding?.source_locked ? "Source locked. Use Change match to choose another title." :
            `Match using ${(detail.data.metadata_source ?? "tvdb").toUpperCase()} without building files.`}
          className="btn"
          onClick={doAutoMatch}
        >
          Auto-match only
        </button>
        <button
          disabled={controlsBusy}
          title="Generate NFO files and download artwork. Uses cached metadata when available."
          className="btn btn-primary"
          onClick={() => doBuild(false)}
        >
          Build NFOs
        </button>
        <button
          disabled={controlsBusy}
          title="Re-fetch provider metadata. Foreign NFO protection follows Settings. Saved overrides are applied."
          className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-sm disabled:opacity-50"
          onClick={() => doBuild(true)}
        >
          Force rebuild
        </button>
        {binding && (
          <button
            disabled={controlsBusy}
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
        {kind === "movie" && binding && (
          <button
            className="btn"
            disabled={controlsBusy}
            onClick={() => setShowRename(true)}
          >
            Preview rename…
          </button>
        )}
        <div className="flex-1" />
        <OverflowMenu
          disabled={controlsBusy}
          items={[
            {
              label: "Delete NFOs & artwork…",
              tone: "danger",
              onClick: doWipe,
            },
            ...(plexConfigured
              ? [
                  {
                    label: "Refresh in Plex",
                    tone: "ok" as const,
                    onClick: doPlexRefresh,
                  },
                ]
              : []),
            { label: "Remove from library", tone: "danger", onClick: doRemove },
          ]}
        />
      </div>
      {msg && (
        <div
          role="status"
          className="panel px-3 py-2 text-xs text-slate-300 mb-3"
        >
          {currentJob && ["queued", "running"].includes(currentJob.status)
            ? `Build ${currentJob.status} · ${currentJob.progress} / ${currentJob.total}`
            : msg}
          {jobId && job.error && (
            <span className="block mt-1 text-amber-300">
              Progress unavailable: {job.error.message}. Retrying…
            </span>
          )}
        </div>
      )}

      <div
        aria-label="Item sections"
        className="border-b border-slate-800 mb-5 flex gap-1 overflow-x-auto"
      >
        {(
          [
            "overview",
            "artwork",
            ...(kind === "series" ? (["episodes"] as Tab[]) : []),
            "overrides",
          ] as Tab[]
        ).map((t) => (
          <button
            key={t}
            aria-pressed={tab === t}
            onClick={() => setTab(t)}
            className={`shrink-0 whitespace-nowrap px-4 py-2 text-sm capitalize border-b-2 transition -mb-px ${
              tab === t
                ? "border-indigo-500 text-white"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {t === "overrides" ? "Metadata overrides" : t}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div>
          {!binding ? (
            <BindEmptyState
              path={path}
              detectedKind={matchDefaultKind}
              defaultProvider={detail.data.metadata_source}
              onBound={() =>
                qc.invalidateQueries({ queryKey: ["detail", path] })
              }
            />
          ) : showMatcher ? (
            <MatchPanel
              path={path}
              detectedKind={matchDefaultKind}
              defaultProvider={binding.provider}
              onBound={() => {
                qc.invalidateQueries({ queryKey: ["detail", path] });
                setShowMatcher(false);
              }}
            />
          ) : null}

          <TagsPanel
            path={path}
            tags={tags ?? { tvdb: [], tmdb: [], custom: [] }}
            bindingProvider={(binding?.provider as string | undefined) ?? null}
            onChanged={() =>
              qc.invalidateQueries({ queryKey: ["detail", path] })
            }
          />

          {binding && (
            <SecondarySourcePanel
              key={`${path}-${binding.kind}-${binding.provider}-${binding.external_id}`}
              path={path}
              kind={kind}
              primaryProvider={(binding.provider as "tvdb" | "tmdb") ?? "tvdb"}
              secondaryProvider={
                (binding.secondary_provider as string | null) ?? null
              }
              secondaryExternalId={
                (binding.secondary_external_id as string | null) ?? null
              }
              onChanged={() =>
                qc.invalidateQueries({ queryKey: ["detail", path] })
              }
            />
          )}

          <div className="flex items-center justify-between gap-2 mt-6 mb-2">
            <h3 className="font-semibold">Local artwork</h3>
            {binding && (
              <button className="btn" onClick={() => setTab("artwork")}>
                Choose artwork →
              </button>
            )}
          </div>
          <p className="text-xs text-slate-500 mb-3">
            The active local files Plex reads from the folder. Rebuilds
            overwrite these in place.
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
                  src={
                    slot("background.jpg")
                      ? fileSrc(slot("background.jpg")!)
                      : null
                  }
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
                  src={
                    slot("clearlogo.png")
                      ? fileSrc(slot("clearlogo.png")!)
                      : null
                  }
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
                        filename={sp.path.split(/[\\/]/).pop() ?? ""}
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
              Bind this folder to a TVDB or TMDB title from the Overview tab to
              pick artwork.
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
              Bind this folder to a TVDB or TMDB series from the Overview tab
              first.
            </div>
          ) : (
            <EpisodeMapper path={path} />
          )}
        </div>
      )}

      {showRename && (
        <RenameModal
          path={path}
          onClose={() => setShowRename(false)}
          onApplied={async () => {
            await qc.invalidateQueries({ queryKey: ["detail", path] });
            await qc.invalidateQueries({ queryKey: ["items"] });
          }}
        />
      )}
      {tab === "overrides" && (
        <OverridesTab
          key={`${path}-${binding?.provider}-${binding?.external_id}`}
          path={path}
          kind={kind}
          binding={binding}
        />
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
  items: Array<{
    label: string;
    tone?: "warn" | "danger" | "ok";
    onClick: () => void;
  }>;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
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
    <div
      className="relative"
      ref={ref}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          setOpen(false);
          ref.current?.querySelector("button")?.focus();
        }
      }}
    >
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        title="More actions"
        aria-label="More item actions"
        aria-expanded={open}
        className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded text-sm disabled:opacity-50"
      >
        More actions ▾
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
        style={
          contain
            ? {
                backgroundImage:
                  "linear-gradient(45deg, #0f172a 25%, #111827 25%, #111827 50%, #0f172a 50%, #0f172a 75%, #111827 75%)",
                backgroundSize: "16px 16px",
              }
            : undefined
        }
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
      <div
        className={`px-2 py-1 ${compact ? "" : "border-t border-slate-800"}`}
      >
        <div
          className={`${compact ? "text-[10px]" : "text-xs"} font-medium text-slate-200 truncate`}
        >
          {label}
        </div>
        {!compact && (
          <div className="text-[10px] text-slate-500 font-mono truncate">
            {filename}
          </div>
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
    } catch (e: unknown) {
      setError(errorMessage(e));
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
    } catch (e: unknown) {
      setError(errorMessage(e));
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
            aria-label="Custom tag"
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
