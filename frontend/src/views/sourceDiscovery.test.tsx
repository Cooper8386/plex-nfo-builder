import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { ConfirmContext } from "../components/confirm";
import DetailView from "./DetailView";
import { SecondarySourcePanel } from "./SourcePanels";

function mount(children: React.ReactNode) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ConfirmContext.Provider value={{ confirm: async () => true, prompt: async () => null }}>
      {children}
    </ConfirmContext.Provider>
  </QueryClientProvider>);
}

it("auto-matches one media item without starting a build", async () => {
  vi.spyOn(api.items, "detail").mockResolvedValue({
    path: "/fixture", state: { title: "Example", kind: "movie" }, binding: null,
    artwork_files: [], tags: { tvdb: [], tmdb: [], custom: [] }, metadata_source: "tmdb",
    library_kind: "movies", overrides: {}, provider_episode_count: null, provider_used: null,
  });
  vi.spyOn(api, "health").mockResolvedValue({ ok: true, tvdb_configured: false });
  const build = vi.spyOn(api, "build");
  const match = vi.spyOn(api.match, "autoBulk").mockResolvedValue({
    ok: true, total: 1, matched: 1, results: [{ matched: true }],
  });
  mount(<DetailView path="/fixture" onBack={() => {}} />);
  expect(await screen.findByLabelText("Metadata provider")).toHaveValue("tmdb");
  await userEvent.click(screen.getByRole("button", { name: "Auto-match only" }));
  expect(await screen.findByText("Source matched. No NFOs or artwork built.")).toBeVisible();
  expect(match).toHaveBeenCalledWith({ folder_paths: ["/fixture"] });
  expect(build).not.toHaveBeenCalled();
});

describe("secondary discovery", () => {
  it.each(["tvdb", "tmdb"] as const)("discovers the counterpart for %s on open", async (primaryProvider) => {
    const onChanged = vi.fn();
    const discover = vi.spyOn(api.match, "discoverSecondary").mockResolvedValue({
      ok: true, found: true, secondary_provider: primaryProvider === "tvdb" ? "tmdb" : "tvdb",
      secondary_external_id: "42",
    });
    mount(<SecondarySourcePanel path="/fixture" kind="series" primaryProvider={primaryProvider}
      secondaryProvider={null} secondaryExternalId={null} onChanged={onChanged} />);
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(discover).toHaveBeenCalledOnce();
    expect(discover).toHaveBeenCalledWith("/fixture");
  });

  it("keeps an existing manual secondary link", () => {
    const discover = vi.spyOn(api.match, "discoverSecondary");
    mount(<SecondarySourcePanel path="/fixture" kind="series" primaryProvider="tvdb"
      secondaryProvider="tmdb" secondaryExternalId="42" onChanged={() => {}} />);
    expect(screen.getByText("tmdb-42")).toBeVisible();
    expect(discover).not.toHaveBeenCalled();
  });

  it("shows a failed lookup and supports retry", async () => {
    const discover = vi.spyOn(api.match, "discoverSecondary")
      .mockRejectedValueOnce(new Error("Provider unavailable"))
      .mockResolvedValueOnce({ ok: true, found: false, secondary_provider: null, secondary_external_id: null });
    mount(<SecondarySourcePanel path="/fixture" kind="movie" primaryProvider="tmdb"
      secondaryProvider={null} secondaryExternalId={null} onChanged={() => {}} />);
    expect(await screen.findByText(/Discovery failed: Provider unavailable/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Discover source" }));
    expect(await screen.findByText(/No exact TVDB link found/)).toBeVisible();
    expect(discover).toHaveBeenCalledTimes(2);
  });
});
