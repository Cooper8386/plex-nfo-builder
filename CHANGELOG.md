# Changelog

All notable changes to **plex-nfo-builder**. The project follows [SemVer](https://semver.org/).

## 0.7.0 — 2026-05-04

### Added

- **Per-library metadata source override.** You can now pin a specific
  library to TVDB or TMDB independently of the global setting, so a
  setup with multiple TV libraries (e.g. `tv` on TVDB, `anime` on TMDB,
  another mixed library on the global default) works the way you'd
  expect without per-show binding gymnastics. Set it from the sidebar
  by opening the ⋮ menu next to a library and picking a metadata
  source; the dropdown also tells you which source is currently in
  effect when the library is inheriting from the global setting.
- The override flows through every entry point that picks a provider:
  per-folder builds (`build_series` / `build_movie`), bulk auto-match
  (`/api/match/auto-bulk`), and manual search (`/api/match/search` now
  accepts an optional `library` query param to scope the default).
- `/api/libraries` responses include a new `metadata_source` (the raw
  override or `null`) plus `effective_metadata_source` (resolved
  source after applying the override) so the UI can display "using
  TVDB (inherited)" without re-implementing the resolution logic.

### Changed

- `POST /api/libraries/{name}` now accepts `metadata_source` in the body.
  Pass `"tvdb"` or `"tmdb"` to set an override; pass `""`, `"default"`,
  or `null` to clear it and inherit the global setting. Unknown values
  are silently treated as "clear" so a typo can't break a build.
- New DB column `libraries.metadata_source` (nullable). Existing
  databases are migrated automatically on first launch via the same
  additive `ALTER TABLE` pattern used for `bindings.source_locked`.

### Notes

- Per-folder bindings still win over the per-library override — if you
  manually pinned a single show to TVDB and locked it, switching the
  library to TMDB won't silently flip that show.

## 0.6.3 — 2026-05-04

### Fixed

- **Plex auto-refresh now actually finds the show.** v0.6.1/0.6.2's
  per-item refresh always fell through to "item not yet indexed"
  even for shows that were clearly visible in Plex, because
  `GET /library/sections/{id}/all?type=2` returns show `<Directory>`
  elements **without** `<Location>` children unless you explicitly
  request them. The matcher therefore had no folder paths to compare
  against and bailed out. Fix: pass `includeLocations=1`.
- Added a belt-and-braces fallback for the rare case where Plex
  still doesn't return Locations: list episodes in the section
  (`type=4`), match an episode file under the target folder, and
  back-derive the parent show's `ratingKey` from
  `grandparentRatingKey`. So even on edge-case Plex builds, the
  metadata refresh fires for the right show.
- Folder matcher now walks every parent directory of a media file
  path — fixes movies whose `Part.file` is nested deeper than one
  level under the folder.
- The fallback "partial-scan-only" message now reports how many
  items were listed in the section and points at the most likely
  cause (path-mapping mismatch), instead of misleadingly claiming
  the item isn't indexed yet.

## 0.6.2 — 2026-05-04

### Fixed

- **Multi-arch Docker build.** The v0.6.1 image never published because
  the arm64 build died with `qemu: uncaught target signal 4 (Illegal
  instruction) - core dumped` during `npm install`. This is a known
  Node-on-QEMU issue on larger dependency trees and is unrelated to
  application code. Dockerfile now pins the frontend build stage to
  `$BUILDPLATFORM`, so `npm install` + `vite build` run natively on
  the GitHub runner (amd64) and the resulting static `dist/` is
  copied into both the amd64 and arm64 backend images. Frontend
  output is architecture-independent, so this is strictly a
  build-time speed and reliability win.
- Ships the v0.6.1 Plex refresh fix (two-step scan + per-item
  metadata refresh) as a published image.

## 0.6.1 — 2026-05-04

### Fixed

- **Plex refresh now actually re-reads the NFO.** v0.6.0 only called
  `GET /library/sections/{id}/refresh?path=...`, which tells Plex to
  scan the folder for _new media files_ — but Plex deliberately skips
  re-reading metadata for items whose `.mkv`/`.mp4` files already
  exist. So after a build the updated `.nfo` / artwork were ignored
  even though Plex returned 200 OK to the scan call. The hook now
  does a two-step dance:
  1. Partial scan of the folder (picks up any newly-added episodes).
  2. `GET /library/sections/{id}/all` to locate the show or movie
     whose location/file path lives under the target folder, then
     `PUT /library/metadata/{ratingKey}/refresh` to force Plex to
     re-read that specific item's metadata.
- Job messages and the "Refresh in Plex" toast now report which
  strategy fired (`metadata-refresh` vs fallback `partial-scan-only`)
  and include the matched item title and ratingKey so it's obvious
  whether Plex really re-read the NFO.
- Help entry for Plex integration now explains the two-step behavior
  and the one edge case where only a partial scan fires (brand-new
  folders Plex hasn't indexed yet).

## 0.6.0 — 2026-05-04

### Added

- **Plex auto-refresh.** When a Plex base URL and token are configured
  in Settings, the app can ask Plex to do a partial rescan of a show
  or movie folder right after a build finishes — so updated NFOs and
  artwork show up in Plex without manually clicking refresh.
  - New Plex section in Settings: base URL, token (masked, treated like
    other secrets), auto-refresh checkbox, configurable delay (default
    5 seconds), and a Test connection button that lists the server's
    library sections.
  - **Path mappings** translate the app's view of disk (e.g. `/media`)
    to whatever Plex sees (e.g. `/data` or `/mnt/media`). Longest
    matching prefix wins, and folders that don't match any mapping pass
    through unchanged.
  - **Refresh in Plex** button on the show / movie detail page triggers
    an on-demand rescan and reports back which Plex section was
    targeted.
  - Refresh runs as a fire-and-forget background task after the build
    completes; any Plex error is logged but never fails the build, so
    NFOs and artwork are always written first.
- New endpoints: `GET /api/plex/test`, `GET /api/plex/sections`,
  `POST /api/plex/refresh`. `/api/health` now reports
  `plex_configured` and `plex_auto_refresh`.
- Help: "Plex auto-refresh" section explaining the workflow, where to
  get a Plex token, and how path mappings work.

## 0.5.9 — 2026-05-04

### Fixed

- Hotfix for 0.5.8: `download_series_canonical` /
  `download_movie_canonical` weren't actually accepting the new
  `preferred_overrides` keyword, so every series/movie build failed
  with `TypeError: unexpected keyword argument 'preferred_overrides'`
  once a preferred artwork source was active. Signatures corrected;
  NFO was still written before the crash, so re-running Build on
  affected shows now also downloads the preferred artwork.

## 0.5.8 — 2026-05-04

### Added

- **Preferred artwork source** setting (`auto` / `tvdb` / `tmdb`). When
  set to a specific provider, that provider's images (poster,
  background, banner, clearlogo, per-season posters) win during every
  build, independent of which provider supplies the metadata. Common
  use case: keep TVDB as the metadata source for descriptions, cast,
  and release dates while pulling artwork from TMDB. Per-show manual
  picks still override the preference, and the metadata source's own
  artwork is used as a fallback whenever the preferred provider has
  nothing suitable or is unreachable.
- Artwork Picker candidate ordering now honours the preferred-artwork
  source so what you see in the UI matches what the build writes to
  disk by default.

### Changed

- NFO `<thumb>` and `<fanart>` URLs on TVDB-bound shows/movies now
  reference the preferred provider's CDN when an override is active,
  so Plex's network fallback uses the same image set you see locally.

## 0.5.7 — 2026-05-04

### Added

- **Wipe NFOs & artwork** button on the detail view. Deletes every NFO
  (`tvshow.nfo`, `season.nfo`, episode `.nfo`) and every generated
  artwork file (`poster.jpg`, `background.jpg`, `banner.jpg`,
  `clearlogo.png`, `Season<NN>-poster.jpg`, season-folder posters)
  while leaving season folders and media files alone. Shows a dry-run
  confirmation listing exactly what will be deleted, then rescans the
  folder so the UI reflects the wipe immediately.
- **In-app Help view** reachable from the top bar with a quick tour,
  a Build NFOs vs Force rebuild reference, status-badge legend, and
  expected folder layout.
- New `POST /api/items/clean` endpoint with `dry_run`, `keep_sidecar`,
  and `rescan` knobs, backed by `services/cleaner.py`.
- Tooltips on the Build NFOs and Force rebuild buttons explaining the
  difference between them.

### Fixed

- **"Mixed" false positive** after a clean build. The scanner was
  counting the new `season.nfo` files (introduced in 0.5.3) as if they
  were episode NFOs, so a fully built show with N episodes ended up
  with N + (number of seasons) NFOs and was flagged `mixed`. The
  scanner now ignores `season.nfo` when computing episode coverage.

### Docs

- README highlights expanded to cover sidecar, overrides, and the new
  wipe action. New "Documentation" section pointing at the in-app Help,
  CHANGELOG, and README itself.

## 0.5.6 — 2026-05-04

### Added

- **Disable / remove libraries.** Each library row in the sidebar now has a
  kebab menu with `Disable` (greys it out, hides items, scans skip it) and
  `Remove from app…` (forgets every binding, override, and item-state row
  for the library). Files on disk — NFOs, artwork, and the
  `.plex-nfo-builder.json` sidecars — are never touched, so re-detecting
  brings everything back from sidecars.
- A footer toggle in the sidebar to show disabled libraries again, with a
  count.
- New `DELETE /api/libraries/{name}` endpoint and `db.delete_library` that
  cascades cleanly across `bindings`, `nfo_overrides`, `artwork_selections`,
  and `episode_overrides`.

### Changed

- `scan_library` skips disabled libraries with an info-level log.
- Re-detect preserves the `enabled` flag on existing rows, so disabling a
  library is sticky across rescans.

## 0.5.5 — 2026-05-03

### Fixed

- **Detail view episode count** is now provider-aware. Previously the
  "Episodes (TVDB)" stat was hard-coded and never populated, so it always
  rendered as `—`. The detail endpoint now computes matched-episode counts
  on demand against whichever provider the folder is actually bound to,
  honoring per-episode mapping overrides for TVDB shows.
- The label switches between `Episodes (TVDB)` and `Episodes (TMDB)`
  depending on the binding.

## 0.5.4 — 2026-05-03

### Fixed

- TMDB **per-season posters** are now picked up as a supplement when a show
  is bound to TVDB. Previously TMDB images were only fetched at the show
  level (poster / backdrop / logo) in the supplement path, so per-season
  posters from TMDB only appeared when the binding was switched. They now
  populate the `season-NN-poster` slot automatically and sort behind any
  TVDB candidate so the existing primary remains stable.

## 0.5.3 — 2026-05-03

### Added

- **Manual NFO field overrides** — Series, season, and episode-level
  overrides for `title`, `sorttitle`, `originaltitle`, `tagline`, and
  `plot`. Movies get the same set at series scope. New "Overrides" tab
  in the detail view lets you edit and reset each field; empty values
  fall back to the source provider.
- **Per-show metadata source override** with a "Lock for this show"
  toggle. Auto-match now respects the lock everywhere it touches a
  binding, so a single show can be pinned to TMDB while the rest of the
  library uses TVDB (or vice versa).
- **Sidecar config file** (`.plex-nfo-builder.json`) is written into each
  bound folder. It carries the binding (provider, external id, language,
  lock state), all NFO overrides, artwork selections, and episode
  overrides. On scan, if the database has no record of a folder but a
  sidecar exists, it's restored automatically — so a full DB wipe is
  recoverable straight from the media library.
- New `Season XX/season.nfo` output for both TVDB and TMDB primary paths,
  honoring season-scope overrides.

### Changed

- All `upsert_binding` callers in the matcher now pass `respect_lock=True`
  so auto-match cannot silently swap providers on a folder you've pinned.
- `/match/auto-bulk` pre-filters locked folders for cleaner result lists.

## 0.5.2 — earlier

- Auto-match binding fix; collapsible sidebar; cleaner show tiles; larger
  buttons.

## 0.5.1 — earlier

- Fix `missing protocol` artwork-download warnings; show locally
  downloaded poster in the detail view; rebuilds always overwrite the
  current artwork files.

## 0.5.0 — earlier

- TMDB as alternate metadata source; fanart.tv + TMDB artwork providers;
  custom artwork upload.

## 0.4.x — earlier

- Smarter artwork picker; per-season posters; episode-mapping UI;
  scanner removes deleted folders from the library.

## 0.3.x — earlier

- Stop downloading artwork through `.artwork/` symlinks; embed TVDB CDN
  URLs in NFOs.
