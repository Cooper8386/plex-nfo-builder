import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

export default function Sidebar(props: {
  activeLibrary: string | null;
  onSelectLibrary: (name: string) => void;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["libraries"],
    queryFn: () => api.libraries.list(),
    refetchInterval: 30_000,
  });
  const libs = data?.libraries ?? [];

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
        {libs.slice(0, 10).map((l) => (
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
        {libs.length === 0 && (
          <div className="text-xs text-slate-500 px-2 py-4">None detected.</div>
        )}
        {libs.map((l) => (
          <button
            key={l.name}
            onClick={() => props.onSelectLibrary(l.name)}
            className={`w-full flex items-center justify-between px-3 py-2 rounded-md text-sm transition ${
              props.activeLibrary === l.name
                ? "bg-indigo-600 text-white shadow-sm"
                : "text-slate-300 hover:bg-slate-900 hover:text-white"
            }`}
          >
            <span className="truncate">{l.name}</span>
            <span
              className={`text-[10px] uppercase tracking-wider ${
                props.activeLibrary === l.name ? "text-indigo-200" : "text-slate-500"
              }`}
            >
              {l.kind}
            </span>
          </button>
        ))}
      </div>
    </aside>
  );
}
