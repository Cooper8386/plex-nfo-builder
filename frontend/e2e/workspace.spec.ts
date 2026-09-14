import { expect, test } from "@playwright/test";
import { readFile, stat } from "node:fs/promises";
import { join } from "node:path";

// Run only against backend/tests/qa_server.py (fictional, temporary media).
test.beforeEach(async ({ page, request }) => {
  const response = await request.get(
    "http://127.0.0.1:8000/api/items?library=Series",
    { headers: { "X-API-Token": "pnb-local-qa" } },
  );
  expect(response.ok()).toBeTruthy();
  const payload = await response.json();
  expect(
    payload.items.find(
      (item: { title: string; folder_path: string }) => item.title === "Aurora",
    )?.folder_path,
  ).toContain("pnb-qa-");
  await page.goto("/");
  await page.getByLabel("API token", { exact: true }).fill("pnb-local-qa");
  await page.getByRole("button", { name: "Unlock" }).click();
  await expect(
    page.getByRole("heading", { name: "Media libraries", exact: true }),
  ).toBeVisible();
});

test("library snapshots create and retain scoped ZIP backups", async ({ page, request }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const existing = await request.get("http://127.0.0.1:8000/api/libraries/Series/snapshots", {
    headers: { "X-API-Token": "pnb-local-qa" },
  });
  expect(existing.ok()).toBeTruthy();
  const before = await existing.json();
  expect(before.storage_path).toContain("pnb-qa-");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings categories" })
    .getByRole("button", { name: /Libraries/ }).click();
  await page.getByRole("combobox", { name: "Library", exact: true }).selectOption("Series");
  const downloads = page.getByRole("link", { name: /^Download ZIP / });
  await expect(downloads).toHaveCount(before.snapshots.length);
  await page.getByRole("button", { name: "Create snapshot", exact: true }).click();
  await expect(downloads).toHaveCount(before.snapshots.length + 1);
  await expect(page.getByRole("button", { name: "Create snapshot", exact: true })).toBeEnabled();
  await expect(page.getByText(/Snapshot completed/).first()).toBeVisible();

  const archive = downloads.first();
  const archiveUrl = (await archive.getAttribute("href"))!;
  expect(archiveUrl).toContain("/api/libraries/Series/snapshots/");
  const downloadEvent = page.waitForEvent("download");
  await archive.click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toMatch(/^Series-.*\.zip$/);
  const savedZip = info.outputPath("snapshot.zip");
  await download.saveAs(savedZip);
  expect((await readFile(savedZip)).subarray(0, 4).toString("hex")).toBe("504b0304");

  await page.reload();
  await page.getByRole("combobox", { name: "Library", exact: true }).selectOption("Series");
  await expect(page.locator(`a[href="${archiveUrl}"]`)).toBeVisible();
  await page.getByRole("combobox", { name: "Library", exact: true }).selectOption("Movies");
  await expect(page.getByText("No snapshots for Movies yet.")).toBeVisible();
  await expect(downloads).toHaveCount(0);
  await page.getByRole("combobox", { name: "Library", exact: true }).selectOption("Series");
  await expect(downloads).toHaveCount(before.snapshots.length + 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: `test-results/library-snapshots-${info.project.name}.png`, fullPage: true });
  expect(errors).toEqual([]);
});

test("sidebar options fit narrow widths and remain reachable at the bottom", async ({ page }, info) => {
  await page.route("**/api/libraries", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    await route.fulfill({
      json: {
        ...payload,
        libraries: Array.from({ length: 24 }, (_, index) => ({
          ...payload.libraries[0],
          name: `Menu ${index + 1}`,
          enabled: 1,
          effective_metadata_source: "tmdb",
        })),
      },
    });
  });
  if (info.project.name === "desktop") {
    await page.setViewportSize({ width: 800, height: 480 });
  }
  await page.reload();
  const showLibraries = page.getByRole("button", { name: "Show libraries", exact: true });
  if (await showLibraries.isVisible()) await showLibraries.click();
  const sidebar = page.getByRole("complementary", { name: "Libraries", exact: true });

  for (const name of ["Menu 1", "Menu 24"]) {
    await page.getByRole("button", { name: `Options for ${name}`, exact: true }).click();
    const menu = page.getByRole("group", { name: `Library options for ${name}`, exact: true });
    await menu.scrollIntoViewIfNeeded();
    await expect(menu).toBeInViewport({ ratio: 1 });
    const bounds = (await menu.boundingBox())!;
    const sidebarBounds = (await sidebar.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(sidebarBounds.x);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(sidebarBounds.x + sidebarBounds.width);
    await expect(menu.getByRole("combobox")).toBeInViewport({ ratio: 1 });
    await expect(menu.getByRole("button", { name: "Remove from app…" })).toBeInViewport({ ratio: 1 });
  }
  await page.screenshot({ path: `test-results/sidebar-${info.project.name}.png` });
});

test("media page matches without building and automatically links secondary source", async ({ page, request }, info) => {
  const fixture = JSON.parse(await readFile(join(process.cwd(), "../.qa/fixture.json"), "utf8"));
  const nfoPath = join(fixture.series_path, "tvshow.nfo");
  const before = await stat(nfoPath);
  const unbind = await request.post(
    `http://127.0.0.1:8000/api/match/unbind?folder_path=${encodeURIComponent(fixture.series_path)}`,
    { headers: { "X-API-Token": "pnb-local-qa" } },
  );
  expect(unbind.ok()).toBeTruthy();
  const builds: string[] = [];
  page.on("request", (req) => { if (req.url().includes("/api/build")) builds.push(req.url()); });
  await page.getByRole("button", { name: /TV series Series.*Open library/i }).click();
  await page.getByRole("button", { name: "Open Aurora", exact: true }).click();
  await expect(page.getByText("This folder isn't bound yet")).toBeVisible();
  await page.getByRole("button", { name: "Auto-match only" }).click();
  await expect(page.getByText("Source matched. No NFOs or artwork built.")).toBeVisible();
  await expect(page.getByText("tmdb-20000", { exact: true })).toBeVisible();
  const detail = await request.get(
    `http://127.0.0.1:8000/api/items/detail?path=${encodeURIComponent(fixture.series_path)}`,
    { headers: { "X-API-Token": "pnb-local-qa" } },
  );
  expect((await detail.json()).binding.secondary_external_id).toBe("20000");
  expect((await stat(nfoPath)).mtimeMs).toBe(before.mtimeMs);
  expect(builds).toEqual([]);
  await page.screenshot({ path: `test-results/source-discovery-${info.project.name}.png`, fullPage: true });
});

test("browse, filter, sort, empty state, scan and scoped destructive preview", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page
    .getByRole("button", { name: /TV series Series.*Open library/i })
    .click();
  await expect(
    page.getByRole("heading", { name: "Series", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: `test-results/grid-${info.project.name}.png`,
    fullPage: true,
  });
  await page
    .getByLabel("Filter by manual artwork")
    .selectOption("complete");
  await expect(
    page.getByRole("button", { name: "Open Afterlight", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Open Aurora", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("Filter by manual artwork").selectOption("any");
  await page.getByLabel("Search library").fill("Aurora");
  await expect(
    page.getByRole("button", { name: "Open Aurora", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Open Afterlight", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "List", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Aurora2024", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Sort library").selectOption("title-desc");
  await page.getByLabel("Search library").fill("nothing-matches");
  await expect(
    page.getByRole("heading", { name: "No titles match this view" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await page.getByRole("button", { name: "Scan library" }).click();
  await expect(page.getByRole("status")).toContainText("Scan complete");
  await page.getByRole("button", { name: /Library maintenance/ }).click();
  await page
    .getByRole("button", { name: "Preview NFO + artwork wipe" })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("12 folder(s)");
  await expect(
    dialog.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(
    dialog.getByRole("button", { name: "Wipe", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(
    dialog.getByRole("button", { name: "Close dialog", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Preview NFO + artwork wipe" }),
  ).toBeFocused();
  await page.screenshot({
    path: `test-results/library-${info.project.name}.png`,
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});

test("detail episodes, artwork and settings draft guard", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page
    .getByRole("button", { name: /TV series Series.*Open library/i })
    .click();
  await page.getByRole("button", { name: "Open Aurora", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Aurora", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: `test-results/detail-${info.project.name}.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: /^episodes$/i }).click();
  await expect(
    page.getByRole("combobox", {
      name: "Provider episode for Aurora - S01E01.mkv",
    }),
  ).toHaveValue("10001");
  await page.getByRole("button", { name: /^artwork$/i }).click();
  await expect(
    page.getByRole("heading", { name: "Aurora", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByLabel("Preferred language (3-letter)").fill("jpn");
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "Leave unsaved settings?" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByLabel("Preferred language (3-letter)")).toHaveValue(
    "jpn",
  );
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page
    .getByRole("dialog", { name: "Leave unsaved settings?" })
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await page.getByRole("button", { name: "Activity", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Activity", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Logs", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Application log" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByLabel("API token", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("match, persist metadata and uploaded artwork, then build fixture files", async ({
  page,
  request,
}, info) => {
  const headers = { "X-API-Token": "pnb-local-qa" };
  const items = await (
    await request.get("http://127.0.0.1:8000/api/items?library=Series", {
      headers,
    })
  ).json();
  const folder = items.items.find(
    (item: { title: string }) => item.title === "Daybreak",
  ).folder_path as string;
  expect(folder).toContain("pnb-qa-");
  const marker = `Isolated QA ${info.project.name}`;
  await page
    .getByRole("button", { name: /TV series Series.*Open library/i })
    .click();
  await page
    .getByRole("button", { name: "Open Daybreak", exact: true })
    .click();
  await page.getByRole("button", { name: "Change match", exact: true }).click();
  await page.getByLabel("Title to match").fill("Daybreak");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("button", { name: "Match title", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Daybreak", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Metadata overrides", exact: true })
    .click();
  await page.getByRole("textbox", { name: /^Tagline/ }).fill(marker);
  await page.getByRole("textbox", { name: /^Tagline/ }).press("Tab");
  await expect
    .poll(async () => {
      const saved = await (
        await request.get(
          `http://127.0.0.1:8000/api/overrides?path=${encodeURIComponent(folder)}`,
          { headers },
        )
      ).json();
      return saved.overrides.series?.tagline;
    })
    .toBe(marker);
  await page.getByRole("button", { name: /^artwork$/i }).click();
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Upload image", exact: true }).click();
  await (
    await chooser
  ).setFiles({
    name: "qa-poster.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2kX8AAAAASUVORK5CYII=",
      "base64",
    ),
  });
  await expect(page.getByRole("status")).toContainText(
    "Uploaded qa-poster.png",
  );
  await page
    .getByRole("button", { name: /Select Poster from Custom.*option 1/ })
    .click();
  await expect(page.getByRole("status")).toContainText("Saved selection");
  // This writes only the guarded temporary fixture; no real provider keys/media exist.
  await page.getByRole("button", { name: "Build NFOs", exact: true }).click();
  await expect(
    page.getByText("Build complete. Local metadata and artwork refreshed.", {
      exact: true,
    }),
  ).toBeVisible({ timeout: 20000 });
  expect(await readFile(join(folder, "tvshow.nfo"), "utf8")).toContain(marker);
  expect((await readFile(join(folder, "poster.jpg"))).length).toBeGreaterThan(
    0,
  );
  expect(
    await readFile(join(folder, ".plex-nfo-builder.json"), "utf8"),
  ).toContain(marker);
  await page.getByRole("button", { name: "Automation", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Filesystem watcher", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
