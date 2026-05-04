import { useEffect, useState, useCallback } from "react";
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

export default function App() {
  const [route, setRouteState] = useState<Route>(() => parseLocation());
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [hideOrganized, setHideOrganized] = useState(false);
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

  // popstate listener so the mouse back/forward buttons work natively.
  useEffect(() => {
    const onPop = () => setRouteState(parseLocation());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback((r: Route, replace = false) => {
    const url = routeToUrl(r);
    if (window.location.pathname + window.location.search !== url) {
      if (replace) window.history.replaceState({}, "", url);
      else window.history.pushState({}, "", url);
    }
    setRouteState(r);
  }, []);

  // First-load: detect libraries, but don't replace the URL if we are already on one.
  useEffect(() => {
    api.libraries.detect().catch(() => {});
  }, []);

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
        statusFilter={statusFilter}
        setStatusFilter={setStatusFilter}
        hideOrganized={hideOrganized}
        setHideOrganized={setHideOrganized}
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
        <main className="flex-1 min-h-0 overflow-auto">
          {(route.name === "library" || route.name === "home") && (
            <LibraryView
              library={route.name === "library" ? route.library : null}
              viewMode={viewMode}
              statusFilter={statusFilter}
              hideOrganized={hideOrganized}
              search={search}
              onOpenDetail={(p) =>
                navigate({
                  name: "detail",
                  library: route.name === "library" ? route.library : "",
                  path: p,
                })
              }
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
