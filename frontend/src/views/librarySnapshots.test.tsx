import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, BuildJob, LibrarySnapshots } from "../lib/api";
import { clearToken, setToken } from "../lib/auth";
import { ConfirmProvider } from "../components/ConfirmDialog";
import SettingsView from "./SettingsView";

const empty: LibrarySnapshots = {
  snapshots: [],
  storage_path: "/data/snapshots/library",
  jobs: [],
};
const job: BuildJob = {
  id: "snapshot-job",
  kind: "library_snapshot",
  folder: "/media/TV & Anime",
  status: "running",
  progress: 2,
  total: 5,
  started_at: 1,
  finished_at: null,
  messages: ["Copying sidecars"],
};

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(<QueryClientProvider client={client}><ConfirmProvider><SettingsView /></ConfirmProvider></QueryClientProvider>);
}

beforeEach(() => {
  clearToken();
  localStorage.setItem("pnb.settings.section", "libraries");
  vi.spyOn(api.settings, "get").mockResolvedValue({});
  vi.spyOn(api.libraries, "list").mockResolvedValue({
    libraries: [
      { name: "TV & Anime", kind: "series", enabled: 1, detected_at: 0 },
      { name: "Movies", kind: "movie", enabled: 0, detected_at: 0 },
    ],
  });
});

describe("library snapshots settings", () => {
  it("creates for one library, polls to completion, and offers an authenticated ZIP", async () => {
    setToken("test token");
    const list = vi.spyOn(api.libraries.snapshots, "list")
      .mockResolvedValueOnce(empty)
      .mockResolvedValueOnce({ ...empty, jobs: [job] })
      .mockResolvedValue({
        ...empty,
        jobs: [{ ...job, status: "completed", progress: 5, messages: ["Snapshot saved"] }],
        snapshots: [{
          id: "backup-id",
          filename: "TV-snapshot.zip",
          created_at: "2026-09-11T14:00:00Z",
          file_count: 5,
          size_bytes: 1024,
        }],
      });
    const create = vi.spyOn(api.libraries.snapshots, "create")
      .mockResolvedValue({ job_id: job.id });
    const user = userEvent.setup();
    mount();
    expect(await screen.findByText("No snapshots for TV & Anime yet.")).toBeVisible();
    expect(screen.getByRole("button", { name: /Libraries Snapshots and backups/ })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "All libraries" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create snapshot" }));
    expect(create).toHaveBeenCalledWith("TV & Anime");
    expect(await screen.findByRole("progressbar", { name: "Snapshot progress" })).toHaveAttribute("value", "2");
    expect(screen.getByRole("button", { name: "Snapshot in progress…" })).toBeDisabled();
    const download = await screen.findByRole("link", { name: "Download ZIP TV-snapshot.zip" }, { timeout: 4000 });
    expect(download).toHaveAttribute("href", "/api/libraries/TV%20%26%20Anime/snapshots/backup-id/download?api_token=test%20token");
    expect(download).toHaveAttribute("download", "TV-snapshot.zip");
    expect(list).toHaveBeenCalledTimes(3);
    expect(screen.getByText(/5 files · 1,024 bytes/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Create snapshot" })).toBeEnabled();
  });

  it("switches to a disabled library without retaining another library's jobs or snapshots", async () => {
    const list = vi.spyOn(api.libraries.snapshots, "list").mockImplementation(async (library) => (
      library === "TV & Anime" ? { ...empty, jobs: [job] } : empty
    ));
    const create = vi.spyOn(api.libraries.snapshots, "create").mockResolvedValue({ job_id: "movie-job" });
    const user = userEvent.setup();
    mount();
    await screen.findByText("Copying sidecars");
    await user.selectOptions(screen.getByRole("combobox", { name: "Library" }), "Movies");
    expect(await screen.findByText("No snapshots for Movies yet.")).toBeVisible();
    expect(screen.queryByText("Copying sidecars")).not.toBeInTheDocument();
    expect(list).toHaveBeenCalledWith("Movies");
    await user.click(screen.getByRole("button", { name: "Create snapshot" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith("Movies"));
  });

  it("shows retrieval, creation, and background errors without reporting success", async () => {
    vi.spyOn(api.libraries.snapshots, "list")
      .mockRejectedValueOnce(new Error("Storage unavailable"))
      .mockResolvedValue({ ...empty, jobs: [{ ...job, status: "error", messages: ["File changed during snapshot"] }] });
    vi.spyOn(api.libraries.snapshots, "create").mockRejectedValue(new Error("Library is busy"));
    const user = userEvent.setup();
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("Storage unavailable");
    expect(screen.getByRole("button", { name: "Create snapshot" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("File changed during snapshot")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Create snapshot" }));
    expect(await screen.findByText("Snapshot could not start: Library is busy")).toBeVisible();
    expect(screen.queryByRole("link", { name: /Download ZIP/ })).not.toBeInTheDocument();
  });

  it("shows an empty library state and does not request a whole-folder snapshot", async () => {
    vi.mocked(api.libraries.list).mockResolvedValue({ libraries: [] });
    const list = vi.spyOn(api.libraries.snapshots, "list");
    mount();
    expect(await screen.findByText(/No libraries found/)).toBeVisible();
    expect(list).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Create snapshot" })).not.toBeInTheDocument();
  });

  it("shows a temporary watcher pause without changing its enabled setting", async () => {
    localStorage.setItem("pnb.settings.section", "watcher");
    vi.spyOn(api.watcher, "status").mockResolvedValue({
      available: true, enabled: true, running: true, paused_for_snapshot: true,
      debounce_seconds: 30, watched_paths: ["/media/TV"], pending_count: 2, in_flight_count: 0,
    });
    mount();
    expect(await screen.findByText("Paused for library snapshot")).toBeVisible();
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(screen.queryByText("Running")).not.toBeInTheDocument();
  });

  it("blocks manual scheduled runs during a snapshot while keeping enabled settings", async () => {
    localStorage.setItem("pnb.settings.section", "schedules");
    vi.spyOn(api.schedules, "list").mockResolvedValue({
      paused_for_snapshot: true,
      schedules: [{
        id: 1, library: "TV & Anime", cron: "0 3 * * *", action: "full", enabled: 1,
        last_run: null, last_status: null, last_message: null, created_at: 0, updated_at: 0,
      }],
    });
    mount();
    expect(await screen.findByText(/Paused for library snapshot/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Run now" })).toBeDisabled();
    for (const checkbox of screen.getAllByRole("checkbox", { name: "Enabled" })) {
      expect(checkbox).toBeChecked();
    }
  });
});
