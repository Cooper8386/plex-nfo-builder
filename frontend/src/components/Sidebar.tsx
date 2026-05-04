import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

export default function Sidebar(props: {
  activeLibrary: string | null;
  onSelectLibrary: (name: string | null) => void;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["libraries"],
    queryFn: () => api.libraries.list(),
    refetchInterval: 30_000,
  });
  const all = data?.libraries ?? [];
  const [showDisabled, setShowDisabled] = useState(false);
  const visible = showDisabled ? all : all.filter((l) => Number(l.enabled) === 1);
  const disabledCount = all.filter((l) => Number(l.enabled) !== 1).length;

  // If the active library is no longer visible, deselect it.
  useEffect(() => {
    if (!props.activeLibrary) return;
    const stillThere = visible.some((l) => l.name === props.activeLibrary);
    if (!stillThere) props.onSelectLibrary(null);
  }, [visible, props.activeLibrary]);

  async function setEnabled(name: string, enabled: boolean) {
    await api.libraries.update(name, { enabled });
    qc.invalidateQueries({ queryKey: ["libraries"] });
    qc.invalidateQueries({ queryKey: ["items"] });
  }

  async function removeLib(name: string) {
    const ok = confirm(
      `Remove "${name}" from the app?\n\n` +
        `This forgets every binding, override, and item-state row for the library. ` +
        `Files on disk (NFOs, artwork, .plex-nfo-builder.json sidecars) are NOT touched, ` +
        `so re-detecting will bring everything back from sidecars.\n\n` +
        `If you want to keep it remembered but hidden, use Disable instead.`
    );
    if (!ok) return;
    await api.libraries.remove(name);
    qc.invalidateQueries({ queryKey: ["libraries"] });
    qc.invalidateQueries({ queryKey: ["items"] });
    if (props.activeLibrary === name) props.onSelectLibrary(null);
  }

  if (props.collapsed) {
    return (
      <aside className="w-12 shrink-0 border-r border-slate-800 bg-slate-950 flex flex-col items-center py-3 gap-2">
        <button
          onClick={props.onToggle}
          title="Show libraries"
          className="w-8 h-8 rounded hover:bg-slate-800 flex items-center justify-center text-slate-400 hover:text-white"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
        <div className="w-6 h-px bg-slate-800 my-1" />
        {visible.slice(0, 10).map((l) => (
          <button
            key={l.name}
            onClick={() => props.onSelectLibrary(l.name)}
            title={l.name}
            className={`w-8 h-8 rounded flex items-center justify-center text-xs font-semibold uppercase ${
              props.activeLibrary === l.name
                ? "bg-indigo-600 text-white"
                : "bg-slate-900 text-slate-400 hover:bg-slate-800 hover:text-white"
            }`}
          >
            {l.name.slice(0, 2)}
          </button>
        ))}
      </aside>
    );
  }

  return (
    <aside className="w-60 shrink-0 border-r border-slate-800 bg-slate-950 flex flex-col">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Libraries
        </h2>
        <div className="flex items-center gap-1">
          <button
            className="text-xs text-slate-400 hover:text-indigo-400 px-1.5 py-0.5 rounded hover:bg-slate-800"
            onClick={async () => {
              await api.libraries.detect();
              qc.invalidateQueries({ queryKey: ["libraries"] });
            }}
            title="Detect new libraries in /media"
          >
            rescan
          </button>
          <button
            onClick={props.onToggle}
            title="Collapse sidebar"
            className="w-6 h-6 rounded hover:bg-slate-800 flex items-center justify-center text-slate-500 hover:text-white"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto py-2 px-2 space-y-0.5">
        {visible.length === 0 && (
          <div className="text-xs text-slate-500 px-2 py-4">
            {all.length === 0
              ? "None detected."
              : "All libraries are disabled. Toggle below to show them."}
          </div>
        )}
        {visible.map((l) => {
          const enabled = Number(l.enabled) === 1;
          return (
            <LibraryRow
              key={l.name}
              name={l.name}
              kind={l.kind}
              enabled={enabled}
              active={props.activeLibrary === l.name}
              onSelect={() => props.onSelectLibrary(l.name)}
              onToggleEnabled={() => setEnabled(l.name, !enabled)}
              onRemove={() => removeLib(l.name)}
            />
          );
        })}
      </div>
      {disabledCount > 0 && (
        <div className="border-t border-slate-800 px-4 py-2">
          <label className="text-[11px] text-slate-400 inline-flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={showDisabled}
              onChange={(e) => setShowDisabled(e.target.checked)}
            />
            Show disabled ({disabledCount})
          </label>
        </div>
      )}
    </aside>
  );
}

function LibraryRow({
  name,
  kind,
  enabled,
  active,
  onSelect,
  onToggleEnabled,
  onRemove,
}: {
  name: string;
  kind: string;
  enabled: boolean;
  active: boolean;
  onSelect: () => void;
  onToggleEnabled: () => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={wrapperRef} className="relative group">
      <button
        onClick={onSelect}
        disabled={!enabled}
        className={`w-full flex items-center justify-between pl-3 pr-9 py-2 rounded-md text-sm transition ${
          active
            ? "bg-indigo-600 text-white shadow-sm"
            : enabled
              ? "text-slate-300 hover:bg-slate-900 hover:text-white"
              : "text-slate-500 italic cursor-not-allowed"
        }`}
      >
        <span className="truncate">{name}</span>
        <span
          className={`text-[10px] uppercase tracking-wider ${
            active ? "text-indigo-200" : "text-slate-500"
          }`}
        >
          {enabled ? kind : "off"}
        </span>
      </button>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        title="Library options"
        className={`absolute top-1/2 -translate-y-1/2 right-1 w-7 h-7 rounded flex items-center justify-center text-slate-400 hover:text-white hover:bg-slate-800 ${
          open ? "bg-slate-800 text-white" : "opacity-0 group-hover:opacity-100 focus:opacity-100"
        } ${active ? "text-indigo-100 hover:bg-indigo-700" : ""}`}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <circle cx="5" cy="12" r="1.6" />
          <circle cx="12" cy="12" r="1.6" />
          <circle cx="19" cy="12" r="1.6" />
        </svg>
      </button>
      {open && (
        <div className="absolute right-1 top-full mt-1 z-30 w-44 rounded-md border border-slate-700 bg-slate-900 shadow-lg py-1 text-sm">
          <button
            className="w-full text-left px-3 py-1.5 hover:bg-slate-800"
            onClick={() => {
              setOpen(false);
              onToggleEnabled();
            }}
          >
            {enabled ? "Disable" : "Enable"}
          </button>
          <button
            className="w-full text-left px-3 py-1.5 text-rose-300 hover:bg-rose-900/30"
            onClick={() => {
              setOpen(false);
              onRemove();
            }}
          >
            Remove from app…
          </button>
          <div className="border-t border-slate-800 my-1" />
          <div className="px-3 py-1 text-[10px] text-slate-500">
            Files on disk are never touched.
          </div>
        </div>
      )}
    </div>
  );
}
