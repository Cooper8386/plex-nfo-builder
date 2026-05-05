import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Item } from "../lib/api";
import { ViewMode } from "../App";

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
}) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const { data, isFetching } = useQuery({
    queryKey: ["items", props.library, props.search],
    queryFn: () =>
      api.items.list({
        library: props.library || undefined,
        q: props.search || undefined,
      }),
    enabled: !!props.library,
  });

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
      const ok = window.confirm(
        `Found ${dry.missing} folder(s) tracked in the database but missing on disk:\n\n${preview}${more}\n\nForget all of them? (No files are deleted.)`
      );
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

  const runRemoveSelected = async () => {
    if (!someSelected) return;
    const ok = window.confirm(
      `Remove ${selected.size} item(s) from the library?\n\nThis only forgets them in the database — no files are deleted on disk.`
    );
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
