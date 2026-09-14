# Configuration and usage reference

[Installation](../README.md) · [Development](development.md) · [Release history](CHANGELOG.md)

## Library snapshots

Open **Settings → Libraries**, select one library, and choose **Create snapshot**
before cleaning its metadata. Wait for the background job to complete. Each ZIP
is retained under `/config/library-snapshots/` (or your `CONFIG_DIR`) and can be
downloaded from the same tab. Snapshots are never automatically rotated or
deleted; keep `/config` persistent and allow enough free space for each backup.
Snapshot storage must be outside `MEDIA_ROOT`.

The archive preserves paths relative to the selected library root and includes
all regular non-media files, including hidden sidecars, foreign NFOs, artwork,
subtitles, and other companion files. Known video, audio, and disc-image
extensions are excluded, case-insensitively. File symlinks whose targets stay
inside the selected library are copied as regular files at the link's original
path. Links to media files are excluded. Already-broken file links whose targets
stay inside the library are skipped: their missing content cannot be backed up
or restored. The saved snapshot shows the skipped-link count, which is retained
in the ZIP comment; the job's **Snapshot details** and application log show the
affected paths. Directory links, cyclic links, links outside the library, and
special files cause the snapshot to fail. Unreadable files, files that disappear
during copying, or changing files also fail the snapshot; existing ZIPs remain safe.
These archives cover files in the library, not the application's database,
settings, or custom uploads under `/config`.

Snapshots automatically pause watcher dispatch and scheduled jobs across all
libraries and wait for active automation and queued/running builds to finish
before copying. Incoming watcher events are retained for processing afterward.
The previous runtime states resume after the last active snapshot completes,
fails, or is cancelled; saved enable settings are unchanged.

Pause imports, manual builds, other file changes, and cleanup while creating a snapshot.
This is a file copy, not an atomic filesystem snapshot. Do not begin cleanup
until a completed snapshot appears in the list.

To restore, pause the watcher, schedules, builds, and all other library writes,
download the ZIP, and extract its contents into the **original library root**,
replacing the matching metadata
files. The archive does not rename or restore media files, so keep the original
media filenames and folder layout. Extraction does not remove newer files;
use the existing cleanup preview if you need to remove generated metadata first.
Rescan the library afterward to recover sidecar state and update file status,
then resume automation.

## Container configuration

Use the Compose example in the [README](../README.md#installation). Both
`linux/amd64` and `linux/arm64` images are published. `latest` follows tagged
releases; use a version tag to pin a release, or `edge` for builds from `main`.
Available tags are listed on [GitHub Packages](https://github.com/Cooper8386/plex-nfo-builder/pkgs/container/plex-nfo-builder).
The container listens on port `8000`; change the left side of `8765:8000` to
choose a different host port.

| Mount | Purpose |
| --- | --- |
| `/config` | Persistent SQLite database, `settings.json`, library configuration, custom uploads, and logs. Back this directory up. |
| `/media` | Media root. NFOs and artwork are written beside videos. Use a read-write mount. |

Use absolute host paths in Compose. Keep the container's paths stable when
upgrading: bindings and settings contain those paths. The host must permit
writes to both mounts; on a NAS, check the share's ownership and permissions
if writes fail. The image does not implement LinuxServer's `PUID`/`PGID`
variables. Do not add them expecting a permission change.

### Existing checkout-based installations

The repository no longer distributes a Compose file or `.env.example`.
Before updating an older checkout, copy your deployment's Compose file and
`.env` to a directory outside the checkout. Keep using that configuration,
or adopt the README example while preserving your existing token, options,
and actual config/media mounts. Existing deployments require no data migration.

A relative mount such as `./config:/config` resolves relative to the Compose
file's directory. Resolve it to its existing absolute host path **before
moving the file**; otherwise the container will start with a different,
empty configuration directory. Back up `/config` before upgrading. Changing
the Compose project directory must not change the data mounted in the container.

### Environment variables

Only `API_TOKEN` is required to run the app. Configure a TVDB or TMDB key in
Settings to fetch metadata. To manage credentials through your deployment
instead, add the corresponding variables below to the Compose
`environment` block; a value present only in `.env` is not automatically
passed into the container. Saved provider credentials take precedence over
environment credentials.

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_TOKEN` | Required | Token protecting the API, including file mutations. |
| `TVDB_API_KEY` | Unset | TVDB key; alternatively set it in Settings → Providers. |
| `TVDB_PIN` | Unset | Optional TVDB subscriber PIN. |
| `TMDB_API_KEY` | Unset | TMDB key; alternatively set it in Settings → Providers. |
| `OMDB_API_KEY` | Unset | Optional OMDb key for IMDb, Rotten Tomatoes critics, and Metacritic ratings; alternatively set it in Settings → Providers. |
| `FANART_API_KEY` | Unset | Optional fanart.tv artwork key. |
| `MEDIA_ROOT` | `/media` | Root containing library directories; normally leave the default and change the host mount. |
| `CONFIG_DIR` | `/config` | Persistent state directory; normally leave the default and change the host mount. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `TZ` | `America/Chicago` in the README example | Container time zone. |
| `CORS_ALLOW_ORIGINS` | Empty | Comma-separated allowed browser origins; empty disables cross-origin access. |
| `TRUSTED_HOSTS` | Empty | Comma-separated Host header allowlist; empty accepts any host. |
| `WATCHER_ENABLED` | `true` | Watcher boot default; Settings can override it. |
| `WATCHER_DEBOUNCE_SECONDS` | `30` | Watcher boot debounce, clamped to 1–3600 seconds; Settings can override it. |
| `WATCHER_MAX_INFLIGHT` | `2` | Concurrent watcher pipelines; use a positive integer. Builds also share a global concurrency limit. |
| `WATCHER_KILL_SWITCH` | Unset | `1` or `true` forces the watcher off regardless of Settings. |

### Security and access

The application can write and delete companion files. Without
`API_TOKEN`, API requests return `503` and the UI explains that the server
needs a token. The README's Compose configuration also refuses to start
without it. Generate a token with `openssl rand -hex 32`, then set
`API_TOKEN=<generated value>` in your deployment's `.env` file.

The UI asks for the token and remembers it in that browser's local storage
when available. **Sign out** forgets it. API clients send `X-API-Token` or
`Authorization: Bearer <token>`. Image and download URLs may use
`api_token`, but only for `GET` and `HEAD`; query tokens cannot authorize
mutations. Access logs redact query tokens. Interactive API documentation
at `/docs`, `/redoc`, and `/openapi.json` also requires authentication.

The bundled UI uses the same origin as the API, so CORS needs no setup. Use
an explicit `CORS_ALLOW_ORIGINS` allowlist only for a separate frontend;
never use a wildcard. Set `TRUSTED_HOSTS` to your actual hostnames/IPs for
additional DNS-rebinding protection. For remote access, use a reverse proxy
with HTTPS and its own authentication; keep the raw app port off untrusted
networks.

Settings are saved by atomic replacement. If `settings.json` is unreadable
or invalid, settings-dependent requests return `503` and preserve the file.
Restore a valid copy before saving changes. Provider secrets are write-only
in the settings API; configured indicators also account for environment keys.

### Settings

| Category | Options |
| --- | --- |
| Metadata | Primary TVDB/TMDB source, preferred language (`eng`), fallback languages (`eng`), cache TTL (168 hours), auto-match threshold (85), foreign NFO overwrite (off), orphan sweep after builds (on). |
| Providers | TVDB key/PIN, TMDB key and supplementary artwork (on), fanart.tv key and artwork (on), optional OMDb ratings key. |
| Artwork | Preferred artwork source (`auto`), provider language allowlists (empty means all), language-less artwork (allowed). |
| Plex | Server URL/token, connection test, automatic refresh (off), refresh delay (5 seconds), Builder-to-Plex path mappings. |
| Schedules / Watcher | Recurring scan/match/build schedules and filesystem watcher controls. The watcher starts enabled unless configured otherwise. |
| Security / About | Access and file-safety guidance, running version, project links. |

Language preference applies to fetched titles and descriptions. Fallback
languages are tried when a translation is unavailable. Plex path mappings
translate the Builder's `/media` paths
to paths visible to Plex, such as `/data`; they do not move files.

## Libraries and workflow

The app discovers directories beneath `MEDIA_ROOT` at startup; library
names need not be `tv`, `movies`, or `anime`. A typical arrangement is:

```text
/media/
  TV/
    Show (2024) {tvdb-12345}/
      Season 01/
        Show - S01E01.mkv
  Movies/
    Film (2024) {tmdb-12345}/
      Film (2024).mkv
```

Use the sidebar's folder picker to add a library. Disabling or removing a
library from the sidebar does not delete its media. Choose a library,
scan it, then open a title to match it, adjust metadata/artwork, and build.
Use **Help** in the app for the button reference and daily workflows.

Grid and list views share title search, **All titles / Needs work /
Complete** filters, and sorting by title, dates, or on-disk season count.
Filter and sort choices persist per library. Sort titles use a manual
`sorttitle` override, then the title with a leading `The`, `A`, or `An`
stripped. NFO builds use the same rule. Date Added records discovery (older records are backfilled
from folder mtime); Date Updated includes season-folder mtimes. Returning
from a detail page restores the previous scroll position. A running version
indicator, Settings → About, and `GET /api/version` identify the installed build.

A matched title links to its TVDB/TMDB page. Metadata overrides for series,
seasons, and episodes include title, sort title, original title, tagline,
and plot; empty values fall back to provider metadata. **Auto-match only**
saves the primary binding without generating NFOs or downloading artwork;
manual **Match title** does the same. Source locks protect manual matches.

Opening a matched title's Overview automatically checks for a **Secondary
source** using direct provider links and exact shared IMDb IDs. An existing
secondary link is preserved. Discovery may need both provider keys; if no
exact link exists, retry with **Discover source**, or search/paste an ID.
A cleared link can be discovered again when Overview reopens. The saved ID
supports additional artwork, fanart.tv, and NFO unique IDs on the next build;
the primary provider still supplies titles, descriptions, and cast.

Each bound folder's `.plex-nfo-builder.json` sidecar carries its binding,
overrides, artwork selections, and episode mapping. It can restore those
records after a database loss; it is not a replacement for backing up
`/config`, which also contains settings, uploads, and operational state.

### Maintenance and status

The item action menu's **Delete NFOs & artwork…** previews generated
companions before confirmation. It includes episode thumbnails and orphan
companions left behind by older video filenames; season folders and videos stay.
Library maintenance offers previews for orphan cleanup, NFO/artwork wipe,
and sidecar deletion. Review the listed files and confirm the intended
scope before applying a destructive operation.

An orphan is an episode/movie `<stem>.nfo` or `<stem>-thumb.*` without its
video, often left after Sonarr/Radarr upgrades a release. These can cause
duplicate Plex entries. The default post-build sweep removes these orphan
companions; turn it off in Settings if needed. Detail warnings and library
maintenance also provide explicit orphan previews. Canonical show, season,
and movie metadata are excluded from orphan classification.

**Preview missing folders** and **Preview empty folders** find stale tracked
items. Confirmation removes database state only, and candidates are checked
again before being forgotten. Files stay on disk. Deleting sidecars removes
that recovery copy and clears its manual artwork picks from the database;
bindings and metadata overrides remain active until separately removed. Later
scans can restore database-only removals from sidecars that still exist.

Click the status pill on a detail page for live coverage by season, missing
or foreign NFO filenames, root-level videos, and reasons a title is partial.
**Force rebuild** refreshes provider metadata while preserving foreign NFOs
unless **Overwrite foreign NFOs** is explicitly enabled.

## How matching works

1. **Folder ID** — `{tvdb-…}` and `{tmdb-…}` tags identify a provider record directly; the app checks the record against the media kind.
2. **Filename ID** — for movies, the `{tmdb-…}` in your filenames is recorded as a `<uniqueid type="tmdb">` even if we use TVDB for the rest.
3. **Auto search** — without a direct ID or saved binding, the configured provider searches by title and year. The score threshold is configurable in Settings (default 85). Global and per-library source choices are supported; a pinned item binding keeps its provider identity if that provider is unavailable.
4. **Manual match** — open an unmatched title's match panel, or choose **Change match** on a bound title, search, and select **Match title**. The binding persists in SQLite, so re-runs skip the search. Source locks prevent automatic matching from replacing a deliberate binding.
5. **Per-episode mapping** — local episodes are matched to TVDB/TMDB episodes by `(season, episode)`. Filename styles supported:
   - Sonarr/Radarr: `Series (Year) - S01E03 - Title.mkv`
   - Daily/talk shows: `Show - 2024-01-15.mkv`
   - Anime/fansub: `[Group] Title - 03 [1080p].mkv` (treated as S01E03; pick a different season per file via the inline picker if your fansub bundles multiple seasons)

   The **Episodes** tab on a series lists every local file as its own row and lets you set a per-file season/episode/external-id override. Overrides survive rebuilds.

## Ratings

TMDB builds include the provider's user score and vote count. For additional
ratings, add an [OMDb API key](https://www.omdbapi.com/apikey.aspx) in
**Settings → Providers**, then rebuild the media. Available IMDb, Rotten
Tomatoes critics, and Metacritic scores are written to show, movie, and
episode NFOs, with source names and score scales preserved.

Lookups use provider-linked IMDb IDs. Episodes without an IMDb ID use the
series IMDb ID and the matched episode's season/episode numbers; the response
must identify that episode. Missing ratings stay absent, and a show rating
is never copied onto an episode. OMDb coverage varies, especially for TV
critic scores; audience scores are not supplied by this integration. API
failures leave the rest of the build intact. Normal builds reuse cached
ratings; **Force rebuild** refreshes them. Which rating badges appear in Plex
depends on its metadata agent and support for the NFO fields.

## Episode mapping

Open a series → **Episodes** tab. Each local file gets its own row showing the
parsed season and episode, the matched provider title, and inline pickers for
unparsed files. Per-file mapping overrides are stored in SQLite and mirrored to
the sidecar.

## NFO provenance

Output follows [Plex's NFO naming conventions](https://support.plex.tv/articles/using-nfo-metadata-files-with-plex/):
`tvshow.nfo` for a series, `season.nfo` for season metadata, and `<video-stem>.nfo`
for episodes and movies. Episode thumbnails use `<video-stem>-thumb.jpg`.

NFO writes use atomic replacement (temp file + `os.replace`) and preserve
foreign NFOs unless explicitly allowed in Settings. Empty `<uniqueid>` tags
are never emitted. Atomic writes and valid IDs prevent
Plex from momentarily seeing a torn or ambiguous NFO and spinning up
a duplicate library entry for the show — see the Help tab in-app
for the full explanation and, if you already have a duplicate, the
steps to merge it into the correct show.

Every NFO file this app writes starts with:

```xml
<!-- plex-nfo-builder version=0.1.0 generated_at=1714719600 tvdb_id=371980 content_hash=sha256:… -->
```

The scanner reads the first ~2 KB of each NFO to identify its provenance, then
classifies coverage against the media files on disk. It does not currently
compare the stored content hash with the file contents:

| Status     | Meaning                                                                            |
| ---------- | ---------------------------------------------------------------------------------- |
| `none`     | No NFO files at all.                                                               |
| `partial`  | Some required NFOs are missing (for example, episode NFOs or `tvshow.nfo`).                                   |
| `complete` | All required NFOs carry our provenance comment; series require `tvshow.nfo` plus an NFO per video.          |
| `foreign`  | NFOs exist but were written by something else (Sonarr/Plex Dance/etc.). Preserved. |
| `mixed`    | A mix of provenance and foreign NFOs.                                              |
| `stale`    | Legacy status accepted by the UI/API; current scans do not calculate hash-based staleness. |

## Artwork

Artwork is written directly to the item folder using Plex-standard filenames — no hidden `.artwork/` subfolder, no symlinks. Automatic selection follows the configured source and language preferences, with fallback artwork when needed. Use the **Artwork** tab on a show or movie to override a slot — including per-season posters — with a provider image or custom upload. Standard slots remain available when a provider has no candidates. Selections persist in SQLite and are re-applied on builds.

```
<series>/poster.jpg
<series>/background.jpg
<series>/banner.jpg
<series>/clearlogo.png
<series>/Season01-poster.jpg          # season ≥ 1
<series>/season-specials-poster.jpg   # season 0 / Specials (Plex-specific name)
<series>/Season 01/<file>-thumb.jpg
<movie>/poster.jpg
<movie>/background.jpg
```

The **Overrides** tab includes a thumbnail picker for each episode. TMDB can
offer multiple stills; TVDB generally provides one. Select a still for the
next build, or choose **Auto** to clear the override. Selections use the
external episode ID and are mirrored into the sidecar, so restoring the
database does not lose them.

NFO artwork references use provider URLs in `<thumb>` / `<fanart><thumb>` tags when available. Plex prefers the local file (when the *Local Media Assets* agent is enabled), but can always fall back to the URL if a local file is missing or unreadable across your mount.

**Cast, crew, and portraits.** Show, movie, and episode NFOs include cast
and guest stars with actor photo URLs, plus separate director/writer credits.
Missing photos are checked against provider people records and galleries.
When both provider keys are available, exact IMDb person links can supply a
photo from the other provider. Lookups never choose a person by name alone.
TMDB portrait URLs use `w500`; available photos are also downloaded to
`{item_folder}/.actors/{Actor Name}.jpg`. Recurring people share downloads
within a series build. Provider coverage and lookup limits still apply.
Normal builds reuse the provider cache. Force rebuild refreshes missing
portrait lookups for TMDB builds; TVDB builds retain the portrait cache to
reduce rate limiting.

Plex's NFO agent uses the `<thumb>` URL inside each `<actor>` entry.
The `.actors/` files support compatible local-image readers; they do not
guarantee that a Plex online provider uses or preserves the same photo.
Crew photo display depends on the Plex provider. Refresh an item's metadata
in Plex after rebuilding. Plex Discover filmography links require cast from
an online provider rather than NFO cast. See the
[Plex NFO guide](https://support.plex.tv/articles/using-nfo-metadata-files-with-plex/)
for agent setup and supported tags.

For TMDB-supplied artwork, the auto-resolver reads each title's TMDB *original language* and includes that flag in its image request alongside `null,en`, so anime, K-dramas, and other foreign-language titles actually surface their fan-uploaded posters instead of coming up empty. The manual artwork picker requests **all** languages from TMDB so you see every uploaded image when you're hand-picking.

**Per-provider artwork language filter** (Settings → Artwork). TVDB and TMDB both surface every language a contributor has ever uploaded, so foreign-language posters and logos with the wrong script can leak in even on titles whose primary language is set correctly. Pick the languages you want to accept from each provider — TVDB uses 3-letter ISO 639-2 codes (`eng`, `fra`, `jpn`), TMDB uses 2-letter ISO 639-1 codes (`en`, `fr`, `ja`) — and toggle whether to include language-less artwork (key art, logos, fan uploads with no language tag) per provider. The list of available languages is queried live from each provider, and the picker is searchable. If your filter would leave a given title with no artwork at all, the unfiltered list is used as a fallback so a niche import never ends up with a blank poster. Empty whitelist means *no filter* — leave it that way for legacy behaviour.

**Custom artwork safety.** Upload raster images or register a public HTTP(S)
image URL on port 80 or 443. Remote downloads reject private, loopback,
link-local, and other non-public addresses; redirects are checked again, and
the validated address is pinned for the connection. Embedded URL credentials
are rejected. For images hosted only on a LAN, upload the image instead.
Transfers are limited to 50 MB, checked for supported raster content, and
written atomically. Failed or cancelled transfers preserve the previous image;
registered uploads are copied locally when building.

Older v0.2 installations used a hidden `.artwork/` directory and symlinks. If an affected item still shows blank or outdated posters, back up that directory and inspect its symlinks before removing the obsolete artwork layout, then rebuild the item. Current builds write canonical files directly.

## Logs

- `/config/logs/app.log` — rolling app log (10 MB × 10 files, ZIP compression).
- `/config/logs/jobs/<id>.log` — per-build log; available through authenticated `GET /api/jobs/<id>/log`. **Activity** shows build status and messages.
- The Logs view live-tails the last 400 lines of `app.log`.

Builds from the API, watcher, and schedules share a limit of two concurrent
jobs. Duplicate requests for the same active folder reuse its job. Additional
builds appear as queued in **Activity**. Shutdown cancels queued/background
work, drains active filesystem calls, and closes provider clients and SQLite;
a stalled network filesystem can delay graceful shutdown.

`GET /api/items` accepts `limit` (1–5000, default 5000) and `offset` (default 0)
and returns `items`, `total`, `limit`, and `offset`. Internal library operations
process all tracked items. `POST /api/libraries/<name>/scan` returns after the
scan completes, including its `scanned` count.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Compose reports that `API_TOKEN` is missing | Create or edit `.env` beside your own Compose file, add the token, and rerun Compose from that directory. |
| UI reports no server token or rejects sign-in | Confirm the container received `API_TOKEN`; sign in with that same value. Recreate the container after changing environment variables. |
| Empty libraries after an upgrade | Check the resolved host mounts, especially a moved relative `./config` path. Use the original config directory and stable container paths. |
| Matching or artwork fails | Check the selected provider's key, source identity, language settings, and Activity/Logs. A provider outage does not justify changing a correct binding. |
| Writes fail | Check NAS mount permissions and free space. |
| A title is partial or appears twice in Plex | Open the status details, inspect missing/foreign NFOs and orphan previews, then use the in-app Help guidance. |
| Settings return `503` | Restore a valid `/config/settings.json`; the unreadable file is deliberately preserved. |
| Remote artwork URL is rejected | Use a public supported raster-image URL on port 80/443, or upload a LAN-hosted image directly. |
| GHCR denies a private image pull | Authenticate with `docker login ghcr.io`; a classic personal token needs `read:packages`. Public images need no registry login. |

For a private registry login without putting a token in command arguments:

```bash
printf '%s' "$GH_PAT" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```
