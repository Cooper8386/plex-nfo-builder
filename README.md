# Plex NFO Builder

A self-hosted web app that pulls metadata from [TheTVDB v4 API](https://thetvdb.github.io/v4-api/) for your shows / movies and writes Plex-compatible NFO files plus full local artwork sets.

Built around your Sonarr/Radarr-style filenames (e.g. `Severance (2022) - S02E08 - Sweet Vitriol [WEBDL-1080p][EAC3 Atmos 5.1][h264]-FLUX.mkv`) and folder layout (e.g. `Severance (2022) {tvdb-371980}/Season 02/...`). Reads the `{tvdb-…}` / `{tmdb-…}` tag in folder/file names so it doesn't have to guess. Anime/fansub-style filenames (`[Group] Title - 01 [1080p][WEB-DL].mkv`) are also parsed and can be renamed in-place to match the Sonarr scheme.

## Highlights

- **Auto library detection** — anything under `MEDIA_ROOT` is found at startup and re-scanned on demand. No hardcoded `tv` / `movies` / `anime`.
- **Three metadata sources**: TheTVDB v4 (primary), TMDB (alternate), fanart.tv (artwork supplement). Per-show source override with a lock so auto-match never silently swaps it.
- **Visual folder picker** in the UI (sidebar) — no need to type paths. Disable / remove libraries from the kebab menu without touching disk.
- **Auto + manual matching** with persistent bindings stored in SQLite. Folder ID tags (`{tvdb-…}` / `{tmdb-…}`) are honored as the primary signal.
- **Plex NFO output** per the [Plex docs](https://support.plex.tv/articles/using-nfo-metadata-files-with-plex/): `tvshow.nfo`, `season.nfo`, per-episode `<file>.nfo`, per-movie `<file>.nfo`, `<file>-thumb.jpg` for episode thumbnails, canonical `poster.jpg` / `background.jpg` / `banner.jpg` for series and movies.
- **Manual NFO field overrides** at the series, season, or per-episode level (title, sorttitle, originaltitle, tagline, plot). Empty falls back to source.
- **Sidecar (`.plex-nfo-builder.json`)** in every bound folder carries the binding, overrides, artwork selections, and episode mapping. A full database wipe is recoverable straight from the media library.
- **Wipe NFOs & artwork** button per show — deletes every generated file in one click while leaving season folders and media files alone.
- **Rename to scheme** — preview-then-apply renamer that rewrites loose anime/fansub filenames into a Sonarr-style template (`{title} ({year}) - S{season:02}E{episode:02} - {episode_title}{ext}`). Dry-run preview shows every rename, flags conflicts, and lets you uncheck individual files. Customizable templates live in Settings.
- **Provenance**: every NFO this app writes carries a header comment with version, source id, content hash, and timestamp. The scanner uses that to label items as `complete / partial / foreign / mixed / stale` and avoids clobbering foreign NFOs unless you opt in.
- **Library views**: poster grid (default), dense list, and per-item detail. Filter by status, free-text title search, and a one-click toggle to **hide already-organized** items.
- **In-app help** (Help in the top bar) — quick orientation, button reference, status badge legend.
- **Language preference** with fallback chain (e.g. `eng` → `jpn` → first available).
- **Logging** with rotating `app.log`, plus per-job logs under `/config/logs/jobs/<job_id>.log`.
- **Multi-arch container** (`linux/amd64`, `linux/arm64`) published to GHCR on every release.

## Documentation

- **In-app**: open the running container and click `Help` in the top bar — it covers the day-to-day workflow, every button, and the status badges.
- **`CHANGELOG.md`** for what changed in each release.
- **The rest of this README** for installation, environment, folder layout, and matching internals.

## Requirements

- Docker / docker-compose
- A TVDB API key — sign up at [thetvdb.com](https://www.thetvdb.com/dashboard/account/apikey). Subscriber API keys also accept a PIN.

## Quick start (prebuilt image)

Multi-arch images (`linux/amd64`, `linux/arm64`) are published to GHCR on every tagged release.

```bash
git clone https://github.com/Cooper8386/plex-nfo-builder.git
cd plex-nfo-builder
cp .env.example .env  # then edit TVDB_API_KEY (and TMDB / fanart keys if you use them)
# point the volume in docker-compose.yml at your media root if not /mnt/user/data/media
docker compose pull
docker compose up -d
```

To upgrade later, just:

```bash
docker compose pull && docker compose up -d
```

If you'd rather pin to a specific release, change the image tag in
`docker-compose.yml` from `:latest` to e.g. `:0.5.5`. All published tags are
listed on the [GitHub Packages page](https://github.com/Cooper8386/plex-nfo-builder/pkgs/container/plex-nfo-builder).

For a private repository you'll need to log in to GHCR once on the host before
pulling:

```bash
echo "$GH_PAT" | docker login ghcr.io -u Cooper8386 --password-stdin
```

(Use a classic personal access token with `read:packages` scope.)

### Build from source instead

```bash
git clone https://github.com/Cooper8386/plex-nfo-builder.git
cd plex-nfo-builder
cp .env.example .env  # edit TVDB_API_KEY
# in docker-compose.yml: comment out `image:` and uncomment `build: .`
docker compose up -d --build
```

Open <http://localhost:8765>. The first time, go to **Settings**, set the preferred language and (if not already set via env) paste your TVDB API key.

## Container layout

```
/media     ← bind-mount of your media root (read-write; NFOs/artwork are written next to your video files)
/config    ← bind-mount that holds the SQLite DB, logs, and settings.json
```

Environment variables:

| Variable          | Default          | Notes                                                     |
| ----------------- | ---------------- | --------------------------------------------------------- |
| `TVDB_API_KEY`    | _required_       | Or paste it in the Settings UI.                           |
| `TVDB_PIN`        |                  | Optional subscriber PIN.                                  |
| `MEDIA_ROOT`      | `/media`         | Anything under this becomes a library (one per top dir).  |
| `CONFIG_DIR`      | `/config`        | DB / logs / settings live here.                           |
| `LOG_LEVEL`       | `INFO`           | `DEBUG`, `INFO`, `WARNING`, `ERROR`.                      |
| `TZ`              | `America/Chicago`| Container time zone.                                      |

## How matching works

1. **Folder ID** — if the folder name ends with `{tvdb-…}` (your default), we pull `series_extended` / `movie_extended` directly. No search, instant.
2. **Filename ID** — for movies, the `{tmdb-…}` in your filenames is recorded as a `<uniqueid type="tmdb">` even if we use TVDB for the rest.
3. **Auto search** — if no ID tag exists, we fuzzy-search TVDB by title+year. Threshold is configurable in Settings (default 85).
4. **Manual match** — open a series → "Manual match" panel → search → "Bind". The binding is stored permanently in SQLite, so re-runs skip the search.
5. **Per-episode mapping** — local episodes are matched to TVDB/TMDB episodes by `(season, episode)`. Filename styles supported:
   - Sonarr/Radarr: `Series (Year) - S01E03 - Title.mkv`
   - Daily/talk shows: `Show - 2024-01-15.mkv`
   - Anime/fansub: `[Group] Title - 03 [1080p].mkv` (treated as S01E03; pick a different season per file via the inline picker if your fansub bundles multiple seasons)

   The **Episodes** tab on a series lists every local file as its own row, lets you set a per-file season/episode/external-id override, and lets you rename the files to your template once they're mapped correctly. Overrides survive renames and rebuilds.

## Episode mapping & renaming

Open a series → **Episodes** tab. Each local file gets its own row showing the parsed `S/E`, the matched provider title, and (for unparsed files) inline season/episode pickers. Override any file's mapping with the per-row dropdown — overrides are stored per-file in SQLite and in the sidecar.

Click **Rename to scheme** on the Episodes tab to open the rename modal:

- Live preview of every `from → to` change.
- Conflict badges (`exists`, `duplicate`) so you don't clobber existing files.
- Per-row checkboxes — auto-checked except for unchanged or conflicting rows.
- Atomic per-file rename via `os.replace`. Per-file overrides are migrated alongside the file so your bindings stay intact.

Default templates (editable in Settings → Renaming):

- Series: `{title} ({year}) - S{season:02}E{episode:02} - {episode_title}{ext}`
- Movie: `{title} ({year}){ext}`

Available tokens: `{title}`, `{year}`, `{season}`, `{season:02}`, `{episode}`, `{episode:02}`, `{episode_title}`, `{quality}` (best-effort `1080p` / `WEB-DL` / etc. from the original stem), `{ext}`.

## NFO provenance

Every NFO file this app writes starts with:

```xml
<!-- plex-nfo-builder version=0.1.0 generated_at=1714719600 tvdb_id=371980 content_hash=sha256:… -->
```

The scanner reads only the first ~2 KB of each NFO to classify items quickly:

| Status     | Meaning                                                                            |
| ---------- | ---------------------------------------------------------------------------------- |
| `none`     | No NFO files at all.                                                               |
| `partial`  | `tvshow.nfo` plus some-but-not-all episode NFOs.                                   |
| `complete` | `tvshow.nfo` and an NFO per episode, all carrying our provenance comment.          |
| `foreign`  | NFOs exist but were written by something else (Sonarr/Plex Dance/etc.). Preserved. |
| `mixed`    | A mix of provenance and foreign NFOs.                                              |
| `stale`    | Provenance hash differs from the on-disk hash (file edited externally).            |

## Artwork

Artwork is written directly to the item folder using Plex-standard filenames — no hidden `.artwork/` subfolder, no symlinks. By default the app chooses the highest-scored TVDB variant in your preferred language (falling back to other languages when necessary). Use the **Artwork** tab on a show or movie to override any slot — including per-season posters — with a different TVDB variant. Selections persist in SQLite and are re-applied on every build.

```
<series>/poster.jpg
<series>/background.jpg
<series>/banner.jpg
<series>/clearlogo.png
<series>/Season01-poster.jpg
<series>/Season 01/<file>-thumb.jpg
<movie>/poster.jpg
<movie>/background.jpg
```

Every NFO also embeds the TVDB CDN URL in `<thumb>` / `<fanart><thumb>` tags. Plex prefers the local file (when the *Local Media Assets* agent is enabled), but can always fall back to the URL if a local file is missing or unreadable across your mount.

Earlier versions wrote everything through a hidden `.artwork/` folder with symlinks to the canonical names. This broke under some Docker bind-mount configurations and caused blank posters when the active artwork was changed — the NFO and the symlink drifted out of sync. If you're upgrading from v0.2, delete any leftover `.artwork/` folders and run **Force rebuild** on affected items.

## Logs

- `/<config>/logs/app.log` — rolling app log (10 MB × 10 files, gzip).
- `/<config>/logs/jobs/<id>.log` — per-build log; downloadable from Jobs view.
- The Logs view live-tails the last 400 lines of `app.log`.

## Running the backend without Docker (dev)

```bash
cd backend
pip install -r requirements.txt
TVDB_API_KEY=… MEDIA_ROOT=/path/to/media CONFIG_DIR=./_config \
  uvicorn app.main:app --reload

# in another shell
cd frontend
npm install
npm run dev      # http://localhost:5173 (proxies /api -> :8000)
```

## License

MIT
