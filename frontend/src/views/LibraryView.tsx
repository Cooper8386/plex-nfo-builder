import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Item } from "../lib/api";
import { ViewMode } from "../App";
import { useConfirm } from "../components/ConfirmDialog";

// v0.11.4 — "Needs work / Complete / All" filter pill on the library toolbar.
// `Needs work` is anything that isn't fully built. `Complete` is the inverse.
type LibFilter = "all" | "needs" | "complete";
const NEEDS_WORK_STATUSES = "none,partial,stale,foreign,mixed";

function filterToParams(f: LibFilter): { status?: string } {
  if (f === "needs") return { status: NEEDS_WORK_STATUSES };
  if (f === "complete") return { status: "complete" };
  return {};
}

function loadFilterFor(library: string | null): LibFilter {
  if (!library) return "all";
  try {
    const v = localStorage.getItem(`pnb.libFilter.${library}`);
    if (v === "needs" || v === "complete" || v === "all") return v;
  } catch {}
  return "all";
}

const STATUS_COLOR: Record<string, string> = {
  none: "bg-slate-700 text-slate-200",
  partial: "bg-amber-700 text-amber-100",
  complete: "bg-emerald-700 text-emerald-100",
  stale: "bg-orange-700 text-orange-100",
  foreign: "bg-purple-700 text-purple-100",
  mixed: "bg-cyan-700 text-cyan-100",
};

export default function LibraryView(props: {
  library: string | null;
  viewMode: ViewMode;
  search: string;
  onOpenDetail: (path: string) => void;
  /**
   * v0.11.4 — fires after the items query resolves so App.tsx can restore
   * the previous scroll position when the user navigates back into a library.
   */
  onItemsReady?: () => void;
}) {
  const qc = useQueryClient();
  const confirmDlg = useConfirm();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [filter, setFilter] = useState<LibFilter>(() => loadFilterFor(props.library));

  // Reload persisted filter when the active library changes.
  useEffect(() => {
    setFilter(loadFilterFor(props.library));
  }, [props.library]);

  const setFilterPersisted = (f: LibFilter) => {
    setFilter(f);
    if (props.library) {
      try {
        localStorage.setItem(`pnb.libFilter.${props.library}`, f);
      } catch {}
    }
  };

  const { data, isFetching } = useQuery({
    queryKey: ["items", props.library, props.search, filter],
    queryFn: () =>
      api.items.list({
        library: props.library || undefined,
        q: props.search || undefined,
        ...filterToParams(filter),
      }),
    enabled: !!props.library,
  });

  // Notify App.tsx as soon as items have rendered so it can restore scroll
  // position. We tie this to `data` (not isFetching) so a background refetch
  // doesn't trigger a re-restore that would yank the user back to the top.
  useEffect(() => {
    if (!data) return;
    props.onItemsReady?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const items = data?.items ?? [];
  const allSelected = items.length > 0 && items.every((i) => selected.has(i.folder_path));
  const someSelected = selected.size > 0;

  const toggle = (path: string) => {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(path)) n.delete(path);
      else n.add(path);
      return n;
    });
  };
  const toggleAll = () => {
    if (allSelected) setSelected(new Set());
    else setSelected(new Set(items.map((i) => i.folder_path)));
  };
  const clearSelection = () => setSelected(new Set());

  const selectedPaths = useMemo(() => Array.from(selected), [selected]);

  const flash = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 4000);
  };

  const runAutoMatch = async (scope: "selected" | "library") => {
    if (!props.library) return;
    setBusy(scope === "selected" ? "Auto-matching selected…" : "Auto-matching library…");
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
    } catch (e: any) {
      flash(`Auto-match failed: ${e?.message ?? e}`);
    } finally {
      setBusy(null);
    }
  };

  const runPruneMissing = async () => {
    if (!props.library) return;
    setBusy("Scanning for missing folders…");
    try {
      const dry = await api.items.prune({ library: props.library, dry_run: true });
      if (dry.missing === 0) {
        flash(`Nothing to prune — all ${dry.checked} folder(s) still exist on disk.`);
        return;
      }
      const preview = dry.items
        .slice(0, 10)
        .map((i) => `• ${i.title ?? i.folder_path}`)
        .join("\n");
      const more = dry.missing > 10 ? `\n… and ${dry.missing - 10} more` : "";
      const ok = await confirmDlg({
        title: `Forget ${dry.missing} missing folder(s)?`,
        message:
          `These folders are tracked in the database but no longer exist on disk:\n\n${preview}${more}\n\nForget all of them? (No files are deleted.)`,
        confirmLabel: "Forget",
        tone: "danger",
      });
      if (!ok) return;
      const res = await api.items.prune({ library: props.library, dry_run: false });
      flash(`Pruned ${res.removed} missing folder(s)`);
      qc.invalidateQueries({ queryKey: ["items"] });
    } catch (e: any) {
      flash(`Prune failed: ${e?.message ?? e}`);
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
          `No empty folders — every tracked folder in "${props.library}" contains at least one media file.`
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
    } catch (e: any) {
      flash(`Prune empty failed: ${e?.message ?? e}`);
    } finally {
      setBusy(null);
    }
  };

  const runRemoveSelected = async () => {
    if (!someSelected) return;
    const ok = await confirmDlg({
      title: `Remove ${selected.size} item(s) from the library?`,
      message:
        `This only forgets them in the database — no files are deleted on disk.`,
      confirmLabel: "Remove",
      tone: "danger",
    });
    if (!ok) return;
    setBusy(`Removing ${selected.size} item(s)…`);
    try {
      let removed = 0;
      for (const p of selectedPaths) {
        try {
          const r = await api.items.remove(p);
          removed += r.removed;
        } catch {
          /* keep going */
        }
      }
      flash(`Removed ${removed} item(s) from library`);
      clearSelection();
      qc.invalidateQueries({ queryKey: ["items"] });
    } finally {
      setBusy(null);
    }
  };

  const runBuild = async (scope: "selected" | "library") => {
    if (!props.library) return;
    setBusy(scope === "selected" ? "Queuing builds…" : "Queuing library builds…");
    try {
      const body =
        scope === "selected"
          ? { folder_paths: selectedPaths }
          : { library: props.library, only_unbuilt: true };
      const res = await api.buildBulk(body);
      flash(`Queued ${res.queued} build job(s) — see Jobs tab`);
      clearSelection();
    } catch (e: any) {
      flash(`Build failed: ${e?.message ?? e}`);
    } finally {
      setBusy(null);
    }
  };

  if (!props.library) {
    return (
      <div className="p-8 text-slate-400">
        Pick a library from the sidebar. New folders under <code>/media</code> are auto-detected.
      </div>
    );
  }

  const btnBase =
    "text-sm px-3 py-1.5 rounded-md font-medium transition disabled:opacity-40 disabled:cursor-not-allowed";
  const btnPrimary = `${btnBase} bg-indigo-600 hover:bg-indigo-500 text-white`;
  const btnAccent = `${btnBase} bg-emerald-600 hover:bg-emerald-500 text-white`;
  const btnSecondary = `${btnBase} bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700`;
  const btnGhost = `${btnBase} bg-transparent hover:bg-slate-800 text-slate-300 border border-slate-800`;
  const btnDanger = `${btnBase} bg-rose-900/40 hover:bg-rose-900/70 text-rose-200 border border-rose-800`;

  // Hazard-yellow buttons for the Danger Zone. Black text on amber-400 with a
  // chunkier border so they read as "don't press unless you mean it".
  const btnHazard = `${btnBase} bg-amber-400 hover:bg-amber-300 text-black border-2 border-amber-500 shadow-sm`;
  const btnHazardOutline = `${btnBase} bg-transparent hover:bg-amber-400/10 text-amber-300 border-2 border-amber-500`;

  return (
    <div className="p-6 max-w-[1600px] mx-auto">
      {/* Library header */}
      <div className="flex flex-wrap items-baseline gap-3 mb-2">
        <h2 className="text-2xl font-semibold tracking-tight">{props.library}</h2>
        <span className="text-sm text-slate-500">{items.length} items</span>
        {busy && <span className="text-xs text-amber-400 ml-auto">{busy}</span>}
      </div>

      {/* Action toolbar — sticky */}
      <div className="sticky top-0 z-20 -mx-6 px-6 py-3 mb-5 bg-slate-950/95 backdrop-blur supports-[backdrop-filter]:bg-slate-950/80 border-b border-slate-800">
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 text-sm text-slate-300 px-3 py-1.5 rounded-md bg-slate-900 border border-slate-800 cursor-pointer hover:border-slate-700">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={toggleAll}
              className="accent-indigo-500"
            />
            Select all
            {someSelected && (
              <span className="text-xs bg-indigo-600 text-white rounded-full px-2 py-0.5 leading-none">
                {selected.size}
              </span>
            )}
          </label>

          {/* Status filter pill — v0.11.4 */}
          <div className="flex bg-slate-900 border border-slate-800 rounded-md p-0.5">
            {(
              [
                { key: "all", label: "All" },
                { key: "needs", label: "Needs work" },
                { key: "complete", label: "Complete" },
              ] as const
            ).map((f) => (
              <button
                key={f.key}
                onClick={() => setFilterPersisted(f.key)}
                className={`px-2.5 py-1 text-xs rounded transition ${
                  filter === f.key
                    ? "bg-indigo-600 text-white"
                    : "text-slate-400 hover:text-white hover:bg-slate-800"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>

          {someSelected ? (
            <>
              <button
                disabled={!!busy}
                onClick={() => runAutoMatch("selected")}
                className={btnSecondary}
              >
                Auto-match
              </button>
              <button
                disabled={!!busy}
                onClick={() => runBuild("selected")}
                className={btnPrimary}
              >
                Build
              </button>
              <button
                disabled={!!busy}
                onClick={runRemoveSelected}
                className={btnDanger}
                title="Forget selected items in the database. Files on disk are not touched."
              >
                Remove
              </button>
            </>
          ) : (
            <span className="text-xs text-slate-500">Select shows to act on them</span>
          )}

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <button
              disabled={!!busy}
              onClick={() => runAutoMatch("library")}
              className={btnGhost}
              title="Run auto-match on all unmatched items in this library"
            >
              Auto-match all
            </button>
            <button
              disabled={!!busy}
              onClick={() => runBuild("library")}
              className={btnAccent}
              title="Queue builds for everything in this library that is not already complete"
            >
              Build all
            </button>
            <button
              disabled={!!busy}
              onClick={runPruneMissing}
              className={btnGhost}
              title="Find folders tracked in the database that no longer exist on disk and remove them."
            >
              Prune missing
            </button>
            <button
              disabled={!!busy}
              onClick={runPruneEmpty}
              className={btnHazardOutline}
              title="Find folders that exist on disk but contain no video files (e.g. only NFOs + posters), preview them, and prune. Folders that contain media are never touched."
            >
              ⚠ Prune empty
            </button>
            <button
              className={btnGhost}
              onClick={async () => {
                await api.libraries.scan(props.library!);
                setTimeout(() => qc.invalidateQueries({ queryKey: ["items"] }), 500);
              }}
            >
              {isFetching ? "scanning…" : "Scan"}
            </button>
          </div>
        </div>
      </div>
      {toast && (
        <div className="mb-4 text-sm px-3 py-2 rounded-md bg-slate-900 border border-indigo-800 text-indigo-200">
          {toast}
        </div>
      )}
      {props.viewMode === "grid" ? (
        <Grid
          items={items}
          selected={selected}
          onToggle={toggle}
          onOpen={props.onOpenDetail}
        />
      ) : (
        <List
          items={items}
          selected={selected}
          onToggle={toggle}
          allSelected={allSelected}
          onToggleAll={toggleAll}
          onOpen={props.onOpenDetail}
        />
      )}
      <DangerZone
        library={props.library}
        busy={busy}
        setBusy={setBusy}
        flash={flash}
        invalidateItems={() => qc.invalidateQueries({ queryKey: ["items"] })}
        btnHazard={btnHazard}
        btnHazardOutline={btnHazardOutline}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Danger Zone
// ---------------------------------------------------------------------------
// Two library-wide "big yellow buttons" for emergencies:
//   1. Wipe ALL NFOs + artwork in this library
//   2. Blast every .plex-nfo-builder.json sidecar in this library
// Both run a dry-run preview first, show a count, and require explicit
// confirmation before touching disk. Collapsed by default so they don't
// scream at the user every time they open a library.

function DangerZone(props: {
  library: string;
  busy: string | null;
  setBusy: (v: string | null) => void;
  flash: (msg: string) => void;
  invalidateItems: () => void;
  btnHazard: string;
  btnHazardOutline: string;
}) {
  const [open, setOpen] = useState(false);
  const confirmDlg = useConfirm();

  const runWipeNfo = async () => {
    if (props.busy) return;
    props.setBusy("Scanning library for NFOs and artwork…");
    let preview: { folder_count: number; file_count?: number };
    try {
      preview = await api.libraries.wipeNfo(props.library, { dry_run: true });
    } catch (e: any) {
      props.flash(`Wipe preview failed: ${e?.message ?? e}`);
      props.setBusy(null);
      return;
    }
    props.setBusy(null);
    if (!preview.file_count) {
      props.flash(
        `Nothing to wipe in "${props.library}" — checked ${preview.folder_count} folder(s).`
      );
      return;
    }
    const ok = await confirmDlg({
      title: `Wipe NFOs + artwork across “${props.library}”?`,
      message:
        `This will delete ${preview.file_count} file(s) across ` +
        `${preview.folder_count} folder(s):\n` +
        `  • Every tvshow.nfo / movie .nfo / episode .nfo / season.nfo\n` +
        `  • Every poster.jpg / background.jpg / banner.jpg / clearlogo.png\n` +
        `  • Every Season<NN>-poster.jpg / season-specials-poster.jpg\n` +
        `  • Every <episode>-thumb.jpg next to a video file\n\n` +
        `Sidecars (.plex-nfo-builder.json) and your media files are NOT touched. ` +
        `Bindings + overrides survive — you can rebuild straight after.\n\n` +
        `This cannot be undone.`,
      confirmLabel: "Wipe",
      tone: "danger",
    });
    if (!ok) return;
    props.setBusy(`Wiping NFOs + artwork from ${preview.folder_count} folder(s)…`);
    try {
      const res = await api.libraries.wipeNfo(props.library, { dry_run: false });
      props.flash(
        `Wiped ${res.nfo_deleted ?? 0} NFO(s) + ${res.artwork_deleted ?? 0} artwork file(s) ` +
          `across ${res.folder_count} folder(s)` +
          (res.failed && res.failed.length ? ` — ${res.failed.length} folder(s) failed` : "")
      );
      props.invalidateItems();
    } catch (e: any) {
      props.flash(`Wipe failed: ${e?.message ?? e}`);
    } finally {
      props.setBusy(null);
    }
  };

  const runWipeSidecars = async () => {
    if (props.busy) return;
    props.setBusy("Scanning library for sidecar files…");
    let preview: { sidecar_count: number; files?: string[] };
    try {
      preview = await api.libraries.wipeSidecars(props.library, { dry_run: true });
    } catch (e: any) {
      props.flash(`Sidecar preview failed: ${e?.message ?? e}`);
      props.setBusy(null);
      return;
    }
    props.setBusy(null);
    if (!preview.sidecar_count) {
      props.flash(`No .plex-nfo-builder.json sidecars found in "${props.library}".`);
      return;
    }
    const ok = await confirmDlg({
      title: `Blast every sidecar in “${props.library}”?`,
      message:
        `Found ${preview.sidecar_count} .plex-nfo-builder.json sidecar file(s) to delete.\n\n` +
        `The sidecar is the only on-disk record of bindings + overrides for ` +
        `each folder. After wiping them, the database still remembers everything, ` +
        `but if you ever wipe the database too you'll have to re-bind from scratch.\n\n` +
        `NFOs and artwork are NOT touched.\n\n` +
        `This cannot be undone.`,
      confirmLabel: "Blast sidecars",
      tone: "danger",
    });
    if (!ok) return;
    props.setBusy(`Deleting ${preview.sidecar_count} sidecar file(s)…`);
    try {
      const res = await api.libraries.wipeSidecars(props.library, { dry_run: false });
      props.flash(
        `Deleted ${res.deleted?.length ?? 0} sidecar file(s)` +
          (res.failed && res.failed.length ? ` — ${res.failed.length} failed` : "")
      );
    } catch (e: any) {
      props.flash(`Sidecar wipe failed: ${e?.message ?? e}`);
    } finally {
      props.setBusy(null);
    }
  };

  return (
    <div className="mt-6 rounded-lg border-2 border-amber-500/60 bg-amber-500/[0.04]">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-3 px-4 py-2.5 text-left"
      >
        <div className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-flex items-center justify-center w-6 h-6 rounded bg-amber-400 text-black text-xs font-black"
            title="Hazard"
          >
            ⚠
          </span>
          <span className="text-sm font-semibold text-amber-200">
            Danger zone
          </span>
          <span className="text-xs text-amber-300/70">
            library-wide destructive operations
          </span>
        </div>
        <span className="text-xs text-amber-300/80">{open ? "hide" : "show"}</span>
      </button>
      {open && (
        <div className="px-4 pb-4 pt-1 border-t border-amber-500/30 space-y-3">
          <p className="text-xs text-amber-200/80 leading-relaxed">
            These actions touch every folder tracked under{" "}
            <span className="font-mono text-amber-100">{props.library}</span>. Each
            one shows you exactly what it will delete and asks for confirmation
            before touching disk. Don't press unless you're sure.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={runWipeNfo}
              disabled={!!props.busy}
              className={props.btnHazard}
              title="Delete every generated NFO and artwork file across this whole library. Bindings survive via the sidecar."
            >
              ⚠ Wipe ALL NFOs + artwork
            </button>
            <button
              onClick={runWipeSidecars}
              disabled={!!props.busy}
              className={props.btnHazardOutline}
              title="Delete every .plex-nfo-builder.json sidecar in this library. Database is untouched."
            >
              ⚠ Blast every sidecar (.plex-nfo-builder.json)
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Grid({
  items,
  selected,
  onToggle,
  onOpen,
}: {
  items: Item[];
  selected: Set<string>;
  onToggle: (p: string) => void;
  onOpen: (p: string) => void;
}) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-5">
      {items.map((it) => (
        <Tile
          key={it.folder_path}
          item={it}
          checked={selected.has(it.folder_path)}
          onToggle={onToggle}
          onOpen={onOpen}
        />
      ))}
    </div>
  );
}

function Tile({
  item,
  checked,
  onToggle,
  onOpen,
}: {
  item: Item;
  checked: boolean;
  onToggle: (p: string) => void;
  onOpen: (p: string) => void;
}) {
  const poster = item.poster_path
    ? `${api.artwork.fileUrl(item.poster_path)}&t=${item.last_built ?? 0}`
    : null;
  return (
    <div
      className={`group relative rounded-lg overflow-hidden transition ${
        checked
          ? "ring-2 ring-indigo-500 ring-offset-2 ring-offset-slate-950"
          : ""
      }`}
    >
      {/* Selection checkbox — only visible on hover or when selected */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onToggle(item.folder_path);
        }}
        className={`absolute top-2 left-2 z-20 w-6 h-6 rounded-md flex items-center justify-center transition ${
          checked
            ? "bg-indigo-600 text-white opacity-100"
            : "bg-black/70 text-transparent opacity-0 group-hover:opacity-100 hover:bg-black/90 hover:text-slate-300 border border-white/20"
        }`}
        aria-label={checked ? "Deselect" : "Select"}
      >
        {checked ? (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        ) : (
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        )}
      </button>

      {/* Status badge — top right */}
      {item.nfo_status && item.nfo_status !== "none" && (
        <span
          className={`absolute top-2 right-2 z-10 text-[10px] font-medium px-1.5 py-0.5 rounded uppercase tracking-wide ${
            STATUS_COLOR[item.nfo_status] ?? "bg-slate-700"
          }`}
        >
          {item.nfo_status}
        </span>
      )}

      <button
        onClick={() => onOpen(item.folder_path)}
        className="w-full text-left block"
      >
        <div className="aspect-[2/3] bg-slate-900 flex items-center justify-center text-slate-600 overflow-hidden rounded-lg border border-slate-800 group-hover:border-slate-700 transition">
          {poster ? (
            <img
              src={poster}
              alt={item.title}
              loading="lazy"
              className="w-full h-full object-cover group-hover:scale-[1.03] transition-transform duration-300"
            />
          ) : (
            <div className="px-3 text-center">
              <div className="text-[10px] uppercase tracking-wider text-slate-600 mb-1">No poster</div>
              <div className="text-xs text-slate-400 line-clamp-3">{item.title}</div>
            </div>
          )}
        </div>
        <div className="pt-2 px-0.5">
          <div className="text-sm truncate font-medium text-slate-200">{item.title}</div>
          <div className="text-xs text-slate-500 mt-0.5">{item.year ?? "—"}</div>
        </div>
      </button>
    </div>
  );
}

function List({
  items,
  selected,
  onToggle,
  allSelected,
  onToggleAll,
  onOpen,
}: {
  items: Item[];
  selected: Set<string>;
  onToggle: (p: string) => void;
  allSelected: boolean;
  onToggleAll: () => void;
  onOpen: (p: string) => void;
}) {
  return (
    <div className="border border-slate-800 rounded overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-slate-900 text-slate-400 text-left">
          <tr>
            <th className="p-2 w-8">
              <input type="checkbox" checked={allSelected} onChange={onToggleAll} />
            </th>
            <th className="p-2">Title</th>
            <th>Year</th>
            <th>ID</th>
            <th>Episodes</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((it) => (
            <tr
              key={it.folder_path}
              className={`border-t border-slate-800 hover:bg-slate-900 ${
                selected.has(it.folder_path) ? "bg-slate-900" : ""
              }`}
            >
              <td className="p-2">
                <input
                  type="checkbox"
                  checked={selected.has(it.folder_path)}
                  onChange={() => onToggle(it.folder_path)}
                />
              </td>
              <td className="p-2">{it.title}</td>
              <td>{it.year ?? ""}</td>
              <td className="text-xs text-slate-500">
                {it.provider}-{it.external_id}
              </td>
              <td className="text-xs">{it.episode_count_local ?? ""}</td>
              <td>
                {it.nfo_status && (
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded ${
                      STATUS_COLOR[it.nfo_status] ?? "bg-slate-700"
                    }`}
                  >
                    {it.nfo_status}
                  </span>
                )}
              </td>
              <td className="text-right">
                <button onClick={() => onOpen(it.folder_path)} className="text-indigo-400 text-xs">
                  open
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
