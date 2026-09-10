import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { signOut } from "../lib/auth";
export type NavPage =
  | "library"
  | "jobs"
  | "logs"
  | "watcher"
  | "settings"
  | "help";

export default function Topbar({
  onNav,
  route,
}: {
  onNav: (route: NavPage) => void;
  route: NavPage;
}) {
  const { data } = useQuery({
    queryKey: ["version"],
    queryFn: api.version,
    staleTime: Infinity,
  });
  return (
    <header className="app-topbar">
      <button
        className="brand text-left"
        onClick={() => onNav("library")}
        aria-label="Plex NFO Builder home"
      >
        <span className="brand-mark" aria-hidden="true">
          N
        </span>
        <span>
          <span className="block text-sm font-semibold tracking-tight">
            Plex <span className="text-indigo-300">NFO</span> Builder
          </span>
          <span className="block text-[9px] uppercase tracking-[.16em] text-slate-500 mt-0.5">
            Your media. Your metadata.
          </span>
        </span>
      </button>
      <nav aria-label="Main navigation" className="top-nav">
        {(
          [
            ["library", "Library"],
            ["jobs", "Activity"],
            ["watcher", "Automation"],
            ["logs", "Logs"],
            ["settings", "Settings"],
            ["help", "Help"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => onNav(key)}
            aria-current={route === key ? "page" : undefined}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-3">
        {data?.version && (
          <span
            className="text-[10px] font-mono text-slate-500"
            title="Running backend version"
          >
            v{data.version}
          </span>
        )}
        <button className="btn btn-ghost" onClick={signOut}>
          Sign out
        </button>
      </div>
    </header>
  );
}
