# Engineering audit and implementation — 10 September 2026

## Scope and baseline

Worked from the local `codex/refactor` branch at `dd910e5`. The starting working tree was clean. The audit preserved Git history and made no remote changes; publication was authorized separately afterward. Reviewed the backend services, routes, persistence, frontend workflows, tests, manifests, Docker configuration, CI, README, and in-app help. Searches covered broad exception handling, suppressions, unfinished work, duplicate filesystem logic, and provider/file boundaries.

Baseline: **114 backend tests passed**, Ruff and the existing mypy configuration passed; frontend typecheck/build passed, ESLint reported **12 warnings**, and there was no frontend test runner. Dependency audits identified vulnerable packages. The initial production JavaScript entry was **393.63 kB / 112.90 kB gzip**. Docker is not installed on this host.

## Important issues fixed

| Severity | Root cause | Implemented fix |
|---|---|---|
| P0 | Shared NFO writer replaced foreign metadata despite the overwrite setting. | Enforce provenance/overwrite policy at the writer, including forced builds. |
| P0 | Renames could replace a target that appeared after preview, and companion moves could partially fail. | No-overwrite moves, companion collision preflight, rollback, transactional mapping updates, and optional expected-plan validation with HTTP 409 on stale previews. |
| P1 | Cleanup classified show/season metadata as orphan episodes; linked season paths could leave the item boundary. | Shared companion rules, resolved containment checks, protected metadata names, and conservative behavior without live video files. |
| P1 | Watcher called a keyword-only argument positionally and created async jobs in a worker thread. | Create jobs on the event loop; wait for stable snapshots and requeue imports arriving during a build. |
| P1 | Artwork fetching accepted internal addresses, unchecked redirects/content, and unsafe uploads. | Validate and pin public DNS addresses, revalidate redirects, bound transfers, verify raster signatures, and atomically preserve previous files. Local uploads now build correctly. |
| P1 | Atomic media writes used private temporary-file permissions, making generated artwork/NFOs unreadable to a separate Plex user on Linux. | Create exclusive sibling files using the process umask and preserve existing regular-file permissions without following symlinks; keep configuration files private. |
| P1 | Settings could truncate on write; concurrent frontend responses could erase drafts. | Atomic replacement, fail-closed corrupt configuration, patch-only saves, and merging delayed connection responses into the latest draft. |
| P1 | Restoring malformed sidecars could partially mutate the database or accept escaping file references. | Validate structure and paths before transactional recovery. |
| P1 | Explicit matches could change identity during outages; provider switches reused unrelated numeric IDs. | Preserve pinned IDs, validate provider changes, require a new provider's ID, and reject invalid media kinds before mutation. |
| P1 | Scanner/build/rename disagreed about root, daily, multi-episode, and manually mapped files. | Share safer parsing and apply file mappings consistently; preserve multi-episode ranges. |
| P1 | Internal library operations stopped silently at 5,000 items. | Remove the internal limit; expose API pagination and load all pages in the client. |
| P1 | Missing/expired build jobs could lock detail controls; cancelled jobs could appear successful. | Poll the requested job directly and handle missing/cancelled states explicitly. |
| P1 | Provider search failures surfaced as generic HTTP 500 responses. | Narrow provider/transport error handling with actionable 502/503 responses; cached searches still work without keys. |
| P1 | The artwork picker only exposed slots returned by the provider, disabling uploads when no candidates existed. | Keep standard local artwork slots available, regardless of provider coverage. |
| P2 | Build jobs, provider clients, scanner workers, and SQLite could outlive shutdown. | Explicit ownership, two-build concurrency limit, duplicate-folder coalescing, cancellation draining, and client/database closure. |

Additional fixes include invalid XML character/comment sanitization, bounded ffprobe caching, safer token storage fallback, stale-401 protection, authentication-scoped confirmation lifetime, completed-scan refreshes, and preserved selection boundaries when filtering or switching libraries.

## Architecture

Backend responsibilities now have cohesive modules: `routes/settings.py` owns settings validation/persistence; `services/jobs.py` owns job lifecycle; `artwork_download.py` owns safe transfers; `async_io.py` owns cancellation-safe thread work; `provider_http.py` shares retry-delay handling; `media_files.py` preserves safe permissions for atomic NFO/artwork writes. Existing provider, scanner, NFO, filesystem, and database interfaces remain recognizable.

Frontend detail work is divided among source matching, diagnostics, episode mapping, artwork, overrides, and an explicit rename workflow. Settings has separate automation controls, reusable fields, and a typed settings model. Shared native dialogs manage focus, Escape, queued confirmations, and cancellation on sign-out. Library presentation, maintenance actions, filtering/sorting helpers, and shell navigation have separate owners. Secondary pages load on demand.

Large API, builder, and database modules still contain legacy sections. They were not mechanically split merely to reduce line counts.

## Interface changes

- Charcoal surfaces, amber action emphasis, consistent controls, spacing, typography, borders, status text, visible focus, and reduced-motion support.
- Persistent library context and compact navigation for Library, Activity, Automation, Logs, Settings, and Help.
- Library grid/list modes with search, sorting, status filters, loading/error/empty states, lazy posters, and separated maintenance previews.
- Detail header with source/status context, progressive tabs, source matching, effective episode mappings, local artwork, metadata overrides, and build feedback.
- Responsive settings categories, Security pane, accurate save state, and unsaved-change protection.
- Destructive dialogs focus Cancel, show scope, trap keyboard focus, restore focus, and retain dry-run behavior. Editing rename options invalidates execution until another preview is generated.

## Verification and safety

Tests exercise temporary directories and mocked/cached providers. `backend/tests/qa_server.py` creates fictional media, isolated settings and SQLite state, clears provider credentials, and disables the watcher. Browser tests require this fixture before operating. They cover desktop and 390px layouts, authentication, library search/filter/sort, grid/list modes, scan completion, destructive previews/cancellation, episode mappings, rename previews, settings drafts, matching, saved metadata, uploaded artwork, builds, Activity, and Logs. Mutating browser checks verify generated NFOs, artwork, and sidecar files in the temporary fixture.

Production dependencies were updated to resolve audit findings, without changing the React/FastAPI stack. Vite moved to the smallest patched supported major available for the Windows file-read advisory; FastAPI/Starlette, multipart parsing, and lxml received security fixes. Primary references: [Vite advisory](https://github.com/advisories/GHSA-fx2h-pf6j-xcff), [Starlette advisory](https://github.com/advisories/GHSA-2c2j-9gv5-cj73), [FastAPI release notes](https://fastapi.tiangolo.com/release-notes/), and [lxml changes](https://lxml.de/changes-6.1.0.html).

API mutations require header authentication; query tokens are accepted only for GET/HEAD. Tokens are redacted from application/access logs. Artwork requests require public HTTP(S) addresses on ports 80/443, with redirects and resolved addresses checked. Private-network images can be uploaded instead. The existing configurable Plex connection remains a separate, intentional LAN integration.

## Performance and limits

The frontend splits secondary screens into lazy chunks, defers off-screen poster rendering, debounces search, and limits episode dropdown choices to the current season until expanded. The initial JavaScript entry fell from **393.63 to 236.41 kB** (about 40%); gzip fell from **112.90 to 73.40 kB**. This measures initial-load code, not total code across every lazy screen. Backend improvements avoid repeated season requests, bound media-probe caching, and cap concurrent builds. No NAS throughput benchmark was performed.

Remaining limits: the project still permits legacy untyped dictionaries and broad service-level exception handling; status tracks NFO coverage/provenance rather than detecting every provider metadata change; job history is in memory; and very large libraries still load all matching items into client memory. POSIX rename safety requires hard-link support and aborts safely when unavailable. Graceful shutdown waits for outstanding OS filesystem calls. Live provider credentials, a real Plex server, real NAS behavior, and container execution were not tested.

## Significant files

- Backend: `app/routes/api.py`, new settings router; `db.py`, `config.py`, `main.py`; builder, watcher, scheduler, scanner, parser, cleaner, orphans, renamer, sidecar, NFO, media-info and provider services; five new service modules.
- Frontend: `App.tsx`, shared shell/auth/dialog components, design tokens, API/auth/library helpers; Library, Detail, Settings, Episodes, Overrides, Artwork, Activity, Logs and Automation views; extracted feature components and tests.
- Verification/deployment: five new backend regression files, Vitest setup, Playwright flows/configuration, `backend/tests/qa_server.py`, dependency locks, Dockerfile/ignore rules, CI, README and in-app Help.

## Repository and installation cleanup

The root README is now a 91-line installation guide with one canonical Compose example. Advanced configuration, workflows, and naming details live in `docs/reference.md`; source builds and tests live in `docs/development.md`. Release history moved intact to `docs/CHANGELOG.md`. The repository no longer tracks deployment Compose files or `.env.example`; existing local copies were preserved and ignored. Migration guidance preserves actual config/media paths, especially relative bind mounts. The isolated QA helper moved into `backend/tests/`. CI extracts the README example and checks that Compose accepts a token and rejects an unset token. Runtime options, image tags, ports, and application workflows remain supported.

## Commands and final results

Commands below use an activated Python environment; frontend commands run in `frontend/`, backend checks in `backend/`.

| Command/check | Result |
|---|---|
| `npm ci` | Passed; lockfile installation reproducible |
| `npm run typecheck` | Passed |
| `npm run lint` | Passed, zero warnings |
| `npm test` | 28 passed |
| `npm run build` | Passed |
| `npm run preview -- --host 127.0.0.1 --port 5173 --strictPort` | Served production assets with the fixture backend |
| `npm run test:e2e` | 6 passed across desktop/mobile; includes actual fixture writes |
| `python -m pytest -q` | 223 passed, 6 POSIX permission cases skipped on Windows; two upstream TestClient deprecation warnings |
| `python -m ruff check app tests` | Passed |
| `python -m mypy app` | Passed, 31 modules; existing untyped-body notes remain |
| `python -m pip check` | Passed |
| `python -m pip_audit --cache-dir .qa/pip-audit-cache --progress-spinner off` | No known vulnerabilities (repository root) |
| `npm audit --audit-level=low` | No known vulnerabilities |
| `python backend/tests/qa_server.py` | Startup and HTTP workflows passed in isolation |
| Repeated application lifespan / import-side-effect tests | Passed within pytest |
| Compose and CI YAML parsing, Dockerfile inspection | Passed static checks; image build/run unavailable without Docker |
| `git diff --check` | Passed |

The final visual review used the dashboard ergonomics preset, adapted for a dense media utility: **4.0/5 overall**. These are review judgments from the tested fixture screens, not an accessibility certification or a comparable before/after score.

| Area | Score | Evidence |
|---|---:|---|
| Navigation and context | 4/5 | Library/item context retained; compact global navigation |
| Information density | 4/5 | Grid/list choice and grouped detail/settings controls |
| Interaction design | 4/5 | Explicit previews, draft guards, loading/error recovery |
| Practical accessibility | 4/5 | Labels, keyboard focus, modal trapping and Escape verified |
| Responsive layout | 4/5 | Desktop and 390px workflows; no document overflow |
| Performance perception | 4/5 | Lazy screens/posters and loading feedback; large NAS workloads unmeasured |

No real media or real configuration was changed.
