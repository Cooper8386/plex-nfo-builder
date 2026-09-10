import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfirmContext } from "../components/confirm";
import { api, RenamePlanItem } from "../lib/api";
import RenameModal from "./RenameModal";
import SettingsView from "./SettingsView";
import OverridesTab from "./OverridesTab";
import EpisodeMapper from "./EpisodeMapper";
import DetailView from "./DetailView";
import { MatchPanel } from "./SourcePanels";
import { settingsPatch, Settings } from "./settingsModel";

const confirm = vi.fn(async () => true);
function mount(children: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ConfirmContext.Provider value={{ confirm, prompt: async () => null }}>
        {children}
      </ConfirmContext.Provider>
    </QueryClientProvider>,
  );
  return client;
}
beforeEach(() => {
  confirm.mockClear();
});

const plan: RenamePlanItem = {
  src: "/fixture/old.mkv",
  dst: "/fixture/new.mkv",
  src_name: "old.mkv",
  dst_name: "new.mkv",
  season: 1,
  episode: 1,
  matched_title: "Pilot",
  conflict: null,
  unchanged: false,
};

describe("rename review", () => {
  it("requires a current preview and sends the approved destination", async () => {
    const preview = vi.spyOn(api.episodes.rename, "preview").mockResolvedValue({
      folder_path: "/fixture",
      template: "default",
      items: [plan],
    });
    const apply = vi
      .spyOn(api.episodes.rename, "apply")
      .mockResolvedValue({ ok: true, renamed: [], skipped: [], failed: [] });
    const user = userEvent.setup();
    mount(
      <RenameModal
        path="/fixture"
        onClose={() => {}}
        onApplied={async () => {}}
      />,
    );
    expect(preview).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Generate preview" }));
    await screen.findByText("new.mkv");
    await user.type(
      screen.getByRole("textbox", { name: "Template override" }),
      "custom",
    );
    expect(
      screen.getByRole("button", { name: "Rename 1 files…" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Generate preview" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Rename 1 files…" }),
      ).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Rename 1 files…" }));
    await waitFor(() =>
      expect(apply).toHaveBeenCalledWith(
        expect.objectContaining({
          template: "custom",
          only_src: [plan.src],
          expected_plan: [{ src: plan.src, dst: plan.dst }],
        }),
      ),
    );
    expect(confirm).toHaveBeenCalledOnce();
    expect(
      await screen.findByText("Renamed 0 · skipped 0 · failed 0."),
    ).toBeVisible();
  });

  it("invalidates an old plan when a new preview fails", async () => {
    vi.spyOn(api.episodes.rename, "preview")
      .mockResolvedValueOnce({
        folder_path: "/fixture",
        template: "default",
        items: [plan],
      })
      .mockRejectedValueOnce(new Error("Provider unavailable"));
    const user = userEvent.setup();
    mount(
      <RenameModal
        path="/fixture"
        onClose={() => {}}
        onApplied={async () => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Generate preview" }));
    await screen.findByText("new.mkv");
    await user.click(screen.getByRole("button", { name: "Generate preview" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Provider unavailable",
    );
    expect(
      screen.getByRole("button", { name: "Rename 0 files…" }),
    ).toBeDisabled();
  });
});

describe("settings edits", () => {
  const saved: Settings = {
    preferred_language: "eng",
    fallback_languages: ["eng"],
    cache_ttl_hours: 168,
    auto_match_threshold: 85,
    metadata_source: "tvdb",
    watcher_enabled: false,
    plex_url: "",
    plex_path_mappings: [],
    plex_token_configured: false,
    include_original_title: true,
    overwrite_foreign_nfo: false,
    fanart_enabled: true,
    tmdb_artwork_enabled: true,
    preferred_artwork_source: "auto",
    plex_auto_refresh: false,
    plex_refresh_delay_seconds: 5,
    rename_episode_template: "",
    rename_daily_template: "",
    rename_anime_template: "",
    rename_series_folder_template: "",
    rename_season_folder_template: "",
    rename_movie_template: "",
    rename_movie_folder_template: "",
    rename_enabled: true,
    auto_sweep_orphans: true,
    tvdb_artwork_languages: [],
    tvdb_artwork_allow_null_language: true,
    tmdb_artwork_languages: [],
    tmdb_artwork_allow_null_language: true,
    watcher_debounce_seconds: 30,
    tvdb_api_key_configured: false,
    tvdb_pin_configured: false,
    tmdb_api_key_configured: false,
    fanart_api_key_configured: false,
  };
  it("creates a patch without readonly secret flags or unrelated values", () => {
    expect(
      settingsPatch(saved, {
        ...saved,
        preferred_language: "jpn",
        plex_token_configured: true,
      }),
    ).toEqual({ preferred_language: "jpn" });
  });
  it("tracks ordinary fields and preserves edits while saving a Plex connection", async () => {
    vi.spyOn(api.settings, "get").mockResolvedValue(saved);
    const save = vi.spyOn(api.settings, "set").mockResolvedValue({ ok: true });
    vi.spyOn(api.plex, "test").mockResolvedValue({
      ok: true,
      identity: { friendly_name: "Fixture Plex" },
      sections: [],
    });
    const user = userEvent.setup();
    const client = mount(<SettingsView />);
    const language = await screen.findByRole("textbox", {
      name: "Preferred language (3-letter)",
    });
    await user.clear(language);
    await user.type(language, "jpn");
    expect(screen.getByText("Unsaved changes")).toBeVisible();
    act(() => {
      client.setQueryData(["settings"], { ...saved, watcher_enabled: true });
    });
    await user.click(screen.getByRole("button", { name: /Plex Server URL/ }));
    await user.click(
      screen.getByRole("button", { name: "Save connection & test" }),
    );
    await screen.findByText("Connected to Fixture Plex");
    await user.click(
      screen.getByRole("button", { name: /Metadata Source, language/ }),
    );
    expect(
      screen.getByRole("textbox", { name: "Preferred language (3-letter)" }),
    ).toHaveValue("jpn");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(save).toHaveBeenLastCalledWith(
        expect.objectContaining({ preferred_language: "jpn" }),
      ),
    );
    expect(save.mock.calls[save.mock.calls.length - 1]?.[0]).not.toHaveProperty(
      "watcher_enabled",
    );
  });

  it("preserves edits made while a Plex connection save is pending", async () => {
    let finishSave!: (value: { ok: true }) => void;
    const pendingSave = new Promise<{ ok: true }>((resolve) => {
      finishSave = resolve;
    });
    vi.spyOn(api.settings, "get")
      .mockResolvedValueOnce(saved)
      .mockResolvedValue({
        ...saved,
        plex_url: "http://saved-plex:32400",
        plex_token_configured: true,
      });
    const save = vi
      .spyOn(api.settings, "set")
      .mockReturnValueOnce(pendingSave)
      .mockResolvedValue({ ok: true });
    const testConnection = vi.spyOn(api.plex, "test").mockResolvedValue({
      ok: true,
      identity: { friendly_name: "Fixture Plex" },
      sections: [],
    });
    const user = userEvent.setup();
    mount(<SettingsView />);
    await screen.findByRole("textbox", {
      name: "Preferred language (3-letter)",
    });
    await user.click(screen.getByRole("button", { name: /Plex Server URL/ }));
    await user.type(
      screen.getByRole("textbox", { name: "Plex base URL" }),
      "http://saved-plex:32400",
    );
    await user.type(screen.getByLabelText("Plex token"), "saved-token");
    await user.click(
      screen.getByRole("button", { name: "Save connection & test" }),
    );
    expect(save).toHaveBeenCalledOnce();
    await user.clear(screen.getByRole("textbox", { name: "Plex base URL" }));
    await user.type(
      screen.getByRole("textbox", { name: "Plex base URL" }),
      "http://next-plex:32400",
    );
    await user.clear(screen.getByLabelText("Plex token"));
    await user.type(screen.getByLabelText("Plex token"), "next-token");
    await user.click(
      screen.getByRole("button", { name: /Metadata Source, language/ }),
    );
    const language = screen.getByRole("textbox", {
      name: "Preferred language (3-letter)",
    });
    await user.clear(language);
    await user.type(language, "jpn");
    await act(async () => {
      finishSave({ ok: true });
      await pendingSave;
    });
    await waitFor(() => expect(testConnection).toHaveBeenCalledOnce());
    expect(language).toHaveValue("jpn");
    await user.click(screen.getByRole("button", { name: /Plex Server URL/ }));
    expect(screen.getByRole("textbox", { name: "Plex base URL" })).toHaveValue(
      "http://next-plex:32400",
    );
    expect(screen.getByLabelText("Plex token (configured)")).toHaveValue(
      "next-token",
    );
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(save).toHaveBeenLastCalledWith(
        expect.objectContaining({
          preferred_language: "jpn",
          plex_url: "http://next-plex:32400",
          plex_token: "next-token",
        }),
      ),
    );
  });

  it("shows a retry instead of perpetual loading after a settings failure", async () => {
    vi.spyOn(api.settings, "get").mockRejectedValue(
      new Error("Connection lost"),
    );
    mount(<SettingsView />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Connection lost",
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });
});

it("keeps failed metadata edits available for retry", async () => {
  vi.spyOn(api.overrides, "get").mockResolvedValue({
    path: "/fixture",
    overrides: {},
  });
  vi.spyOn(api.overrides, "set").mockRejectedValueOnce(
    new Error("Read-only share"),
  );
  mount(<OverridesTab path="/fixture" kind="movie" binding={null} />);
  const title = await screen.findByRole("textbox", { name: "Title" });
  fireEvent.change(title, { target: { value: "My title" } });
  fireEvent.blur(title);
  expect(await screen.findByRole("alert")).toHaveTextContent("Read-only share");
  expect(title).toHaveValue("My title");
  expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
});

it("binds the media kind used for the displayed search results", async () => {
  vi.spyOn(api.match, "search").mockResolvedValue({
    provider: "tvdb",
    results: [{ id: 123, name: "Fixture", year: 2020, provider: "tvdb" }],
  });
  const bind = vi.spyOn(api.match, "bind").mockResolvedValue({ ok: true });
  const user = userEvent.setup();
  mount(
    <MatchPanel path="/fixture" detectedKind="series" onBound={() => {}} />,
  );
  await user.type(
    screen.getByRole("textbox", { name: "Title to match" }),
    "Fixture",
  );
  await user.click(screen.getByRole("button", { name: "Search" }));
  await screen.findByRole("button", { name: "Match title" });
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Media type" }),
    "movie",
  );
  await user.click(screen.getByRole("button", { name: "Match title" }));
  await waitFor(() =>
    expect(bind).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "series", external_id: "123" }),
    ),
  );
});

it("recovers the build controls after a queue failure", async () => {
  vi.spyOn(api.items, "detail").mockResolvedValue({
    path: "/fixture",
    state: { title: "Fixture", kind: "movie", orphan_count: 0 },
    binding: null,
    artwork_files: [],
    overrides: {},
    provider_episode_count: null,
    provider_used: null,
    library_kind: "movies",
    tags: { tvdb: [], tmdb: [], custom: [] },
  });
  vi.spyOn(api, "health").mockResolvedValue({
    ok: true,
    tvdb_configured: false,
  });
  vi.spyOn(api, "build").mockRejectedValue(new Error("Queue unavailable"));
  const user = userEvent.setup();
  mount(<DetailView path="/fixture" onBack={() => {}} />);
  const build = await screen.findByRole("button", { name: "Build NFOs" });
  await user.click(build);
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Queue unavailable",
  );
  expect(build).toBeEnabled();
});

it("refreshes the item when its queued build finishes", async () => {
  const detail = vi.spyOn(api.items, "detail").mockResolvedValue({
    path: "/fixture",
    state: { title: "Fixture", kind: "movie", orphan_count: 0 },
    binding: null,
    artwork_files: [],
    overrides: {},
    provider_episode_count: null,
    provider_used: null,
    library_kind: "movies",
    tags: { tvdb: [], tmdb: [], custom: [] },
  });
  vi.spyOn(api, "health").mockResolvedValue({
    ok: true,
    tvdb_configured: false,
  });
  vi.spyOn(api, "build").mockResolvedValue({ ok: true, job: "fixture-job" });
  const getJob = vi.spyOn(api.jobs, "get").mockResolvedValue({
    id: "fixture-job",
    kind: "movie",
    folder: "/fixture",
    status: "completed",
    progress: 1,
    total: 1,
    messages: [],
    started_at: 1,
    finished_at: 2,
  });
  const user = userEvent.setup();
  mount(<DetailView path="/fixture" onBack={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "Build NFOs" }));
  expect(
    await screen.findByText(
      "Build complete. Local metadata and artwork refreshed.",
    ),
  ).toBeVisible();
  expect(detail.mock.calls.length).toBeGreaterThan(1);
  expect(getJob).toHaveBeenCalledWith("fixture-job");
  expect(screen.getByRole("button", { name: "Build NFOs" })).toBeEnabled();
});

describe("build tracking recovery", () => {
  const completedJob = {
    id: "fixture-job",
    kind: "movie",
    folder: "/fixture",
    status: "completed",
    progress: 1,
    total: 1,
    messages: [],
    started_at: 1,
    finished_at: 2,
  };
  beforeEach(() => {
    vi.spyOn(api.items, "detail").mockResolvedValue({
      path: "/fixture",
      state: { title: "Fixture", kind: "movie", orphan_count: 0 },
      binding: null,
      artwork_files: [],
      overrides: {},
      provider_episode_count: null,
      provider_used: null,
      library_kind: "movies",
      tags: { tvdb: [], tmdb: [], custom: [] },
    });
    vi.spyOn(api, "health").mockResolvedValue({
      ok: true,
      tvdb_configured: false,
    });
    vi.spyOn(api, "build").mockResolvedValue({ ok: true, job: "fixture-job" });
  });

  it("releases controls when the server no longer has the build job", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: "Job not found" }), {
          status: 404,
        }),
      );
    const user = userEvent.setup();
    mount(<DetailView path="/fixture" onBack={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "Build NFOs" }));
    expect(
      await screen.findByText(/Build status is no longer available/),
    ).toBeVisible();
    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/fixture-job",
      expect.any(Object),
    );
    expect(screen.getByRole("button", { name: "Build NFOs" })).toBeEnabled();
  });

  it("reports cancelled jobs without claiming a successful build", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async () =>
        new Response(JSON.stringify({ ...completedJob, status: "cancelled" })),
    );
    const user = userEvent.setup();
    mount(<DetailView path="/fixture" onBack={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "Build NFOs" }));
    expect(await screen.findByText(/Build interrupted/)).toBeVisible();
    expect(screen.queryByText(/Build complete/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Build NFOs" })).toBeEnabled();
  });

  it("keeps polling after a transient progress failure", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("Connection lost"))
      .mockImplementation(
        async () => new Response(JSON.stringify(completedJob)),
      );
    const user = userEvent.setup();
    mount(<DetailView path="/fixture" onBack={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "Build NFOs" }));
    expect(
      await screen.findByText(/Progress unavailable: Connection lost/),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Build NFOs" })).toBeDisabled();
    expect(
      await screen.findByText(
        "Build complete. Local metadata and artwork refreshed.",
        {},
        { timeout: 3500 },
      ),
    ).toBeVisible();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Build NFOs" })).toBeEnabled();
  });
});

it("limits episode choices by season while retaining cross-season mapping", async () => {
  vi.spyOn(api.episodes, "list").mockResolvedValue({
    path: "/fixture",
    provider: "tvdb",
    tvdb_episodes: [
      {
        id: "one",
        season: 1,
        number: 1,
        name: "First",
        aired: null,
        image: null,
      },
      {
        id: "two",
        season: 2,
        number: 1,
        name: "Second",
        aired: null,
        image: null,
      },
    ],
    locals: [
      {
        file_path: "/fixture/video.mkv",
        file_name: "video.mkv",
        parsed_season: 1,
        parsed_episode: 1,
        effective_season: 1,
        effective_episode: 1,
        override_episode_id: null,
        matched_episode_id: "one",
        matched_season: 1,
        matched_number: 1,
        matched_title: "First",
      },
    ],
  });
  const user = userEvent.setup();
  mount(<EpisodeMapper path="/fixture" />);
  const picker = await screen.findByRole("combobox", {
    name: "Provider episode for video.mkv",
  });
  expect(
    screen.queryByRole("option", { name: "S02E01 — Second" }),
  ).not.toBeInTheDocument();
  await user.selectOptions(picker, "__all_seasons__");
  expect(
    screen.getByRole("option", { name: "S02E01 — Second" }),
  ).toBeInTheDocument();
});
