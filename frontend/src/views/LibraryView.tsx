import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ViewMode } from "../App";
import { useConfirm } from "../components/confirm";
import {
  filterToParams,
  loadArtworkFilterFor,
  loadFilterFor,
  loadSortFor,
  sortItems,
  SORT_OPTIONS,
  type ArtworkFilter,
  type LibFilter,
  type SortKey,
} from "../lib/library";
import LibraryMaintenance from "./LibraryMaintenance";
import { LibraryGrid, LibraryList } from "./LibraryItems";

export default function LibraryView(props: {
  library: string | null;
  viewMode: ViewMode;
  search: string;
  onSearch: (search: string) => void;
  onViewMode: (mode: ViewMode) => void;
  onSelectLibrary: (library: string) => void;
  onOpenDetail: (path: string) => void;
  onItemsReady?: () => void;
}) {
  const qc = useQueryClient();
  const confirmDlg = useConfirm();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [filter, setFilter] = useState<LibFilter>(() =>
    loadFilterFor(props.library),
  );
  const [artworkFilter, setArtworkFilter] = useState<ArtworkFilter>(() =>
    loadArtworkFilterFor(props.library),
  );
  const [sort, setSort] = useState<SortKey>(() => loadSortFor(props.library));
  const [query, setQuery] = useState(props.search);
  useEffect(() => {
    const id = setTimeout(() => setQuery(props.search), 250);
    return () => clearTimeout(id);
  }, [props.search]);
  const { data, isPending, isFetching, error, refetch } = useQuery({
    queryKey: ["items", props.library, query, filter, artworkFilter],
    queryFn: () =>
      api.items.list({
        library: props.library ?? undefined,
        q: query || undefined,
        ...filterToParams(filter),
        manual_artwork: artworkFilter === "any" ? undefined : artworkFilter,
      }),
    enabled: !!props.library,
    staleTime: 60_000,
  });
  const { onItemsReady } = props;
  useEffect(() => {
    if (data) onItemsReady?.();
  }, [data, onItemsReady]);
  const items = useMemo(() => sortItems(data?.items ?? [], sort), [data, sort]);
  // Only visible, current-library rows can enter an operation, including while filters load.
  const selectedPaths = useMemo(
    () =>
      items
        .filter((item) => selected.has(item.folder_path))
        .map((item) => item.folder_path),
    [items, selected],
  );
  const allSelected = items.length > 0 && selectedPaths.length === items.length;
  const flash = (message: string) => setToast(message);
  const clearSelection = () => setSelected(new Set());
  const toggle = (path: string) =>
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  const toggleAll = () =>
    setSelected(
      allSelected ? new Set() : new Set(items.map((item) => item.folder_path)),
    );
  const saveFilter = (next: LibFilter) => {
    setFilter(next);
    clearSelection();
    try {
      localStorage.setItem(`pnb.libFilter.${props.library}`, next);
    } catch {
      /* Optional preference storage. */
    }
  };
  const saveArtworkFilter = (next: ArtworkFilter) => {
    setArtworkFilter(next);
    clearSelection();
    try {
      localStorage.setItem(`pnb.artworkFilter.${props.library}`, next);
    } catch {
      /* Optional preference storage. */
    }
  };
  const saveSort = (next: SortKey) => {
    setSort(next);
    try {
      localStorage.setItem(`pnb.libSort.${props.library}`, next);
    } catch {
      /* Optional preference storage. */
    }
  };
  const scan = async () => {
    if (!props.library || busy) return;
    setBusy("Scanning library…");
    setToast(null);
    try {
      await api.libraries.scan(props.library);
      await qc.invalidateQueries({ queryKey: ["items"] });
      flash("Scan complete. Library is up to date.");
    } catch (error) {
      flash(
        `Scan failed: ${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      setBusy(null);
    }
  };
  const runAutoMatch = async (scope: "selected" | "library") => {
    if (!props.library) return;
    setBusy(
      scope === "selected"
        ? "Auto-matching selected…"
        : "Auto-matching library…",
    );
    try {
      // v0.8.0: "Auto-match all" now processes every folder in the library —
      // we deliberately drop only_unmatched:true so already-matched folders
      // can be re-resolved if their bindings are stale.
      const body =
        scope === "selected"
          ? { folder_paths: selectedPaths }
          : { library: props.library };
      const res = await api.match.autoBulk(body);
      flash(`Auto-match: ${res.matched}/${res.total} matched`);
      qc.invalidateQueries({ queryKey: ["items"] });
    } catch (e: unknown) {
      flash(`Auto-match failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  const runPruneMissing = async () => {
    if (!props.library) return;
    setBusy("Scanning for missing folders…");
    try {
      const dry = await api.items.prune({
        library: props.library,
        dry_run: true,
      });
      if (dry.missing === 0) {
        flash(
          `Nothing to prune — all ${dry.checked} folder(s) still exist on disk.`,
        );
        return;
      }
      const preview = dry.items
        .slice(0, 10)
        .map((i) => `• ${i.title ?? i.folder_path}`)
        .join("\n");
      const more = dry.missing > 10 ? `\n… and ${dry.missing - 10} more` : "";
      const ok = await confirmDlg({
        title: `Forget ${dry.missing} missing folder(s)?`,
        message: `These folders are tracked in the database but no longer exist on disk:\n\n${preview}${more}\n\nForget all of them? (No files are deleted.)`,
        confirmLabel: "Forget",
        tone: "danger",
      });
      if (!ok) return;
      const res = await api.items.prune({
        library: props.library,
        dry_run: false,
      });
      flash(`Pruned ${res.removed} missing folder(s)`);
      qc.invalidateQueries({ queryKey: ["items"] });
    } catch (e: unknown) {
      flash(`Prune failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  // v0.11.5 — prune folders that exist on disk but contain *zero* media
  // files. Dry-run preview → single confirm → delete. The backend
  // re-walks every candidate folder immediately before deletion so a
  // download landing between the preview and the confirm cannot be
  // pruned by accident; folders that gain a video in that window are
  // reported back in `skipped`. Files on disk are never deleted by this
  // path — we only forget the database row — so even if a freak race did
  // slip through, no media could be lost. The user can re-scan to add the
  // folder back.
  const runPruneEmpty = async () => {
    if (!props.library) return;
    setBusy("Looking for folders with no media…");
    try {
      const dry = await api.items.pruneEmpty({
        library: props.library,
        dry_run: true,
      });
      if (dry.candidates === 0) {
        flash(
          `No empty folders — every tracked folder in "${props.library}" contains at least one media file.`,
        );
        return;
      }
      const preview = dry.items
        .slice(0, 12)
        .map((i) => `• ${i.title ?? i.folder_path}`)
        .join("\n");
      const more =
        dry.candidates > 12 ? `\n… and ${dry.candidates - 12} more` : "";
      const ok = await confirmDlg({
        title: `Prune ${dry.candidates} empty folder(s)?`,
        message:
          `These folders exist on disk but contain ZERO media files:\n\n` +
          `${preview}${more}\n\n` +
          `Forget all of them in the database?\n\n` +
          `Each folder will be re-checked immediately before deletion. Any folder ` +
          `that contains media at that moment is skipped — video, audio, and ` +
          `subtitle files are NEVER touched by this action.`,
        confirmLabel: "Prune",
        tone: "danger",
      });
      if (!ok) return;
      const res = await api.items.pruneEmpty({
        library: props.library,
        dry_run: false,
        delete_files: false,
      });
      const skippedCount = res.skipped?.length ?? 0;
      const skippedPart = skippedCount
        ? ` — ${skippedCount} skipped (gained media before delete)`
        : "";
      flash(`Pruned ${res.removed ?? 0} empty folder(s)${skippedPart}`);
      qc.invalidateQueries({ queryKey: ["items"] });
    } catch (e: unknown) {
      flash(
        `Prune empty failed: ${e instanceof Error ? e.message : String(e)}`,
      );
    } finally {
      setBusy(null);
    }
  };

  const runRemoveSelected = async () => {
    if (!selectedPaths.length) return;
    const ok = await confirmDlg({
      title: `Remove ${selectedPaths.length} item(s) from the library?`,
      message: `This only forgets them in the database — no files are deleted on disk.`,
      confirmLabel: "Remove",
      tone: "danger",
    });
    if (!ok) return;
    setBusy(`Removing ${selectedPaths.length} item(s)…`);
    try {
      let removed = 0;
      let failed = 0;
      for (const p of selectedPaths) {
        try {
          const r = await api.items.remove(p);
          removed += r.removed;
        } catch {
          failed++;
        }
      }
      flash(
        `Removed ${removed} item(s) from library${failed ? `; ${failed} failed. Retry failed items.` : ""}`,
      );
      clearSelection();
      qc.invalidateQueries({ queryKey: ["items"] });
    } finally {
      setBusy(null);
    }
  };

  const runBuild = async (scope: "selected" | "library") => {
    if (!props.library) return;
    setBusy(
      scope === "selected" ? "Queuing builds…" : "Queuing library builds…",
    );
    try {
      const body =
        scope === "selected"
          ? { folder_paths: selectedPaths }
          : { library: props.library, only_unbuilt: true };
      const res = await api.buildBulk(body);
      flash(`Queued ${res.queued} build job(s) — see Jobs tab`);
      clearSelection();
    } catch (e: unknown) {
      flash(`Build failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  if (!props.library) return <LibraryHome onSelect={props.onSelectLibrary} />;
  const locked = !!busy || isFetching || query !== props.search;
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Media library</p>
          <h1 className="page-title">{props.library}</h1>
          <p className="text-xs text-slate-500 mt-2">
            {isPending
              ? "Loading titles…"
              : `${items.length.toLocaleString()} ${items.length === 1 ? "title" : "titles"}${filter !== "all" || artworkFilter !== "any" || query ? " in this view" : ""}`}{" "}
            · Local metadata & artwork
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-ghost" disabled={!!busy} onClick={scan}>
            {busy === "Scanning library…" ? "Scanning…" : "Scan library"}
          </button>
          <button
            className="btn"
            disabled={locked}
            onClick={() => runAutoMatch("library")}
          >
            Auto-match all
          </button>
          <button
            className="btn btn-primary"
            disabled={locked}
            onClick={() => runBuild("library")}
          >
            Build missing
          </button>
        </div>
      </div>
      <div className="library-toolbar">
        <label className="relative flex-1 min-w-[180px] max-w-md">
          <span className="sr-only">Search library</span>
          <input
            type="search"
            className="field w-full"
            placeholder="Search titles…"
            value={props.search}
            onChange={(event) => {
              props.onSearch(event.target.value);
              clearSelection();
            }}
          />
        </label>
        <div
          className="flex gap-0.5 rounded-md bg-slate-900 p-1 border border-slate-800"
          aria-label="Filter by status"
        >
          {(
            [
              ["all", "All titles"],
              ["needs", "Needs work"],
              ["complete", "Complete"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              className={`px-3 py-1.5 rounded text-xs ${filter === key ? "bg-slate-700 text-slate-100" : "text-slate-400"}`}
              aria-pressed={filter === key}
              onClick={() => saveFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <select
          aria-label="Filter by manual artwork"
          className="field text-xs"
          value={artworkFilter}
          onChange={(event) =>
            saveArtworkFilter(event.target.value as ArtworkFilter)
          }
        >
          <option value="any">Manual artwork: Any</option>
          <option value="complete">Manual artwork: Complete</option>
          <option value="incomplete">Manual artwork: Incomplete</option>
        </select>
        <select
          aria-label="Sort library"
          className="field text-xs"
          value={sort}
          onChange={(event) => saveSort(event.target.value as SortKey)}
        >
          {SORT_OPTIONS.map((option) => (
            <option key={option.key} value={option.key}>
              {option.label}
            </option>
          ))}
        </select>
        <div className="flex gap-1 ml-auto" aria-label="Library layout">
          {(["grid", "list"] as const).map((mode) => (
            <button
              key={mode}
              className={`btn ${props.viewMode === mode ? "border-indigo-500 text-indigo-300" : ""}`}
              aria-pressed={props.viewMode === mode}
              onClick={() => props.onViewMode(mode)}
            >
              {mode === "grid" ? "Grid" : "List"}
            </button>
          ))}
        </div>
      </div>
      <div className="flex items-center flex-wrap gap-3 mb-5 min-h-9">
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={allSelected}
            disabled={locked || !items.length}
            onChange={toggleAll}
          />
          Select visible
        </label>
        {selectedPaths.length > 0 && (
          <>
            <span className="badge">{selectedPaths.length} selected</span>
            <button
              className="btn"
              disabled={locked}
              onClick={() => runAutoMatch("selected")}
            >
              Match selected
            </button>
            <button
              className="btn btn-primary"
              disabled={locked}
              onClick={() => runBuild("selected")}
            >
              Build selected
            </button>
            <button
              className="btn btn-danger"
              disabled={locked}
              onClick={runRemoveSelected}
            >
              Forget selected
            </button>
            <button className="text-xs text-slate-400" onClick={clearSelection}>
              Clear
            </button>
          </>
        )}
        {busy && (
          <span role="status" className="text-xs text-indigo-300 ml-auto">
            {busy}
          </span>
        )}
        {!busy && isFetching && (
          <span role="status" className="text-xs text-slate-500 ml-auto">
            Updating view…
          </span>
        )}
      </div>
      {toast && (
        <div
          role="status"
          className="notice mb-4 flex items-start justify-between gap-3"
        >
          <span>{toast}</span>
          <button
            aria-label="Dismiss notification"
            onClick={() => setToast(null)}
          >
            ×
          </button>
        </div>
      )}
      {error ? (
        <div role="alert" className="notice error-notice">
          Could not load this library: {error.message}
          <button className="btn ml-3" onClick={() => refetch()}>
            Retry
          </button>
        </div>
      ) : isPending ? (
        <div className="poster-grid" aria-label="Loading titles">
          {Array.from({ length: 12 }, (_, i) => (
            <div key={i} className="skeleton aspect-[2/3] rounded-md" />
          ))}
        </div>
      ) : !items.length ? (
        <div className="empty-state">
          <h2 className="text-base text-slate-200 mb-2">
            {query || filter !== "all" || artworkFilter !== "any"
              ? "No titles match this view"
              : "Ready for your media"}
          </h2>
          <p className="text-sm">
            {query || filter !== "all" || artworkFilter !== "any"
              ? "Try another title or clear the filters."
              : "Scan this library to discover movie and series folders."}
          </p>
          {(query || filter !== "all" || artworkFilter !== "any") && (
            <button
              className="btn mt-4"
              onClick={() => {
                props.onSearch("");
                saveFilter("all");
                saveArtworkFilter("any");
              }}
            >
              Clear filters
            </button>
          )}
        </div>
      ) : props.viewMode === "grid" ? (
        <LibraryGrid
          items={items}
          selected={selected}
          onToggle={toggle}
          onOpen={props.onOpenDetail}
        />
      ) : (
        <LibraryList
          items={items}
          selected={selected}
          onToggle={toggle}
          onOpen={props.onOpenDetail}
        />
      )}
      <details className="mt-8 text-xs text-slate-400">
        <summary className="py-2">Database cleanup</summary>
        <p className="mb-3">
          Forget missing or empty folders from the app. Files remain on disk.
        </p>
        <div className="flex gap-2">
          <button className="btn" disabled={!!busy} onClick={runPruneMissing}>
            Preview missing folders
          </button>
          <button className="btn" disabled={!!busy} onClick={runPruneEmpty}>
            Preview empty folders
          </button>
        </div>
      </details>
      <LibraryMaintenance
        library={props.library}
        busy={busy}
        setBusy={setBusy}
        flash={flash}
        invalidateItems={() => qc.invalidateQueries({ queryKey: ["items"] })}
        btnHazard="btn btn-danger"
        btnHazardOutline="btn btn-danger"
      />
    </div>
  );
}

function LibraryHome({ onSelect }: { onSelect: (name: string) => void }) {
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["libraries"],
    queryFn: api.libraries.list,
  });
  const libraries =
    data?.libraries.filter((library) => Number(library.enabled) === 1) ?? [];
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Your workspace</p>
          <h1 className="page-title">Media libraries</h1>
          <p className="text-slate-400 text-sm mt-2">
            Match your titles. Choose artwork. Build metadata that stays with
            your media.
          </p>
        </div>
      </div>
      {error ? (
        <div role="alert" className="notice error-notice">
          {error.message}
          <button className="btn ml-3" onClick={() => refetch()}>
            Retry
          </button>
        </div>
      ) : isPending ? (
        <p role="status">Loading libraries…</p>
      ) : libraries.length ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {libraries.map((library) => (
            <button
              key={library.name}
              onClick={() => onSelect(library.name)}
              className="panel text-left p-6 hover:border-indigo-600 transition-colors"
            >
              <span className="eyebrow block">
                {library.kind === "movies"
                  ? "Movies"
                  : library.kind === "mixed"
                    ? "Mixed media"
                    : "TV series"}
              </span>
              <span className="block text-lg font-medium mt-5">
                {library.name}
              </span>
              <span className="flex justify-between text-xs text-slate-500 mt-3">
                <span>
                  {library.effective_metadata_source?.toUpperCase() ??
                    "Default provider"}
                </span>
                <span className="text-indigo-300">Open library ↗</span>
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="empty-state">
          <h2 className="text-slate-200 mb-2">No enabled libraries yet</h2>
          <p>
            Mount your media under MEDIA_ROOT, then use Detect in the library
            sidebar.
          </p>
        </div>
      )}
      <div className="mt-10 max-w-2xl border-t border-slate-800 pt-5">
        <p className="eyebrow">A simple workflow</p>
        <div className="grid sm:grid-cols-3 gap-6 mt-5">
          {[
            [
              "01",
              "Scan & match",
              "Discover folders and connect each title to its metadata source.",
            ],
            [
              "02",
              "Review & refine",
              "Check episodes, artwork and any title-specific overrides.",
            ],
            [
              "03",
              "Build & keep",
              "Write local NFOs and artwork. Review progress in Activity.",
            ],
          ].map(([number, title, body]) => (
            <div key={number}>
              <span className="text-xs font-mono text-indigo-300">
                {number}
              </span>
              <h2 className="font-medium mt-2 mb-1">{title}</h2>
              <p className="text-xs leading-relaxed text-slate-500">{body}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
