import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, test, vi } from "vitest";
import { ConfirmProvider } from "../components/ConfirmDialog";
import { useConfirm } from "../components/confirm";
import { authFetch, clearToken, getToken, setToken } from "./auth";
import { api, type Item } from "./api";
import { sortItems } from "./library";
import LibraryView from "../views/LibraryView";
import { LibraryGrid } from "../views/LibraryItems";
import ArtworkPicker from "../views/ArtworkPicker";

beforeEach(() => clearToken());

test("allows custom artwork when the provider has no candidates", async () => {
  vi.spyOn(api.artwork, "candidates").mockResolvedValue({
    path: "/fixture",
    kind: "series",
    slots: {},
    selections: {},
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ConfirmProvider>
        <ArtworkPicker path="/fixture" kind="series" />
      </ConfirmProvider>
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("button", { name: "Poster" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByRole("button", { name: "Upload image" })).toBeEnabled();
});

test("can ignore a show artwork slot", async () => {
  vi.spyOn(api.artwork, "candidates").mockResolvedValue({
    path: "/fixture",
    kind: "series",
    slots: { "season-08-poster": [] },
    selections: {},
  });
  const ignore = vi.spyOn(api.artwork, "ignore").mockResolvedValue({
    ok: true,
    ignored: true,
  });
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ConfirmProvider>
        <ArtworkPicker path="/fixture" kind="series" />
      </ConfirmProvider>
    </QueryClientProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Season 8 Poster" }));
  await userEvent.click(screen.getByRole("button", { name: "Ignore Season 8 Poster" }));
  await waitFor(() =>
    expect(ignore).toHaveBeenCalledWith({
      folder_path: "/fixture",
      slot: "season-08-poster",
    }),
  );
});

test("keeps ignored season slots visible when providers stop returning them", async () => {
  vi.spyOn(api.artwork, "candidates").mockResolvedValue({
    path: "/fixture",
    kind: "series",
    slots: {},
    selections: {
      "season-08-poster": {
        url: "",
        language: null,
        score: null,
        ignored: true,
      },
    },
  });
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ConfirmProvider>
        <ArtworkPicker path="/fixture" kind="series" />
      </ConfirmProvider>
    </QueryClientProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: /Season 8 Poster/ }));
  expect(screen.getByRole("button", { name: "Use auto for Season 8 Poster" })).toBeVisible();
});
const title = (name: string, path: string): Item => ({
  folder_path: path,
  library: "TV",
  kind: "series",
  title: name,
  year: 2024,
  external_id: null,
  provider: null,
  nfo_status: "none",
  episode_count_local: 1,
  episode_count_tvdb: null,
  poster_path: null,
});
function mountLibrary(library = "TV") {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ConfirmProvider>
        <LibraryView
          key={library}
          library={library}
          viewMode="grid"
          search=""
          onSearch={vi.fn()}
          onViewMode={vi.fn()}
          onSelectLibrary={vi.fn()}
          onOpenDetail={vi.fn()}
        />
      </ConfirmProvider>
    </QueryClientProvider>,
  );
}
test("sorts missing dates last and preserves original list", () => {
  const items = [
    { ...title("Later", "b"), date_added: 20 },
    { ...title("Missing", "c"), date_added: null },
    { ...title("Earlier", "a"), date_added: 10 },
  ];
  expect(sortItems(items, "added").map((item) => item.title)).toEqual([
    "Later",
    "Earlier",
    "Missing",
  ]);
  expect(items[0].title).toBe("Later");
});
test("token survives disabled browser storage for current session", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  setToken("session-token");
  const fetch = vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}"));
  await authFetch("/api/health");
  expect(new Headers(fetch.mock.calls[0][1]?.headers).get("X-API-Token")).toBe(
    "session-token",
  );
});
test("refuses token transmission to external URLs", async () => {
  setToken("secret");
  const fetch = vi.spyOn(window, "fetch");
  await expect(authFetch("https://example.com/")).rejects.toThrow(
    "another origin",
  );
  expect(fetch).not.toHaveBeenCalled();
});
test("late unauthorized response cannot erase a new login", async () => {
  let resolve!: (response: Response) => void;
  vi.spyOn(window, "fetch").mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  setToken("old");
  const request = authFetch("/api/health");
  setToken("new");
  resolve(new Response("", { status: 401 }));
  await request;
  expect(getToken()).toBe("new");
});

test("new login supersedes readable stale storage when storage writes fail", () => {
  vi.spyOn(Storage.prototype, "getItem").mockReturnValue("old");
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("quota");
  });
  setToken("new");
  expect(getToken()).toBe("new");
  clearToken();
  expect(getToken()).toBeNull();
});

test("loads every API page before returning the library", async () => {
  const fetch = vi
    .spyOn(window, "fetch")
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ items: [title("Aurora", "a")], total: 2 })),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({ items: [title("Daybreak", "b")], total: 2 }),
      ),
    );
  expect(
    (await api.items.list({ library: "TV" })).items.map((item) => item.title),
  ).toEqual(["Aurora", "Daybreak"]);
  expect(fetch.mock.calls[1][0]).toContain("offset=1");
});

test("poster retries after a completed build changes its image URL", () => {
  const item = {
    ...title("Aurora", "a"),
    poster_path: "/TV/Aurora/poster.jpg",
    last_built: 1,
  };
  const props = {
    items: [item],
    selected: new Set<string>(),
    onToggle: vi.fn(),
    onOpen: vi.fn(),
  };
  const view = render(<LibraryGrid {...props} />);
  fireEvent.error(view.container.querySelector("img")!);
  expect(screen.getByText("Artwork pending")).toBeVisible();
  view.rerender(
    <LibraryGrid {...props} items={[{ ...item, last_built: 2 }]} />,
  );
  expect(view.container.querySelector("img")?.src).toContain("&t=2");
});
test("destructive confirmation focuses Cancel and cancels on Escape", async () => {
  const result = vi.fn();
  function Trigger() {
    const confirm = useConfirm();
    return (
      <button
        onClick={async () =>
          result(
            await confirm({
              title: "Delete companions?",
              message: "3 files in TV",
              tone: "danger",
              confirmLabel: "Delete files",
            }),
          )
        }
      >
        Preview
      </button>
    );
  }
  render(
    <ConfirmProvider>
      <Trigger />
    </ConfirmProvider>,
  );
  await userEvent.click(screen.getByText("Preview"));
  expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  fireEvent(
    screen.getByRole("dialog"),
    new Event("cancel", { cancelable: true }),
  );
  await waitFor(() => expect(result).toHaveBeenCalledWith(false));
  expect(screen.getByText("Preview")).toHaveFocus();
});
test("queues simultaneous confirmations without stranding promises", async () => {
  const result = vi.fn();
  function Trigger() {
    const confirm = useConfirm();
    return (
      <button
        onClick={() => {
          void Promise.all([
            confirm({ title: "First", message: "one" }),
            confirm({ title: "Second", message: "two" }),
          ]).then(result);
        }}
      >
        Start
      </button>
    );
  }
  render(
    <ConfirmProvider>
      <Trigger />
    </ConfirmProvider>,
  );
  await userEvent.click(screen.getByText("Start"));
  await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
  expect(screen.getByRole("dialog", { name: "Second" })).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(result).toHaveBeenCalledWith([true, false]));
});

test("unmounting the authenticated workspace cancels pending confirmations", async () => {
  const result = vi.fn();
  function Trigger() {
    const confirm = useConfirm();
    return (
      <button
        onClick={() => {
          void confirm({
            title: "Delete files?",
            message: "Temporary fixture",
          }).then(result);
        }}
      >
        Preview
      </button>
    );
  }
  const view = render(
    <ConfirmProvider>
      <Trigger />
    </ConfirmProvider>,
  );
  await userEvent.click(screen.getByText("Preview"));
  view.unmount();
  await waitFor(() => expect(result).toHaveBeenCalledWith(false));
});
test("library presents fetch failure and retries", async () => {
  vi.spyOn(api.items, "list")
    .mockRejectedValueOnce(new Error("Share unavailable"))
    .mockResolvedValue({ items: [title("Aurora", "/TV/Aurora")] });
  mountLibrary();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Share unavailable",
  );
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(
    await screen.findByRole("button", { name: "Open Aurora" }),
  ).toBeVisible();
});
test("filter change clears selection and cannot submit hidden titles", async () => {
  vi.spyOn(api.items, "list").mockImplementation(async (params) => ({
    items: params.status ? [] : [title("Aurora", "/TV/Aurora")],
  }));
  mountLibrary();
  await userEvent.click(
    await screen.findByRole("button", { name: "Select Aurora" }),
  );
  expect(screen.getByRole("button", { name: "Build selected" })).toBeEnabled();
  await userEvent.click(screen.getByRole("button", { name: "Complete" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Build selected" })).toBeNull(),
  );
});
test("library maintenance targets the selected titles", async () => {
  vi.spyOn(api.items, "list").mockResolvedValue({
    items: [
      title("Aurora", "/TV/Aurora"),
      title("Daybreak", "/TV/Daybreak"),
    ],
  });
  const wipe = vi
    .spyOn(api.libraries, "wipeNfo")
    .mockResolvedValueOnce({
      ok: true,
      dry_run: true,
      library: "TV",
      folder_count: 1,
      file_count: 4,
    })
    .mockResolvedValueOnce({
      ok: true,
      library: "TV",
      folder_count: 1,
      nfo_deleted: 2,
      artwork_deleted: 2,
    });
  let finishSweep!: (
    value: Awaited<ReturnType<typeof api.libraries.sweepOrphans>>,
  ) => void;
  const sweep = vi.spyOn(api.libraries, "sweepOrphans").mockImplementation(
    () =>
      new Promise((resolve) => {
        finishSweep = resolve;
      }),
  );
  const emptySweep: Awaited<
    ReturnType<typeof api.libraries.sweepOrphans>
  > = {
    ok: true,
    dry_run: true,
    library: "TV",
    folder_count: 2,
    affected_folder_count: 0,
    nfo_removed: 0,
    thumb_removed: 0,
    folders: [],
    failed: [],
  };
  const sidecars = vi
    .spyOn(api.libraries, "wipeSidecars")
    .mockRejectedValue(new Error("Preview unavailable"));
  mountLibrary();
  await userEvent.click(
    await screen.findByRole("button", { name: "Select Aurora" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Select Daybreak" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: /Library maintenance/ }),
  );
  const maintenance = screen
    .getByRole("button", { name: /Library maintenance/ })
    .closest(".maintenance");
  expect(screen.getByText("Actions apply only to 2 selected titles.")).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "Preview selected NFO + artwork wipe" }),
  );
  expect(
    await screen.findByRole("dialog", {
      name: "Wipe NFOs + artwork from 2 selected titles?",
    }),
  ).toBeVisible();
  expect(wipe).toHaveBeenLastCalledWith("TV", {
    dry_run: true,
    folder_paths: ["/TV/Aurora", "/TV/Daybreak"],
  });
  await userEvent.click(screen.getByRole("button", { name: "Wipe" }));
  await waitFor(() =>
    expect(wipe).toHaveBeenLastCalledWith("TV", {
      dry_run: false,
      folder_paths: ["/TV/Aurora", "/TV/Daybreak"],
    }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Preview selected orphan cleanup" }),
  );
  expect(maintenance).toHaveTextContent(
    "Scanning 2 selected titles for orphaned NFO + thumbnail sidecars",
  );
  await act(async () => finishSweep(emptySweep));
  await waitFor(() =>
    expect(sweep).toHaveBeenCalledWith("TV", {
      dry_run: true,
      folder_paths: ["/TV/Aurora", "/TV/Daybreak"],
    }),
  );
  expect(maintenance).toHaveTextContent(
    "No orphaned sidecars found in 2 selected titles",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Preview selected sidecar deletion" }),
  );
  await waitFor(() =>
    expect(sidecars).toHaveBeenCalledWith("TV", {
      dry_run: true,
      folder_paths: ["/TV/Aurora", "/TV/Daybreak"],
    }),
  );
  expect(maintenance).toHaveTextContent(
    "Sidecar preview failed: Preview unavailable",
  );
});
test("manual artwork filter is independent and saved per library", async () => {
  localStorage.setItem("pnb.artworkFilter.TV", "incomplete");
  const list = vi.spyOn(api.items, "list").mockResolvedValue({
    items: [title("Aurora", "/TV/Aurora")],
  });
  mountLibrary("TV");
  const artwork = await screen.findByLabelText("Filter by manual artwork");
  expect(artwork).toHaveValue("incomplete");
  await waitFor(() =>
    expect(list).toHaveBeenCalledWith(
      expect.objectContaining({ library: "TV", manual_artwork: "incomplete" }),
    ),
  );

  await userEvent.selectOptions(artwork, "complete");
  await waitFor(() =>
    expect(list).toHaveBeenLastCalledWith(
      expect.objectContaining({ library: "TV", manual_artwork: "complete" }),
    ),
  );
  expect(localStorage.getItem("pnb.artworkFilter.TV")).toBe("complete");
  expect(localStorage.getItem("pnb.artworkFilter.Movies")).toBeNull();
});
test("scan remains pending until the server completes and refreshes items", async () => {
  const list = vi
    .spyOn(api.items, "list")
    .mockResolvedValue({ items: [title("Aurora", "/TV/Aurora")] });
  let done!: () => void;
  vi.spyOn(api.libraries, "scan").mockImplementation(
    () =>
      new Promise((resolve) => {
        done = () => resolve({ ok: true, scheduled: false });
      }),
  );
  mountLibrary();
  await screen.findByRole("button", { name: "Open Aurora" });
  await userEvent.click(screen.getByRole("button", { name: "Scan library" }));
  expect(screen.getByRole("button", { name: "Scanning…" })).toBeDisabled();
  await act(async () => done());
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent("Scan complete"),
  );
  expect(list).toHaveBeenCalledTimes(2);
});
