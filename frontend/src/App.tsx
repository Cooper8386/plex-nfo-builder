import { useEffect, useRef, useState, useCallback } from "react";
import Sidebar from "./components/Sidebar";
import Topbar from "./components/Topbar";
import LibraryView from "./views/LibraryView";
import DetailView from "./views/DetailView";
import SettingsView from "./views/SettingsView";
import LogsView from "./views/LogsView";
import JobsView from "./views/JobsView";
import HelpView from "./views/HelpView";
import { api } from "./lib/api";

export type ViewMode = "grid" | "list";
export type Route =
  | { name: "home" }
  | { name: "library"; library: string }
  | { name: "detail"; library: string; path: string }
  | { name: "jobs" }
  | { name: "logs" }
  | { name: "settings" }
  | { name: "help" };

function parseLocation(): Route {
  const path = window.location.pathname || "/";
  const search = new URLSearchParams(window.location.search);
  if (path.startsWith("/library/")) {
    const lib = decodeURIComponent(path.slice("/library/".length).replace(/\/$/, ""));
    if (lib) return { name: "library", library: lib };
  }
  if (path.startsWith("/detail/")) {
    const lib = decodeURIComponent(path.slice("/detail/".length).replace(/\/$/, ""));
    const folder = search.get("path") || "";
    if (lib && folder) return { name: "detail", library: lib, path: folder };
  }
  if (path === "/jobs") return { name: "jobs" };
  if (path === "/logs") return { name: "logs" };
  if (path === "/settings") return { name: "settings" };
  if (path === "/help") return { name: "help" };
  return { name: "home" };
}

function routeToUrl(r: Route): string {
  switch (r.name) {
    case "home":
      return "/";
    case "library":
      return `/library/${encodeURIComponent(r.library)}`;
    case "detail":
      return `/detail/${encodeURIComponent(r.library)}?path=${encodeURIComponent(r.path)}`;
    case "jobs":
      return "/jobs";
    case "logs":
      return "/logs";
    case "settings":
      return "/settings";
    case "help":
      return "/help";
  }
}

// Key the saved scroll position by library route — "home" and per-library use
// "lib:<name>", everything else by route name. The map is bounded to the most
// recent ~10 entries so it doesn't grow forever.
function scrollKeyFor(r: Route): string {
  switch (r.name) {
    case "library":
      return `lib:${r.library}`;
    case "home":
      return "home";
    case "detail":
      // Detail view scroll is irrelevant — we want to restore the *library*
      // scroll when the user backs out, not the detail page itself.
      return `detail:${r.library}:${r.path}`;
    default:
      return r.name;
  }
}

const SCROLL_HISTORY_LIMIT = 10;

export default function App() {
  const [route, setRouteState] = useState<Route>(() => parseLocation());
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [search, setSearch] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem("pnb.sidebarCollapsed") === "1";
    } catch {
      return false;
    }
  });
  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem("pnb.sidebarCollapsed", next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  // The single scroll container for the right pane. LibraryView/DetailView
  // both render inside <main>, so its scrollTop is the source of truth.
  const mainRef = useRef<HTMLElement>(null);

  // v0.11.4 — scroll restore. We snapshot mainRef.scrollTop on every nav-away
  // (both button-driven and via popstate) and restore it once items render.
  const scrollPositionsRef = useRef<Map<string, number>>(new Map());
  // Tracks the route we are *leaving* so popstate can record where we were.
  const prevRouteRef = useRef<Route>(route);

  const captureScrollFor = useCallback((r: Route) => {
    const el = mainRef.current;
    if (!el) return;
    const key = scrollKeyFor(r);
    const map = scrollPositionsRef.current;
    map.set(key, el.scrollTop);
    // Trim oldest entries if we're over budget.
    if (map.size > SCROLL_HISTORY_LIMIT) {
      const overflow = map.size - SCROLL_HISTORY_LIMIT;
      const it = map.keys();
      for (let i = 0; i < overflow; i++) {
        const k = it.next().value as string | undefined;
        if (k !== undefined) map.delete(k);
      }
    }
  }, []);

  const consumeScrollFor = useCallback((r: Route): number | undefined => {
    const key = scrollKeyFor(r);
    const map = scrollPositionsRef.current;
    if (!map.has(key)) return undefined;
    const y = map.get(key);
    map.delete(key);
    return y;
  }, []);

  // popstate listener so the mouse back/forward buttons work natively. Note
  // we capture scroll for the *previous* route (the one we are leaving) before
  // updating state — popstate fires after the URL has already changed, so we
  // rely on prevRouteRef for the "from" route.
  useEffect(() => {
    const onPop = () => {
      captureScrollFor(prevRouteRef.current);
      const next = parseLocation();
      prevRouteRef.current = next;
      setRouteState(next);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [captureScrollFor]);

  const navigate = useCallback(
    (r: Route, replace = false) => {
      // Snapshot the *current* scroll for the route we're leaving before we
      // change anything else.
      captureScrollFor(prevRouteRef.current);
      const url = routeToUrl(r);
      if (window.location.pathname + window.location.search !== url) {
        if (replace) window.history.replaceState({}, "", url);
        else window.history.pushState({}, "", url);
      }
      prevRouteRef.current = r;
      setRouteState(r);
    },
    [captureScrollFor]
  );

  // First-load: detect libraries, but don't replace the URL if we are already on one.
  useEffect(() => {
    api.libraries.detect().catch(() => {});
  }, []);

  // Reset main scroll to top whenever we land on a route that has *no* saved
  // position (e.g. clicking into a detail view, jumping to settings). Library
  // restore is handled separately via onItemsReady so we wait for the grid to
  // mount before scrolling.
  useEffect(() => {
    if (route.name === "library") return;
    const el = mainRef.current;
    if (!el) return;
    const saved = consumeScrollFor(route);
    el.scrollTop = saved ?? 0;
  }, [route, consumeScrollFor]);

  const onLibraryItemsReady = useCallback(() => {
    if (route.name !== "library") return;
    const saved = consumeScrollFor(route);
    if (saved === undefined) return;
    const el = mainRef.current;
    if (!el) return;
    // The grid mounts in this same tick; rAF defers until after layout so
    // the scrollTop assignment isn't clobbered by React's own paint.
    requestAnimationFrame(() => {
      if (mainRef.current) mainRef.current.scrollTop = saved;
    });
  }, [route, consumeScrollFor]);

  const activeLibrary =
    route.name === "library" ? route.library : route.name === "detail" ? route.library : null;
  const topbarRoute =
    route.name === "library" || route.name === "home" || route.name === "detail"
      ? "library"
      : route.name;

  return (
    <div className="h-full flex flex-col">
      <Topbar
        viewMode={viewMode}
        setViewMode={setViewMode}
        search={search}
        setSearch={setSearch}
        onNav={(r) => {
          if (r === "library") {
            navigate(activeLibrary ? { name: "library", library: activeLibrary } : { name: "home" });
          } else if (r === "jobs") navigate({ name: "jobs" });
          else if (r === "logs") navigate({ name: "logs" });
          else if (r === "settings") navigate({ name: "settings" });
          else if (r === "help") navigate({ name: "help" });
        }}
        route={topbarRoute}
        showLibraryControls={route.name === "library" || route.name === "home"}
      />
      <div className="flex flex-1 min-h-0">
        <Sidebar
          activeLibrary={activeLibrary}
          onSelectLibrary={(l) => navigate(l ? { name: "library", library: l } : { name: "home" })}
          collapsed={sidebarCollapsed}
          onToggle={toggleSidebar}
        />
        <main ref={mainRef} className="flex-1 min-h-0 overflow-auto">
          {(route.name === "library" || route.name === "home") && (
            <LibraryView
              library={route.name === "library" ? route.library : null}
              viewMode={viewMode}
              search={search}
              onOpenDetail={(p) =>
                navigate({
                  name: "detail",
                  library: route.name === "library" ? route.library : "",
                  path: p,
                })
              }
              onItemsReady={onLibraryItemsReady}
            />
          )}
          {route.name === "detail" && (
            <DetailView
              path={route.path}
              onBack={() =>
                navigate(
                  route.library
                    ? { name: "library", library: route.library }
                    : { name: "home" }
                )
              }
            />
          )}
          {route.name === "settings" && <SettingsView />}
          {route.name === "logs" && <LogsView />}
          {route.name === "jobs" && <JobsView />}
          {route.name === "help" && <HelpView />}
        </main>
      </div>
    </div>
  );
}
