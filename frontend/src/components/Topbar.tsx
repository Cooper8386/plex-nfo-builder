import { useEffect, useState } from "react";
import { ViewMode } from "../App";
import { api } from "../lib/api";

export default function Topbar(props: {
  viewMode: ViewMode;
  setViewMode: (m: ViewMode) => void;
  search: string;
  setSearch: (s: string) => void;
  onNav: (r: any) => void;
  route: string;
  showLibraryControls?: boolean;
}) {
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .version()
      .then((v) => {
        if (alive) setVersion(v.version || null);
      })
      .catch(() => {
        // ignore — chip just won't render
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="sticky top-0 z-30 flex items-center gap-3 px-4 h-14 border-b border-slate-800 bg-slate-950/95 backdrop-blur supports-[backdrop-filter]:bg-slate-950/80">
      <h1 className="font-bold text-base tracking-tight">
        <span className="text-indigo-400">Plex</span>{" "}
        <span className="text-slate-200">NFO</span>
      </h1>
      {version && (
        <span
          className="px-1.5 py-0.5 text-[10px] font-mono uppercase tracking-wider rounded border border-slate-800 bg-slate-900 text-slate-400 select-none"
          title={`Backend version ${version} — useful when running the :latest Docker tag.`}
        >
          v{version}
        </span>
      )}
      <div className="flex bg-slate-900 border border-slate-800 rounded-md p-0.5">
        {(["library", "jobs", "logs", "watcher", "settings", "help"] as const).map((r) => (
          <button
            key={r}
            onClick={() => props.onNav(r)}
            className={`px-3 py-1.5 text-sm capitalize rounded transition ${
              props.route === r
                ? "bg-indigo-600 text-white"
                : "text-slate-400 hover:text-white hover:bg-slate-800"
            }`}
          >
            {r}
          </button>
        ))}
      </div>
      <div className="flex-1" />
      {props.showLibraryControls && (
        <>
          <div className="relative">
            <svg
              className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 pointer-events-none"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              value={props.search}
              onChange={(e) => props.setSearch(e.target.value)}
              placeholder="Search title"
              className="bg-slate-900 border border-slate-800 pl-8 pr-3 py-1.5 rounded-md text-sm w-56 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
            />
          </div>
          <div className="flex bg-slate-900 border border-slate-800 rounded-md p-0.5">
            {(["grid", "list"] as ViewMode[]).map((m) => (
              <button
                key={m}
                onClick={() => props.setViewMode(m)}
                className={`px-2.5 py-1.5 text-xs capitalize rounded transition ${
                  props.viewMode === m
                    ? "bg-indigo-600 text-white"
                    : "text-slate-400 hover:text-white hover:bg-slate-800"
                }`}
              >
                {m}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
