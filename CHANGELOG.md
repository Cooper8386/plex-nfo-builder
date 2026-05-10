# Changelog

All notable changes to **plex-nfo-builder**. The project follows [SemVer](https://semver.org/).

## 0.11.16 — 2026-05-10

Follow-up to v0.11.15. The cast hydration step worked on a fresh
build, then immediately regressed when the user Force-rebuilt the next
show back-to-back. Two bugs combined to cause this and both are fixed.

### Fixed

- **Cast hydration is now cache-first regardless of `force`.** A Force
  rebuild was passing `force=True` straight through to the new
  `/people/{peopleId}` lookups, so every Force rebuild re-hit TVDB for
  every missing portrait. Two back-to-back Force rebuilds (e.g. RWBY,
  then RWBY Chibi) burned the same token through enough requests to
  trip TVDB's rate limit, the requests started 429-ing, and the
  failure path silently dropped the portraits with no log line. People
  records change at glacial speed; there is no good reason to bypass
  the cache for them, so the helper now ignores the build's `force`
  flag and always reads-through the normal cache.
- **Hydration failures now log loudly.** Previously each failed
  `/people/{peopleId}` lookup wrote a single debug-level message that
  most users wouldn't see. Failures now log at warning level with the
  peopleId, the cast member's name, and the underlying error. The
  summary line at the end of hydration also reports the list of
  failed peopleIds, so a glance at the build log will tell you why
  a particular cast member is still showing initials in Plex.

### Notes

- The cache key is per-peopleId; once a person's headshot is in cache
  it sticks across rebuilds of every show that person appears in.
- If you're still seeing missing cast images after this release,
  check the build log for `person_image lookup failed for peopleId=`
  lines — those will tell you whether TVDB is rate-limiting, the
  person record genuinely has no image, or something else.

## 0.11.15 — 2026-05-10

Follow-up to v0.11.14. The `image → personImgURL` fallback fixed cast
portraits when TVDB sets either field on the character record, but on
shows like RWBY the character payload from `/series/{id}/extended`
leaves *both* fields null even when the actor has a headshot uploaded
on their People record. So Miles Luna, Shannon McCormick, Michael
Jones, and Kerry Shawcross still rendered as initials in Plex after
upgrading.

### Fixed

- **Backfill missing actor portraits from TVDB People records.** Before
  building each TVDB-sourced NFO (series, movie, and per-episode), the
  builder now scans the character list for entries with no `image` and
  no `personImgURL`. For any such entry that has a `peopleId`, it
  fetches `/people/{peopleId}` and copies the actor's headshot onto
  the character as `personImgURL`. The existing fallback chain in the
  NFO writer then picks it up and emits a proper `<thumb>`. Matches
  what TVDB's own site does — fall back to the person's default
  headshot when the role art is missing.
- **Concurrency-capped enrichment.** People lookups are issued in
  parallel, capped at 60 missing portraits per build to keep a
  poorly-curated series with hundreds of bit-parts from tying up the
  job. Failed lookups are logged at debug level and silently dropped
  — the cast member just keeps its initials, no worse than before.
- **Cached on the normal TVDB TTL.** Headshot URLs almost never
  change, and the cache is shared with the rest of the TVDB client,
  so subsequent builds of the same library reuse the lookups.

### Notes

- Re-run **Force rebuild** on shows that were missing cast pictures
  in v0.11.14 — the first build of each show will issue the People
  lookups, and any future builds will hit the cache.
- This adds at most one TVDB request per missing cast member per
  show. RWBY's hydration was 4 lookups; on most shows it's zero.

## 0.11.14 — 2026-05-10

Cast headshot fix. Plex was showing initials ("ML", "SM", "MJ", "KS")
for cast members like Miles Luna, Shannon McCormick, Michael Jones, and
Kerry Shawcross even though their portraits were clearly present on
TVDB. The bug: TVDB's character object actually carries *two* image
fields, and we were only emitting the wrong one to the NFO.

### Fixed

- **Actor `<thumb>` now falls back from role art to person headshot.**
  TVDB v4 character records expose `image` (role-specific art, often
  `null`) and `personImgURL` (the actor's headshot). Plex's NFO reader
  renders whichever URL we put in `<actor><thumb>`. The series builder
  was only writing `image`, so anyone whose character had no role art
  ended up as initials in Plex even when the actor portrait was set on
  TVDB. We now fall back to `personImgURL` when `image` is missing,
  matching what TVDB's own site does. Same fix applied to the episode
  and movie NFO builders, which weren't writing actor thumbs at all.
- **Episode and movie NFOs now include cast portraits.** Both builders
  previously emitted `<actor><name>...</name><role>...</role></actor>`
  with no `<thumb>`. They now write the same `image → personImgURL`
  fallback the series builder uses, so Plex finally fills in cast
  pictures on those scopes too.

### Notes

- Existing series.nfo / episode.nfo / movie.nfo files won't update on
  their own — run **Force rebuild** on affected items (or **Rebuild
  changed** if you'd like the app to detect them automatically) to
  pick up the fix.

## 0.11.13 — 2026-05-10

Follow-up to v0.11.12. The TMDB language whitelist set in Settings was
applied everywhere the auto-resolver picks artwork but **not** in the
manual artwork picker (the candidate grid in a folder's detail panel),
so a user who set the whitelist to `en, ja` was still seeing `pt`, `es`,
`de`, `it`, `ar`, `it`, etc. cards in the picker. The picker now goes
through the same TMDB language filter as the auto-resolver.

### Fixed

- **TMDB manual artwork picker now respects the language whitelist.**
  `_tmdb_to_candidates` calls in the manual picker (series posters,
  backdrops, logos, season posters; both primary-TMDB and TVDB-primary-
  with-TMDB-supplement paths) now run their input through
  `apply_tmdb_image_language_filter` before normalization. The picker
  still calls TMDB with `include_all_languages=True` so the filter is
  the only thing constraining language; when the whitelist is empty,
  behaviour is unchanged. The all-rejected fallback still applies, so
  a title with no art in your whitelisted languages still shows the
  unfiltered set instead of an empty grid.

### Notes

- TVDB manual picker calls already routed through `list_candidates`
  with `apply_language_filter=True` (default), so it was correctly
  filtered in v0.11.12. This release closes the TMDB-side gap.
- If you want the picker to show every uploaded image regardless of
  language (e.g. you want to hand-pick a foreign poster on purpose),
  clear the TMDB whitelist in Settings → Artwork.

## 0.11.12 — 2026-05-09

Artwork language filtering. Mixed-language scrapers (TVDB and TMDB)
surface posters, fanart, and logos in every language a contributor has
ever uploaded. Until this release the app accepted whatever the
providers' default ranking returned, which on heavily-localized titles
often meant a Japanese poster on an English library or a logo with text
in the wrong script. This release lets you tell each provider which
languages of artwork you want to see and whether to allow art with no
language tag at all.

### Added

- **Per-provider artwork language whitelist.** New settings under
  Settings → Artwork — one multi-select list for TVDB (3-letter ISO
  639-2 codes like `eng`, `fra`, `jpn`) and a separate list for TMDB
  (2-letter ISO 639-1 codes like `en`, `fr`, `ja`). The available
  languages are queried live from each provider so the picker stays in
  sync with whatever each catalog actually supports. Empty list means
  *no whitelist* — legacy behaviour, every language is accepted.
- **Include / exclude language-less artwork toggle, per provider.**
  Both providers attach a lot of poster art with no language metadata
  (text-free key art, logos rendered as image-only, fan-uploaded
  variants). A separate "Include artwork with no language tag" switch
  for each provider lets you keep or drop those independently of the
  whitelist.
- **`GET /api/artwork/languages`** — backend endpoint that returns
  `{tvdb: [...], tmdb: [...]}` with `{code, name, native_name}` per
  entry. Returns an empty list for any provider whose credentials are
  missing so the UI can show a helpful empty state instead of an error.
- **Searchable language picker component** in the Settings page. The
  list of TMDB languages alone is close to 200 entries, so each picker
  has a free-text filter, an inline scrollable list, removable chips
  for the current selection, and a *Clear all* shortcut.

### Changed

- **Artwork selection respects the whitelist everywhere it picks art.**
  `services/artwork.py` (`list_candidates`, `best_artwork_url`) and
  `services/artwork_resolver.py` (TMDB poster / backdrop / logo lookups
  for series, seasons, and movies) now filter their results through the
  configured language rules before scoring and ranking. The filter is
  applied per call so series A's English poster and series B's Japanese
  poster are still chosen by their own metadata, not pre-filtered out
  of a shared bag.
- **All-rejected fallback.** If your filter would leave a given title
  with *zero* artwork, the unfiltered candidate list is returned
  instead. The whitelist is a preference, not a guarantee — a niche
  anime that only ships Japanese posters won't end up with no art
  because you set the filter to `eng` on a mostly-English library.
- **Language list normalization** on save. Codes are lowercased and
  deduplicated server-side so case-insensitive entry from the picker
  doesn't produce duplicate or mismatching whitelist entries.

### Notes

- TVDB and TMDB use different code systems on purpose. TVDB returns
  ISO 639-2 (3-letter), TMDB returns ISO 639-1 (2-letter), and there is
  no clean 1:1 mapping for several languages. Picking each
  independently is more honest than synthesizing a unified list.
- Default after upgrading is *unchanged behaviour* — both whitelists
  are empty (= accept all) and both "include language-less" toggles
  are on. Configure the filter only if you want it.

## 0.11.11 — 2026-05-09

A performance pass. After v0.11.10 shipped the orphan companion sweeper,
the app started to feel sluggish on large NAS-backed libraries — the
first page load took close to 90 seconds, switching pages stalled, and
the library-wide *Sweep orphaned sidecars* button took several minutes
on a TV library that fundamentally has nothing to sweep. Every slow
path had the same root cause: the app was re-walking every season
directory on every navigation and serializing those walks on the event
loop.

This release adds a small caching layer to `item_state` and rewires
every hot path to read from it.

### Added

- **Cached orphan count on `item_state`.** New `orphan_count` integer
  column, populated by every `scan_series_folder` / `scan_movie_folder`
  call from a single-pass directory walk. The detail page consults the
  cache before issuing the orphans preview request and the library-wide
  sweep skips folders whose count is proven-zero. NULL is treated as
  *unknown*, so the first sweep after upgrading still walks every folder
  once and primes the cache.
- **Single-pass series scanner** (`_scan_series_state` in
  `services/scanner.py`). The old code path iterated each season
  directory twice: once to count NFO files for status bucketing and
  once (via `services/orphans.py`) to count orphans. On the user's
  Unraid share each `iterdir()` is a full network round-trip, so the
  redundant pass dominated scan latency. The unified walk now produces
  status, provenance flag, NFO episode count, and orphan count in one
  enumeration.
- **`db.get_item_state(folder_path)`** — O(1) single-row lookup.
  Replaces three call-sites (`item_detail`, `items_clean`,
  `items_orphans_sweep`) that previously fetched every row in the table
  and filtered in Python — that meant detail-page navigation latency
  scaled linearly with library size.
- **Composite index `idx_item_state_lib_status (library, nfo_status)`**
  so the *All / Needs work / Complete* filter pill on the library
  toolbar is a single index range scan instead of a full-table scan.

### Changed

- **Library detection moved off the synchronous startup hook.**
  `detect_libraries()` now runs as an `asyncio.create_task` in the
  background. The API begins serving requests immediately on container
  start instead of after the share has been fully enumerated.
- **All orphan walks run in worker threads.** Every disk-touching
  branch in `/api/items/orphans`, `/api/items/orphans/sweep`, and
  `/api/libraries/{name}/orphans/sweep` now dispatches via
  `asyncio.to_thread` so a slow share can't stall every other request.
- **Library-wide sweep filters candidates in SQL.** The route fetches
  rows where `orphan_count > 0 OR orphan_count IS NULL` and walks only
  those. After the first run the cache stabilises and subsequent sweeps
  on a clean library complete in well under a second instead of
  walking the entire share.
- **`/api/items/orphans` fast-paths to a cached zero.** When the cached
  count is `0`, the endpoint returns `{nfo_removed: 0, thumb_removed:
  0, files: [], cached: true}` without touching disk. The freshly
  computed count is persisted on every cold call so subsequent calls
  short-circuit.
- **`hide_organized` on `/api/items` is now a SQL filter** instead of a
  post-query Python filter — saves materialising and discarding every
  `complete` row on libraries that hide them by default.

### Frontend

- **`OrphansPanel` skips its query entirely when the cached count is 0.**
  No request, no spinner, no flash on every detail-page navigation —
  the panel returns `null` immediately for clean folders.
- **Library items query: `staleTime: 60_000` + `keepPreviousData`.**
  Navigating into a show and back no longer refetches the grid, and
  typing in the search box / toggling the filter pill keeps the
  previous list visible during the refetch instead of flashing empty.
  Detail-view orphan preview `staleTime` bumped from 15s to 60s.

### Files touched

- `backend/app/db.py` — `orphan_count` column migration, composite
  index, `get_item_state()`.
- `backend/app/services/scanner.py` — `_scan_series_state()` single
  pass, `_count_movie_orphans_inline()`, `orphan_count` upsert in both
  scan_series_folder and scan_movie_folder. Legacy `_scan_nfo_state()`
  delegates to the new path.
- `backend/app/services/orphans.py` — `count_series_orphans` /
  `count_movie_orphans` helpers (used by callers that don't already do
  a full scan).
- `backend/app/routes/api.py` — every orphan / item route rewired to
  use cached counts, `get_item_state`, and `asyncio.to_thread`.
- `backend/app/main.py` — startup hook backgrounds
  `detect_libraries()`.
- `frontend/src/views/DetailView.tsx` — `OrphansPanel` accepts
  `cachedOrphanCount` and skips fetch on zero.
- `frontend/src/views/LibraryView.tsx` — items query gets `staleTime`
  and `keepPreviousData`.

### Migration

None required. The new `orphan_count` column is added by the existing
migration framework and starts at NULL; the first scan / sweep / orphan
preview on each folder populates it. No re-scan is required after
upgrade — the cache primes itself the first time you visit a detail
page or run a sweep.

## 0.11.10 — 2026-05-09

A targeted-fix release. Plugs the “my show appears twice in Plex even
though there's only one folder on disk” duplication bug introduced by
Sonarr/Radarr release upgrades.

### Diagnosis

Sonarr and Radarr only manage video files. When they swap a release for
an upgrade (different release group, higher quality, different codec)
the new `<new-stem>.mkv` arrives and the old video is deleted, but the
companion `<old-stem>.nfo` and `<old-stem>-thumb.{jpg,jpeg,png}` files
this app wrote next to the old video are left orphaned in the season
folder with no matching video. Plex's “Plex TV Series” agent reads
*every* `.nfo` regardless of whether it has a paired video; the
orphaned NFO carries its own `<uniqueid type="tvdb" default="true">`
block, Plex faithfully indexes it, and — because the orphaned uniqueid
doesn't match the new release sitting next to it — Plex creates a
second library entry for the same show in order to host the
orphaned-but-claimed episode. Two upgrade rounds = three Plex entries
for one folder, etc.

This release introduces a surgical, video-driven sweeper that deletes
those orphan companions whenever they are detected.

### Added

- **Orphan companion sweeper service** (`backend/app/services/orphans.py`).
  Enumerates the live video files in each season directory (or the
  show root for flat-layout series, or the movie folder for movies)
  via the same parser the builder uses, then deletes any `<stem>.nfo`
  or `<stem>-thumb.{jpg,jpeg,png}` whose stem isn't in the live video
  set. `tvshow.nfo`, every `season.nfo`, every show / season-level
  artwork file, and every video / subtitle / audio / unknown file is
  always preserved. Movie folders that currently contain no video are
  skipped entirely so an in-flight download can't get nuked.
- **Auto-sweep after every build (default on).** The four build
  end-paths (TVDB series, TVDB movie, TMDB series, TMDB movie) call
  the orphan sweeper immediately before the post-build Plex rescan.
  Failures are swallowed and logged — a sweep error never fails a
  build. The job log records `Removed N orphaned NFO(s) and M
  orphaned thumbnail(s) left behind by a Sonarr/Radarr file upgrade.`
  Toggle in **Settings → General → Auto-sweep orphaned sidecars**
  (new `auto_sweep_orphans` setting, default `True`).
- **Per-show orphan panel on the Detail view.** Hazard-yellow
  *Orphaned sidecars detected* card with the file list, an
  expandable details listing, and a `⚠ Remove orphaned sidecars`
  button. Renders only when at least one orphan exists. The button
  uses the in-app confirm dialog (`tone: "danger"`, Enter accepts)
  introduced in v0.11.9.
- **Library-wide orphan sweep in the Danger Zone.** New
  `⚠ Sweep orphaned sidecars` hazard-yellow button in every Library's
  Danger Zone, sitting alongside the existing wipe-NFOs and
  blast-sidecars buttons. Runs a dry-run preview first, lists the
  affected folders, and requires explicit confirmation before
  touching disk. The one-shot fix for libraries that have already
  accumulated duplicate Plex entries from prior Sonarr/Radarr file
  upgrades.
- **“Why does my show appear twice in Plex?” Help section** with the
  full root-cause writeup and post-sweep cleanup steps for Plex
  itself (Empty Trash + Clean Bundles, Merge ghost duplicates into
  the canonical entry).

### API

- `GET /api/items/orphans?path=…` — preview the orphans for a single
  folder. Returns
  `{ok, folder_path, nfo_removed, thumb_removed, files: ["Season 01/foo.nfo", …]}`.
  Detects series vs movie via a cheap filesystem probe.
- `POST /api/items/orphans/sweep` — body
  `{folder_path, dry_run?, rescan?}`. Deletes the orphans (or, with
  `dry_run: true`, just lists them). When `rescan` is true (default)
  re-runs the appropriate `scan_*_folder` so item state reflects
  the sweep.
- `POST /api/libraries/{name}/orphans/sweep` — body
  `{library, dry_run?, rescan?}`. Walks every tracked folder in the
  library, sweeps each, and returns
  `{ok, dry_run, library, folder_count, affected_folder_count, nfo_removed, thumb_removed, folders: […], failed: […]}`.
  Per-folder scan failures are isolated — the rest of the library
  still gets swept.

### Changed

- The Library page Danger Zone now has three buttons instead of two
  (Sweep orphaned sidecars + the existing wipe pair). README
  description updated to match.

### Notes

After pulling the update, run **Library → Danger Zone → Sweep orphaned
sidecars** once on each existing library. Then in Plex itself:
*Manage Libraries → affected show → Empty Trash + Clean Bundles*,
right-click each ghost duplicate and **Merge** it into the canonical
entry (Plex preserves watch state on the merge target), and
optionally **Refresh All Metadata** on the library afterwards. From
that point on the auto-sweep keeps duplicates from coming back.

## 0.11.9 — 2026-05-06

A UX-polish release with two big quality-of-life wins: a real
per-episode thumbnail picker for TMDB-bound shows, and the death of
every native browser confirm / prompt popup in the app.

### Added

- **Per-episode thumbnail picker (TMDB).** The flat "Episode
  thumbnails" gallery introduced in v0.11.8 has been replaced with a
  per-episode picker that lives directly inside each episode's
  collapsible row on the Overrides tab. Expanding an episode
  lazy-fetches every still TMDB has on file for that exact episode
  via the new `/3/tv/{id}/season/{n}/episode/{e}/images` endpoint.
  Click any tile to pin it; click `Auto` to clear the override and
  let the resolver pick the highest-rated upload again. Selections
  are keyed to the provider's episode id (`episode-thumb-{id}`), not
  the file path, so renames preserve them; the existing
  `artwork_selections` sidecar serializer carries them to disk
  unchanged. TVDB-bound shows still ship a single still per episode,
  so the picker degrades to a one-tile view with a note suggesting a
  TMDB switch.
- **In-app confirm / prompt dialogs.** Every destructive or
  input-driven action that used to fire a native
  `window.confirm` / `window.prompt` popup now opens an in-app
  modal. Affected sites: library remove, prune missing / empty,
  remove selected, library Danger Zone wipe + sidecar blast, per-show
  wipe + remove, schedule delete, custom artwork URL add + delete,
  and the rename modal apply step. The new `ConfirmDialog`
  component (and its `useConfirm()` / `usePrompt()` hooks) auto-focus
  the confirm button so hitting `Enter` immediately accepts — no
  mouse trip required for the common case. `Esc` or a backdrop click
  cancels. Destructive variants render the confirm button in hazard
  yellow to match the Danger Zone styling.

### API

- `GET /api/episodes/thumb-candidates?path=&season=&episode=` —
  returns every TMDB still candidate (or the lone TVDB image) for
  the given episode, with each candidate marked `is_default` /
  `selected` and a human-readable `note` field for the TVDB case.
- `POST /api/episodes/thumb-select` — body
  `{folder_path, external_id, url|null}`. Stores the selection in
  the existing `artwork_selections` table under slot
  `episode-thumb-{external_id}`; passing `url: null` clears it.

### Internals

- `tmdb.tv_episode_images()` calls
  `/3/tv/{id}/season/{n}/episode/{e}/images` and returns the raw
  `stills` list.
- TMDB series builder reads `db.get_artwork_selections(folder)` and
  honours an `episode-thumb-{id}` slot before falling back to the
  default `still_path`. TVDB series builds are unaffected because
  TVDB only ships one image per episode.
- README intro trimmed to a single high-level sentence; the per-show
  feature bullet now reads "Per-episode thumbnail picker (TMDB)".

## 0.11.8 — 2026-05-06

A small TMDB-parity release that closes three rough edges between the
TVDB and TMDB code paths.

### Fixed

- **Jobs view always showed `0/0` for TMDB-bound shows and movies.**
  Only the TVDB build paths set `job["total"]` and incremented
  `job["progress"]`; the TMDB series build, TMDB movie build, and
  even the TVDB movie build silently skipped progress reporting.
  Episodes were being written correctly underneath, but the UI
  couldn't show it. All four build paths now publish per-step
  progress (`1/(1 + #episodes)` for series, `1/1` for movies),
  including a tick for unparseable / unmatched files so the counter
  doesn't stall when a fansub release has stray files we can't map.
- **Manual match dropdown defaulted to `Movie` inside a TV library.**
  The Detail view derived the dropdown's initial value from
  `state.kind`, which is empty until the folder has been scanned at
  least once. That fell back to `"series"` for unbound folders, but
  was still flipping to `"movie"` whenever the per-folder scanner
  bucketed a single-video TV folder as a movie (common during early
  downloads). The detail endpoint now also returns the parent
  library's declared kind (`tv` / `movies`), and the manual-match
  panel uses that as the default for unbound folders.

### Added

- **Episode thumbnails gallery** in the Overrides tab. For every
  matched episode, the gallery shows the still pulled from the
  metadata source on the left and the existing
  `<stem>-thumb.{jpg,jpeg,png}` file already on disk on the right,
  grouped by season with collapsible season headers. This
  intentionally lives in the Overrides tab rather than the Artwork
  tab — per-episode stills would clutter the series-level artwork
  picker. The new view also adds a `matched_image` /
  `local_thumb` field to `GET /api/episodes` for any tooling that
  wants the same data.

### Internals

- TVDB episode `image` paths returned by `/api/episodes` are now
  absolutized via `artwork_svc.absolutize_tvdb_url` before being sent
  to the frontend. TMDB stills are normalised at the top of the
  endpoint as before.

## 0.11.7 — 2026-05-06

Makes the per-folder NFO status actually explainable, and gives the
renamer a manual release-group override for anime fansub files where
the group name can't be auto-detected.

### Why does this folder say "partial"?

The library list shows a one-word status pill on every show / movie
(`none` / `partial` / `mixed` / `foreign` / `complete`). That bucketed
label is fine for filtering, but it told you nothing about *which* file
was actually missing or what to do about it. A user staring at
`partial` on a 24-episode show couldn't tell whether one episode was
uncovered, ten were, or the show NFO itself was foreign.

### Added

- **Status breakdown panel** on the Detail page. The status pill in
  the title bar is now clickable; clicking it opens an inline panel
  that re-walks the folder live and reports:
  - Whether `tvshow.nfo` (or `movie.nfo`) is missing, present, or
    foreign (i.e. NFO exists but lacks the plex-nfo-builder provenance
    comment).
  - Total episode-NFO coverage as `built / total videos`.
  - Total foreign episode NFOs (those that didn't come from this app).
  - A bulleted list of human-readable reasons ("Season 02: 6 of 12
    episode NFOs present.", "3 episode NFOs were not written by
    plex-nfo-builder. Force rebuild to overwrite them.", etc.).
  - A per-season coverage table with one row per season showing video
    count, NFO count, missing count, foreign count, and whether
    `season.nfo` exists. Click *show files* on any troubled row to
    expand the actual filenames that are missing or foreign.
  - A list of any video files sitting at the series root rather than
    inside a `Season XX/` subfolder (the renamer / NFO writer still
    counts those, but Plex usually wants them in a season folder).
  - A short legend distinguishing `partial`, `mixed`, and `foreign`,
    with a hint to use *Force rebuild* to overwrite foreign NFOs.
- New `GET /api/items/nfo-explain?path=...` endpoint that powers the
  panel. Returns a structured payload (status, kind, video / nfo /
  foreign-nfo counts, per-season list with up to 50 missing /
  foreign filenames each, root-level orphan video list, and the
  human-readable reasons array). The bucketing logic mirrors the
  scanner's existing `_scan_nfo_state` so the panel agrees with the
  library list.
- New `scanner.explain_nfo_state(folder, kind)` helper backing the
  endpoint. Walks the folder once, records every contributing fact,
  and never throws on a single unreadable subdirectory.

### Renamer: manual release-group override

Anime fansub layouts often use bracket patterns the auto-detector
can't safely guess (e.g. `[Group A][Group B]Title - 01 [1080p].mkv`).
The `{Release Group}` token in the rename template comes out empty,
leaving you with `Title - 01 [1080p]-.mkv`-style results.

- **Release group input** added to the rename modal next to the
  Series-type selector. Type a value (e.g. `SubsPlease`) and every
  plan item uses that as the `{Release Group}` token, including the
  `{-Release Group}` conditional. Press Enter or blur the field to
  re-preview. *Clear* button restores auto-detection. Empty value
  keeps the existing behaviour (auto-detect from the filename).
- `POST /api/episodes/rename/preview` and `POST /api/episodes/
  rename/apply` now accept `release_group: str | None` and forward
  it to the renamer.
- `renamer.plan_series_rename` and `renamer.plan_movie_rename` got a
  `release_group_override` kwarg. When set, it short-circuits
  `mediainfo.extract_release_group()` for that run.

## 0.11.6 — 2026-05-06

Fixes artwork resolution for non-English shows and movies. Anime,
K-dramas, C-dramas, telenovelas, and other foreign-language titles
will now actually pull posters and backdrops from TMDB instead of
coming up empty.

### Why

TMDB's `/3/{tv,movie}/{id}/images` endpoint applies the
`include_image_language` query as a **server-side filter**. Previously
the app hard-coded `include_image_language=null,en`, which means any
poster that an uploader tagged with a non-English language flag (e.g.
`ja` for a Japanese anime, `ko` for a Korean drama) was filtered out
before the response ever left TMDB's servers — even though the
artwork is published, public, and not under moderation. Example case
that surfaced this: [Joshi Ochi! 2-kai kara Onna no Ko ga Futte
Kita](https://www.themoviedb.org/tv/81044) has many fan-uploaded
posters, all flagged `ja`, all invisible to the previous filter.

### Changed

- **Auto-resolver** (`artwork_resolver.resolve_preferred_artwork_*`)
  now reads each title's TMDB `original_language` and includes that
  language flag in the image request alongside `null,en`. So a title
  whose original language is Japanese will now see Japanese-tagged
  posters as candidates; a Korean title will see Korean-tagged
  posters; etc. English-original titles still see only `null,en` so
  they don't get a foreign poster picked for them automatically.
  TMDB orders images by `vote_average DESC` so the highest-rated
  upload still wins regardless of language tag.
- **Manual artwork picker** (the modal you open from a show's
  detail page) now requests **all languages** from TMDB — both for
  TMDB-bound titles and for TVDB-bound titles where TMDB
  supplementation is enabled. When you're hand-picking artwork the
  app no longer filters out images on your behalf; you see every
  uploaded poster, backdrop, and season poster TMDB has.
- `tmdb.tv_images`, `tmdb.tv_season_images`, and `tmdb.movie_images`
  now accept `languages: list[str] | None` and
  `include_all_languages: bool`. When `include_all_languages=True`
  the `include_image_language` param is omitted entirely so TMDB
  returns the full unfiltered set.

### Added

- `_ISO_639_3_TO_1` mapping in `tmdb.py` for normalising ISO 639-3
  language codes (which TVDB tends to emit) into the 2-letter ISO
  639-1 codes TMDB expects.

## 0.11.5 — 2026-05-06

Adds a **Prune empty** action so you can clean up show / movie folders
that contain only generated NFOs and artwork — the classic case of a
show you deleted from disk but whose folder lingered behind because the
builder kept regenerating `tvshow.nfo` + posters into it.

### Added

- **⚠ Prune empty** hazard-yellow button on every library toolbar
  (next to *Prune missing*). Runs a dry-run preview that lists every
  tracked folder under the current library which exists on disk but
  contains zero recognised media files (mkv / mp4 / m4v / avi / mov /
  ts / webm / wmv / flv at any depth), confirms before touching
  anything, then forgets those rows in the database.
- New `POST /api/items/prune-empty` endpoint backing the button.
  Accepts `library`, `dry_run`, and `delete_files` (off by default —
  the UI never sends it, only forgetting the DB row).
- New `scanner.folder_has_media(folder)` helper — iterative DFS that
  bails out on the first video file. Errors and permission problems
  are treated as "has media" so unreadable subtrees can never be
  pruned by accident.

### Safety

- Every candidate is **re-walked on the live filesystem immediately
  before deletion**, not just at preview time. A download that lands
  between the preview and the user's confirmation is detected and that
  folder is skipped (and reported back to the UI).
- Files on disk are **never** removed by Prune empty — only the
  database row is forgotten. The folder stays on disk for the user to
  delete manually with their file manager. Even the optional
  `delete_files=True` mode (not exposed in the UI) defers to the
  existing cleaner, which has always refused to touch anything that
  matches the video / audio / subtitle extension list.
- Folders missing on disk are skipped entirely — use the existing
  *Prune missing* action for those.

## 0.11.4 — 2026-05-06

Quality-of-life polish: know what version is actually running, find the
shows that still need work, browse libraries in the same order Plex /
Sonarr / Radarr use, and stop losing your scroll position when you back
out of a detail page.

### Added

- **Backend version chip in the top bar.** When you run the
  `:latest` Docker tag and a new release rolls in, you can confirm at a
  glance which numbered version is actually live (e.g. `v0.11.4`).
  Hover for tooltip context. Settings → About lists the same info
  alongside the repo link and the matching
  `ghcr.io/cooper8386/plex-nfo-builder:vX.Y.Z` container tag.
- **`GET /api/version`** returns `{version, name, repo}`. The same
  version is now also reported by `GET /api/health` and the FastAPI
  OpenAPI doc (`/docs`).
- **"Needs work / Complete / All" filter pill** on every library
  toolbar. "Needs work" surfaces folders whose status is
  `none / partial / stale / foreign / mixed` — anything you'd want to
  open and finish. "Complete" shows only the green ones. The choice is
  remembered per-library so each library opens to whichever filter you
  used last.
- **Sort title support.** Library grids and lists now order shows the
  way Plex/Sonarr/Radarr do — leading articles (`The`, `A`, `An`) are
  ignored, and any manual `sorttitle` override you've set on the
  Overview → Overrides tab wins. The Yamato/Star Blazers split-anime
  case (one TVDB record but four community-recognized shows) Just
  Works: set `sorttitle` to `Star Blazers 2199 / 2202 / 2205 / 3199`
  on each folder and they'll cluster together.
- **Scroll restoration.** Clicking a poster, then hitting the back
  arrow or your mouse-back button, now drops you back at exactly the
  scroll position you came from instead of the top of the library.
  Bounded to the most recent ten library views to keep memory tiny.
- **Settings rewrite.** New left-rail subnav splits the page into
  Metadata · Providers · Artwork · Plex · Renaming · Schedules ·
  About panes with a single sticky save bar at the bottom. The Plex
  test result is now an inline card under the Plex form. The Renaming
  pane groups Episodes / Folders / Movies and the token reference
  collapses out of the way.

### Changed

- `item_state` gains a `sort_title TEXT` column with a backfill
  migration; `list_item_state` orders by `COALESCE(sort_title, title)`.
  Editing a series or movie `sorttitle` override now refreshes the
  cached sort key automatically.
- The scanner and builder both populate `sort_title` on every upsert
  (override → provider `sortName` → leading-article-stripped fallback).
- `GET /api/items` accepts a comma-separated `status=` query
  parameter and the legacy `hide_organized=1` flag, both consumed by
  the new filter pill.

## 0.11.3 — 2026-05-06

Manual secondary provider id — the missing piece for shows whose TVDB
record doesn't list a TMDB id (or vice versa).

### Added

- **Manual secondary TMDB / TVDB id per binding.** A new "Secondary
  source" panel on the Detail view → Overview tab lets you pin a
  cross-source id on top of your primary binding. Two ways to set it:
  paste the id directly, or search the other provider in-place and link
  the right hit. Once set, a chip shows the linked id (e.g.
  `tmdb-12345`) with an external-link button straight to the source
  page, plus Edit and Clear actions.
- The manual secondary id is consumed in three places: the
  cross-provider artwork resolver (so a TVDB-bound show can still pull
  TMDB / fanart.tv artwork even when TVDB doesn't list a TMDB id),
  the fanart.tv lookup, and the NFO `<uniqueid>` block (Plex / Kodi
  see both ids on the same record).
- **Sidecar persistence.** The new `secondary_provider` /
  `secondary_external_id` fields are written to
  `.plex-nfo-builder.json` and restored after a DB wipe alongside the
  primary binding.
- **API.** New `POST /api/match/secondary` endpoint; rejects a
  secondary that matches the primary provider, accepts both fields
  null to clear.

### Changed

- `bindings` table gains `secondary_provider` and
  `secondary_external_id` columns. Migration is idempotent; existing
  bindings are untouched until you set a secondary id.

## 0.11.2 — 2026-05-06

Three quality-of-life fixes that came out of using v0.11.1 against a real
library with a chunk of anime in it.

### Fixed

- **Renamer was using non-preferred-language titles for non-English
  shows.** Episode titles correctly came back in your preferred language,
  but the *series* title was whatever the provider returned at match time
  — for non-English originals (most anime, foreign films), that's the
  original-language name. Result: a folder called *Adam's Sweet Agony*
  that was matched in English would still rename files to `問えてよ、
  アダムくん (2024) - S01E01 - …`. The renamer now re-fetches the title
  from TVDB (`best_translation` with your fallbacks) or TMDB
  (`tv_details` / `movie_details` with `language=`) at rename time, so
  the preferred language in Settings is the language for *all* fetched
  information — including the title plugged into the rename template.
  Falls back to the bound title on any provider hiccup so renames never
  break.

### Added

- **TVDB / TMDB external link in the Detail view.** Every matched folder
  now shows a small `TVDB ↗` or `TMDB ↗` chip next to the title, linking
  straight to the public source page for that record. Only the link for
  the bound provider is rendered — opens in a new tab.

### Changed

- **Library Danger Zone moved to the bottom of the Library page.**
  Having a hazard-yellow panel scream at you the moment you open a
  library was the wrong default. It now sits below the grid/list, where
  you only see it after you've scrolled past your media. Same
  collapsible component, same buttons, same dry-run-then-confirm flow.

## 0.11.1 — 2026-05-05

Bug-fix follow-up to v0.11.0 plus a small but long-overdue feature: a
proper library-wide Danger Zone with two big hazard-yellow buttons.

### Added

- **Library Danger Zone.** Each Library page now has a collapsible
  hazard-yellow panel at the top with two destructive buttons that run
  across every folder tracked under the current library:
  - **Wipe ALL NFOs + artwork** — same as the per-show Wipe button, but
    applied to the whole library at once. Sidecars are preserved.
  - **Blast every sidecar (`.plex-nfo-builder.json`)** — deletes every
    sidecar in the library. Database and NFOs are untouched.

  Both buttons run a dry-run preview, show the exact file count, and
  require an explicit confirmation before touching disk. New API:
  `POST /libraries/{name}/wipe-nfo` and `POST /libraries/{name}/wipe-sidecars`.

### Fixed

- **Renamer leaves orphan `.nfo` and `-thumb.jpg` files.** When you
  renamed a video file, the matching sidecar `.nfo` and Plex thumbnail
  stayed under the old name. The renamer now moves every recognised
  companion file (`<stem>.nfo`, `<stem>-thumb.{jpg,jpeg,png}`, and
  language-tagged subtitle sidecars `.srt` / `.ass` / `.ssa` / `.vtt` /
  `.sub` / `.idx` / `.sup`) in lockstep with the video.
- **Wipe NFOs & artwork doesn't delete `-thumb.jpg` files.** The cleaner
  only knew about the show-level artwork filenames and `season.nfo`. It
  now wipes any `*-thumb.{jpg,jpeg,png}` file sitting next to a video
  (top-level for movies, inside Season folders for episodes), including
  orphans left over from a previous rename.

## 0.11.0 — 2026-05-05

Sonarr/Radarr-compatible naming, MediaInfo via ffprobe, and a stack of
polish fixes from the v0.10.0 shake-out. The renamer now speaks the same
token grammar as Sonarr/Radarr (Trash Guides defaults included), reads
codec / HDR / audio metadata directly from the file with `ffprobe`, and
fixes the long-standing Specials-poster filename mismatch with Plex.

### Added

- **MediaInfo extraction.** New `services/mediainfo.py` shells out to
  `ffprobe` (now included in the container) and returns video codec,
  resolution, bit depth, HDR/Dolby Vision flags, audio codec/channels,
  language, and 3D type. Results are cached per-file by inode + size +
  mtime so the renamer is fast on repeat runs.
- **Sonarr/Radarr token grammar.** `services/renamer.py` rewritten
  with a real expression engine — `{Series TitleYear}`, `{[Quality Full]}`,
  `{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}`,
  `{-Release Group}`, `{tvdb-{TvdbId}}`, `{(Release Year)}`, `{season:00}`,
  and conditional groups all behave the way Trash Guides documents them.
- **Series-type selector + new template fields.** Settings has five new
  template fields (`rename_daily_template`, `rename_anime_template`,
  `rename_series_folder_template`, `rename_season_folder_template`,
  `rename_movie_folder_template`) all defaulted to the exact Trash Guides
  recommendations. The rename modal in the Episode Mapper has an
  Auto/Standard/Daily/Anime selector that picks the right template.
- **Container ffmpeg.** `Dockerfile` now installs `ffmpeg` so MediaInfo
  works out of the box.

### Fixed

- **Specials season poster filename.** Plex looks for
  `season-specials-poster.<ext>` for season 0 — we were writing
  `Season00-poster.jpg`, which Plex silently ignored. Builder now writes
  the correct filename, and Wipe / preview-clean recognise both the new
  name and the legacy `Season00-poster.jpg` so old files get cleaned up
  on the next pass.
- **Provider labels.** Several screens still said "TheTVDB" / "TheMovieDB"
  in the dropdowns and field labels (DetailView, SettingsView,
  OverridesTab, HelpView, Episode Mapper). Standardised to **TVDB** and
  **TMDB** everywhere.

### Changed

- Rename templates now use Sonarr/Radarr syntax. Old `{title}`-style
  templates from v0.10.0 still render via the legacy fallback path, but
  the new defaults match Trash Guides exactly. Resetting any field in
  Settings → Renaming gives you the canonical default back.
- README and HelpView fully refreshed with the new token reference,
  conditional-group rules, MediaInfo block, and the corrected season-
  poster filenames.

## 0.10.0 — 2026-05-05

Large-scale episode mapping & file rename overhaul. The Episodes tab is now
anchored to local file paths (so anime fansub releases that all parse as
`S00E00` no longer collapse onto a single row), and there's finally a built-in
renamer that rewrites loose filenames into your Sonarr-style scheme.

### Added

- **Anime / fansub filename parser.** New `ANIME_RE` regex in
  `services/parser.py` matches `[Group] Title - NN [tags].mkv` and
  `[Group] Title - NNvN [tags].mkv` and treats the bare episode number as
  S01E`NN`. Sonarr-style `SxxExx` names still take precedence.
- **Per-file episode overrides.** New `episode_file_overrides` table in SQLite
  keyed on `(folder_path, file_path)` — every local file gets its own
  season/episode/external-id slot. Sidecar carries the new map under
  `episode_file_overrides` so a DB wipe restores them from disk.
- **Rename-to-scheme feature.** New `services/renamer.py` plus three API
  endpoints (`/api/episodes/rename/preview`, `/api/episodes/rename/apply`,
  `/api/episodes/override-file`). Sonarr-style template rendering with token
  fallback, sanitised filenames, `{quality}` token best-effort extracted from
  the original stem, conflict detection (`exists` / `duplicate`), atomic
  per-file rename via `os.replace`, and per-file override migration so your
  bindings survive the rename.
- **Settings → Renaming.** Three new fields: `rename_episode_template` (default
  `{title} ({year}) - S{season:02}E{episode:02} - {episode_title}{ext}`),
  `rename_movie_template` (default `{title} ({year}){ext}`), and
  `rename_enabled`.
- **README + Help.** Both now have an Episode mapping & renaming section,
  including the supported filename styles, the rename modal flow, and the full
  token list. Going forward every release that ships a user-visible change
  updates these alongside the code.

### Fixed

- **Episodes tab said "TVDB Episode" even on TMDB-bound shows.** The header
  now follows the active binding's provider — TMDB-bound series read "TMDB
  Episode", TVDB-bound series read "TVDB Episode".
- **"Override saved for S00E00" with no actual change.** Anime filenames had
  no `SxxExx` and no daily date, so every file collapsed to `(0,0)` in the
  old `(season, episode)`-keyed override table. With per-file override rows
  plus the new anime parser this no longer happens — each file has its own
  row and its own override slot.

### Changed

- **Episodes tab rewritten.** Rows are now keyed by file path. Unparsed files
  show inline season/episode pickers. Provider-aware column header. A new
  Rename modal shows a dry-run diff with per-row checkboxes (auto-checked
  except for unchanged or conflicting rows) and conflict badges.
- **DetailView decluttered.** The action row collapses secondary actions
  (Wipe, Refresh in Plex, Remove from library) into a `•••` overflow menu;
  Build / Force rebuild stay primary, and a new "Change match" toggle reveals
  the search panel only when you ask for it. Unbound folders get a prominent
  empty-state card guiding you to bind.
- **Cascade cleanup.** `forget_folder` and `delete_library` now also clear
  `episode_file_overrides` rows, matching the existing override tables.

## 0.9.3 — 2026-05-05

### Fixed

- **TMDB manual search returned no results for adult-flagged shows.**
  Searching for `Shishunki no Obenkyou` (TMDB tv `153655`) showed
  "No results yet." even though the series exists on themoviedb.org,
  because TMDB's `/search/tv` defaults `include_adult=false` and hides
  the entry. `TMDBClient.search()` now passes `include_adult=true` by
  default — this is a desktop NFO scraper, not a user-content surface,
  so adult-flagged anime should be findable.
- **TMDB search now falls back to `en-US` when a non-default language
  returns nothing.** Niche anime is often only indexed under the
  romaji/English title on TMDB, so a search constrained to e.g. `ja-JP`
  silently returned zero hits even when results existed. After a
  language-scoped query comes back empty, the client retries in `en-US`
  (with and without the year filter) before giving up.

## 0.9.2 — 2026-05-05

### Fixed

- **Stale `kind=movie` bindings keep 404'ing the build.** v0.9.1 stopped
  *creating* mis-kinded bindings, but folders that were auto-matched by
  v0.9.0 still had `kind="movie"` for what is actually a TV id, so every
  Build click hit `TMDB GET /movie/<tv_id>` and failed. v0.9.2 self-heals
  these bindings in three places:
  - **Builder**: when `_build_movie_tmdb` 404s on the bound id, retry as
    `tv_details`, rewrite the binding to `kind="series"`, and continue
    the build through the series path. Mirror logic when
    `_build_series_tmdb` 404s.
  - **Scanner**: a regular library scan now compares each binding's
    stored `kind` against the folder's actual content (season subdirs
    vs. parsed-episode files) and rewrites it when they disagree, so
    the wrong-kind binding is corrected without forcing a rebuild.

## 0.9.1 — 2026-05-05

### Fixed

- **Series with no `Season XX/` subdir misidentified as a movie.** Short
  Sonarr-managed shows and OVAs that drop episodes at the folder root
  were classified as movies by v0.9.0's per-folder routing, causing the
  TMDB matcher to call `movie_details(<tv_id>)` and 404 (e.g.
  `TMDB GET /movie/153655 failed 404`). The movie-shape heuristic now
  also requires that **none** of the root video files parse as an
  episode — a folder where any file has `SxxExx` or a Sonarr daily
  `YYYY-MM-DD` is treated as a series.
- **TMDB folder-id 404s now retry as the other kind.** Belt and braces:
  if `auto_match_movie_tmdb` 404s on the folder-tagged id, we retry it
  as `tv_details` (and vice versa for `auto_match_series_tmdb`) before
  giving up. Catches the residual cases where the heuristic still picks
  the wrong kind but the `{tmdb-...}` id is correct for the other.
- **Episodes tab returned "internal server error" for TMDB-bound
  series.** The `/api/episodes` endpoint always queried TVDB, even for
  folders bound to TMDB. It now reads the binding's provider and pulls
  episodes from `tv_details` + `tv_season` on TMDB. Loose video files at
  the series root and unparseable videos now also surface in the mapper
  (instead of silently disappearing).
- **Hard-refresh on a library or detail page no longer kicks you back
  to "Select a library".** The Sidebar's deselect-when-missing effect
  was firing before the libraries query had returned, briefly seeing an
  empty list and clearing the active library. It now waits for the
  query to load before deciding the library is actually gone.

## 0.9.0 — 2026-05-05

### Added

- **Sonarr/Radarr-aware file detection.** The scanner now recognises the
  full set of [Trash-Guides recommended naming schemes](https://trash-guides.info)
  out of the box: Sonarr standard (`SxxExx`), Sonarr daily
  (`Title - YYYY-MM-DD - Episode Title`), Sonarr anime (extra bracketed
  `[10bit]` / `[JA]` tags), and Radarr movies including
  `{edition-…}` tags. Edition tags are stripped before the provider id
  match so `Mad Max (1979) {edition-Directors Cut} {tmdb-8810}` parses
  cleanly.
- **Always-show video files.** Video files that don't match any naming
  scheme are no longer silently dropped. They show up in the file count
  and in the unmatched list so you can bind them manually instead of
  the folder appearing empty. Recognised extensions now also include
  `.wmv` and `.flv`.
- **Per-folder kind routing.** A Radarr movie sitting in a TV-classified
  library (common in mixed anime libraries) is now scanned, matched, and
  built as a movie regardless of the library's declared kind. The
  detection is content-based: season subdirs ⇒ series, otherwise direct
  video files ⇒ movie.
- **Folder-id fast path on every matcher.** When a folder name carries
  an explicit `{tvdb-…}` or `{tmdb-…}` tag, all four auto-matchers
  (TVDB/TMDB × series/movie) trust the tag, bind, and return early — no
  fuzzy fallback that could overwrite the binding with a noisier hit.
- **`extras/` recognised as season 0.** The Sonarr `extras/` convention
  is now treated like `Specials/` for season detection.
- **Series root videos.** Loose video files dropped at the series root
  (rather than under `Season XX/`) are picked up and counted as season 0
  episodes so the UI doesn't claim the folder is empty.

### Fixed

- **TMDB auto-match for movies no longer falls through.** A folder
  tagged `{tmdb-NNNN}` could previously have its TMDB binding
  overwritten by a TVDB search hit because the matcher kept executing
  past the tmdb branch. Both the folder-id and filename-id paths now
  return as soon as the binding is written.
- **TMDB series matcher no longer 404s on movie ids.** When a Radarr
  movie folder ended up classified as a series the TMDB matcher would
  call `tv_details(movie_id)` and return nothing. The matcher now routes
  movie-shaped folders to the movie matcher even when invoked from the
  series entry point.
- **Year filter is no longer a hard filter.** TVDB and TMDB searches
  retry without the year if the year-filtered pass is empty or all
  candidates score below 70 — anime release years often disagree across
  databases by ±1. The year bonus also went up from +5 to +15 (with a
  smaller +5 for off-by-one) so a correctly-dated candidate beats a
  same-titled remake that's a year off.
- **Manual TMDB search returns results when year is wrong.** The
  manual-search endpoint mirrors the year-fallback behaviour so users
  searching with the folder's year still see candidates instead of an
  empty list.
- **DetailView "Kind" defaults to the detected kind.** Previously the
  manual-match dialog always opened with `series` selected, so users
  searching for a movie had to flip the dropdown every time and could
  bind the wrong kind by mistake. The picker now opens on the folder's
  detected kind and only stays sticky once the user changes it manually.

### Changed

- `_detect_kind` (used by `/build` and the bulk matchers) now consults
  the folder content first and falls back to the library kind, so
  building a single mixed-library item works without manually specifying
  `kind` in the request.
- `_pick_best_tmdb` adds a small popularity tiebreaker (capped at +3)
  so a popular hit beats an obscure same-title match when both score
  equally.

## 0.8.0 — 2026-05-05

### Added

- **Custom tags per item.** You can now add your own tags to a series or
  movie from the DetailView. Custom tags are appended to (never replace)
  the genres pulled from TVDB/TMDB and are emitted into the NFO as both
  `<genre>` and `<tag>` so Plex picks them up automatically. Removing a
  custom tag is a click on the × next to the chip.
- **Tag source toggle.** The DetailView now shows the genres/keywords
  fetched from each metadata source side-by-side: switch between TVDB
  genres, TMDB keywords, and your custom tags from a single tab strip.
  When the item is bound to TVDB the panel also surfaces TMDB keywords
  (resolved through TVDB `remoteIds`) so you can preview what the other
  source would emit before swapping providers.
- **Scheduling.** A new Schedules section in Settings lets you run
  scans, auto-matches, and builds on a cron schedule. Actions:
  `scan_only`, `match_only`, `build_only`, `match_and_build`, and `full`
  (scan + match + build). Schedules can target a specific library or
  every enabled library. Each row has an "Run now" button for manual
  triggers and shows the last run timestamp + status badge. Cron
  expressions are evaluated in UTC; presets cover the common cadences
  (daily 3am, weekly Sunday, every 6 hours, hourly).
- The scheduler skips items with nothing new — a fully-built series whose
  local episode count hasn't changed since the last build is left alone
  so a daily "full" schedule is cheap to leave running.
- New REST endpoints: `GET/POST /api/schedules`, `PATCH/DELETE
  /api/schedules/{id}`, `POST /api/schedules/{id}/run`, plus
  `POST /api/items/tags` and `DELETE /api/items/tags`.
- Custom tags survive a Wipe → Restore round-trip via the
  `.plex-nfo-builder.json` sidecar (new `custom_tags` field, schema
  version unchanged).

### Changed

- **"Auto-match all" actually does something now.** The button used to
  send `only_unmatched: true` to the backend, which short-circuited to
  zero work whenever every folder was already bound. The frontend now
  asks the backend to re-resolve every folder in the library, matching
  the documented behaviour.
- **Removed the status filter.** The Topbar no longer has the Filters
  dropdown and item lists no longer hide items based on NFO status. All
  items in a library are shown by default; status colour-codes still
  surface the state at a glance. The legacy `status` and `hide_organized`
  query params on `/api/items` are accepted for back-compat but the UI
  no longer sends them.

### Notes

- The scheduler runs entirely on stdlib `asyncio` — no APScheduler
  dependency. The cron parser supports numbers, `*`, `*/N`, `a-b`, and
  comma lists, which covers every preset surfaced in the UI.
- New DB tables: `custom_tags(folder_path, tag, created_at)` and
  `schedules(id, library, cron, action, enabled, last_run, last_status,
  last_message, created_at, updated_at)`. Existing databases are
  migrated automatically on first launch.

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
