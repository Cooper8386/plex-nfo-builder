# Changelog

All notable changes to **plex-nfo-builder**. The project follows [SemVer](https://semver.org/).

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
