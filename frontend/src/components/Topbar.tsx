import { ViewMode } from "../App";

const STATUSES = ["none", "partial", "complete", "stale", "foreign", "mixed"];

export default function Topbar(props: {
  viewMode: ViewMode;
  setViewMode: (m: ViewMode) => void;
  statusFilter: string[];
  setStatusFilter: (s: string[]) => void;
  hideOrganized: boolean;
  setHideOrganized: (b: boolean) => void;
  search: string;
  setSearch: (s: string) => void;
  onNav: (r: any) => void;
  route: string;
  showLibraryControls?: boolean;
}) {
  const toggle = (s: string) =>
    props.setStatusFilter(
      props.statusFilter.includes(s)
        ? props.statusFilter.filter((x) => x !== s)
        : [...props.statusFilter, s]
    );

  return (
    <div className="sticky top-0 z-30 flex items-center gap-3 px-4 h-14 border-b border-slate-800 bg-slate-950/95 backdrop-blur supports-[backdrop-filter]:bg-slate-950/80">
      <h1 className="font-bold text-base tracking-tight">
        <span className="text-indigo-400">Plex</span>{" "}
        <span className="text-slate-200">NFO</span>
      </h1>
      <div className="flex bg-slate-900 border border-slate-800 rounded-md p-0.5">
        {(["library", "jobs", "logs", "settings", "help"] as const).map((r) => (
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
          <details className="relative">
            <summary
              className="list-none cursor-pointer select-none px-3 py-1.5 text-sm bg-slate-900 border border-slate-800 rounded-md text-slate-300 hover:text-white hover:border-slate-700 inline-flex items-center gap-2"
            >
              Filters
              {(props.statusFilter.length > 0 || props.hideOrganized) && (
                <span className="text-[10px] bg-indigo-600 text-white rounded-full px-1.5 py-0.5 leading-none">
                  {props.statusFilter.length + (props.hideOrganized ? 1 : 0)}
                </span>
              )}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </summary>
            <div className="absolute right-0 mt-2 w-64 p-3 bg-slate-900 border border-slate-800 rounded-md shadow-xl z-20 space-y-3">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1.5">
                  NFO status
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {STATUSES.map((s) => (
                    <button
                      key={s}
                      onClick={() => toggle(s)}
                      className={`text-xs px-2 py-1 rounded border transition ${
                        props.statusFilter.includes(s)
                          ? "bg-indigo-600 border-indigo-500 text-white"
                          : "bg-slate-800 border-slate-700 text-slate-300 hover:border-slate-500"
                      }`}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input
                  type="checkbox"
                  checked={props.hideOrganized}
                  onChange={(e) => props.setHideOrganized(e.target.checked)}
                  className="accent-indigo-500"
                />
                Hide organized
              </label>
              {(props.statusFilter.length > 0 || props.hideOrganized) && (
                <button
                  onClick={() => {
                    props.setStatusFilter([]);
                    props.setHideOrganized(false);
                  }}
                  className="text-xs text-slate-400 hover:text-white"
                >
                  Clear all
                </button>
              )}
            </div>
          </details>
        </>
      )}
    </div>
  );
}
