# Plex NFO Builder

A self-hosted app that creates Plex-compatible NFO metadata and local artwork
for TV shows and movies. Works with Sonarr/Radarr folder layouts and provides
previewed file renaming when your library needs tidying.

- TVDB and TMDB metadata, with optional fanart.tv artwork.
- Automatic or manual matching, episode mapping, and metadata/artwork overrides.
- Persistent bindings and recoverable sidecars beside your media.
- Library browsing, scheduled builds, filesystem watching, and cleanup previews.
- Per-library ZIP snapshots of sidecars and metadata in **Settings → Libraries**.

## Installation

Requires Docker with Compose and a TVDB or TMDB API key. Images support
`linux/amd64` and `linux/arm64`; no source checkout is needed.

1. Create a directory for your deployment. Save this as `compose.yaml`,
   replacing both `/path/to/…` entries with **absolute host paths**:

```yaml
services:
  plex-nfo-builder:
    image: ghcr.io/cooper8386/plex-nfo-builder:latest
    container_name: plex-nfo-builder
    environment:
      API_TOKEN: ${API_TOKEN:?Set API_TOKEN in .env}
      TZ: ${TZ:-America/Chicago}
    volumes:
      - /path/to/config:/config
      - /path/to/media:/media
    ports:
      - "8765:8000"
    restart: unless-stopped
```

2. Generate an access token:

```bash
openssl rand -hex 32
```

   Create `.env` beside `compose.yaml` and save the generated value:

```dotenv
API_TOKEN=your-generated-token
```

   Optionally add `TZ=Your/Timezone`. Keep this file private. The container
   requires the token; you will use it to sign in.

3. From that directory, start the app:

```bash
docker compose up -d
```

4. Open [localhost:8765](http://localhost:8765), sign in with your token, and
   add your TVDB or TMDB key in **Settings → Providers**. Select the matching
   primary source and language under **Metadata**, then open a library.
   fanart.tv is optional.

`/config` stores settings, SQLite state, uploads, and logs; keep it persistent
and backed up. `/media` exposes your library read-write: NFOs and artwork are
written beside videos. Builds preserve foreign NFOs by default. Renaming and
cleanup offer previews; review their scope before applying changes. The
filesystem watcher is enabled by default and can be controlled in Settings.

Existing checkout-based installs can keep their current configuration; see
[the migration note](docs/reference.md#existing-checkout-based-installations)
before moving a Compose file or updating the checkout.

## Updates

Run from your deployment directory:

```bash
docker compose pull
docker compose up -d
```

`latest` follows releases. To pin a version, use a
[published image tag](https://github.com/Cooper8386/plex-nfo-builder/pkgs/container/plex-nfo-builder).

## Documentation

- **Help** in the app: everyday workflows, controls, and status meanings.
- [Configuration and usage](docs/reference.md): environment variables, security, naming, artwork, and troubleshooting.
- [Development](docs/development.md): source builds, tests, and isolated browser QA.
- [Changelog](docs/CHANGELOG.md): release history.

[MIT license](LICENSE).
