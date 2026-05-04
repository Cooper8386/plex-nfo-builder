# Changelog

All notable changes to **plex-nfo-builder**. The project follows [SemVer](https://semver.org/).

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
