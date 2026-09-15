import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api, type NfoExplain } from "../lib/api";
import { WhyStatusPanel } from "./ItemDiagnostics";

const explain: NfoExplain = {
  path: "/fixture",
  status: "partial",
  kind: "series",
  video_count: 4,
  nfo_count: 1,
  foreign_nfo_count: 0,
  ignored_episode_count: 2,
  ignored_episode_files: [
    "Season 01/Ignored.mkv",
    "Season 01/Missing-elsewhere.mkv",
  ],
  show_nfo: { path: "/fixture/tvshow.nfo", present: true, foreign: false },
  movie_nfo: null,
  seasons: [
    {
      season: 1,
      folder: "/fixture/Season 01",
      video_count: 4,
      nfo_count: 1,
      foreign_nfo_count: 0,
      missing: ["Active.mkv"],
      missing_paths: ["Season 01/Active.mkv"],
      missing_total: 1,
      ignored: ["Ignored.mkv"],
      ignored_paths: ["Season 01/Ignored.mkv"],
      ignored_total: 2,
      foreign: [],
      foreign_total: 0,
      season_nfo: true,
    },
  ],
  orphan_root_videos: [],
  reasons: ["One episode file has no matching .nfo yet."],
};

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <WhyStatusPanel path="/fixture" onClose={() => {}} />
    </QueryClientProvider>,
  );
}

describe("NFO completion ignores", () => {
  it("shows raw coverage and sends folder-relative paths for ignore changes", async () => {
    vi.spyOn(api.items, "nfoExplain").mockResolvedValue(explain);
    const ignore = vi.spyOn(api.items, "nfoIgnore").mockResolvedValue({
      ok: true,
      ignored: ["Season 01/Ignored.mkv"],
      status: "partial",
    });
    const clear = vi.spyOn(api.items, "clearNfoIgnores").mockResolvedValue({
      ok: true,
      ignored: [],
      status: "partial",
    });
    const user = userEvent.setup();
    mount();

    expect(await screen.findByText("1 / 4 · 2 ignored")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "show files" }));
    await user.click(
      screen.getByRole("button", {
        name: "Ignore Active.mkv from completion checks",
      }),
    );
    await waitFor(() =>
      expect(ignore).toHaveBeenCalledWith({
        folder_path: "/fixture",
        file_path: "Season 01/Active.mkv",
        ignored: true,
      }),
    );
    expect(
      screen.getByRole("button", {
        name: "Restore Ignored.mkv to completion checks",
      }),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Clear ignored (2)" }));
    await waitFor(() =>
      expect(clear).toHaveBeenCalledWith({ folder_path: "/fixture" }),
    );
  });

  it("reports ignore failures without leaving controls disabled", async () => {
    vi.spyOn(api.items, "nfoExplain").mockResolvedValue(explain);
    vi.spyOn(api.items, "nfoIgnore").mockRejectedValue(new Error("Read-only"));
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: "show files" }));
    const ignore = screen.getByRole("button", {
      name: "Ignore Active.mkv from completion checks",
    });
    await user.click(ignore);
    expect(await screen.findByRole("alert")).toHaveTextContent("Read-only");
    expect(ignore).toBeEnabled();
  });

  it("keeps stale stored ignores clearable without counting them as active", async () => {
    vi.spyOn(api.items, "nfoExplain").mockResolvedValue({
      ...explain,
      ignored_episode_count: 0,
      ignored_episode_files: ["Season 99/Removed.mkv"],
    });
    const clear = vi.spyOn(api.items, "clearNfoIgnores").mockResolvedValue({
      ok: true,
      ignored: [],
      status: "partial",
    });
    const user = userEvent.setup();
    mount();

    expect(await screen.findByText("1 / 4")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Clear ignored (1)" }));
    await waitFor(() =>
      expect(clear).toHaveBeenCalledWith({ folder_path: "/fixture" }),
    );
  });
});
