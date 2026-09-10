import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
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

test("detail episodes, rename preview, artwork and settings draft guard", async ({
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
  await page
    .getByRole("button", { name: "Preview rename…", exact: true })
    .click();
  const rename = page.getByRole("dialog", { name: "Rename to scheme" });
  await rename.getByRole("button", { name: "Generate preview" }).click();
  await expect(
    rename.getByRole("button", { name: "Rename 3 files…", exact: true }),
  ).toBeEnabled();
  await rename.getByLabel("Release group override").fill("QA");
  await expect(
    rename.getByRole("button", { name: "Rename 3 files…", exact: true }),
  ).toBeDisabled();
  await expect(rename).toContainText("Options changed");
  await page.keyboard.press("Escape");
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
