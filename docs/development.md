# Development

[Installation](../README.md) · [Configuration reference](reference.md)

Use Python 3.12 and Node 22. Commands below use a POSIX shell; on Windows,
activate the virtual environment with `.venv\Scripts\Activate.ps1` and set
environment variables through PowerShell when needed. Install `ffmpeg` if
you need real media-probe results outside Docker.

## Setup and checks

```bash
git clone https://github.com/Cooper8386/plex-nfo-builder.git
cd plex-nfo-builder
python -m venv .venv
. .venv/bin/activate
cd backend
python -m pip install -r requirements.txt -r requirements-dev.txt
ruff check app tests
mypy app
python -m pytest -q

cd ../frontend
npm ci
npm run typecheck
npm run lint
npm test
npm run build
```

[CI](../.github/workflows/ci.yml) runs these backend and frontend checks and
validates the README's Compose example. Backend regression tests use temporary
directories and mocked providers for filesystem safety, matching, NFO output,
renaming, sidecar recovery, authentication, settings, and background jobs.
Frontend tests cover controls and important user behavior. Some legacy Python
code remains untyped; passing mypy does not imply strict typing everywhere.

## Isolated development and browser QA

Use the disposable fixture server for library mutations. It creates fictional
media, settings, cached metadata, and SQLite state in a temporary directory;
overrides real media/config paths; disables the watcher; and clears provider
credentials. Run from the repository root using the Python environment above:

```bash
python backend/tests/qa_server.py
# Backend: http://127.0.0.1:8000
# Local QA token: pnb-local-qa
```

In a second terminal:

```bash
cd frontend
npm run dev -- --host 127.0.0.1
# Open http://127.0.0.1:5173 and sign in with pnb-local-qa.
```

Vite proxies `/api` to port `8000`. With both servers running, use a third
terminal for the desktop and mobile Playwright flows:

```bash
cd frontend
npx playwright install chromium
npm run test:e2e
```

For production-asset QA, run `npm run build`, then replace the Vite dev command
with `npm run preview -- --host 127.0.0.1 --port 5173 --strictPort`.
The fixture server records its disposable paths in `.qa/fixture.json`.
Stop it normally to close SQLite and remove its temporary library. The QA
token is public and suitable only for this local fixture. Never point tests
or destructive QA at a real Plex/Sonarr/Radarr library.

To run against your own **disposable** fixtures instead, start the backend
from `backend/` with explicit paths and a private token:

```bash
API_TOKEN=your-development-token MEDIA_ROOT=/absolute/path/to/test-media \
  CONFIG_DIR=/absolute/path/to/test-config WATCHER_KILL_SWITCH=1 \
  uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Configure provider credentials through Settings if that test needs live data.
Provider outages, real metadata, NAS behavior, and Plex refresh need separate
integration checks; the offline fixture does not simulate those services.

## Build the container from source

From the repository root:

```bash
docker build -t plex-nfo-builder:local .
```

Copy the [README Compose example](../README.md#installation) into your own
deployment directory, replace its image with `plex-nfo-builder:local`, and
use disposable config/media mounts for testing. Start it with
`docker compose up -d`. To rebuild after changes, rerun the build and
`docker compose up -d --force-recreate`.

The Dockerfile builds frontend assets on the build host's architecture and
copies them into the Python runtime. It includes `tini`, CA certificates,
and `ffmpeg`; the runtime listens on port `8000` and uses `/config` and
`/media`. The publish workflow builds `linux/amd64` and `linux/arm64` images.
Do not introduce deployment-specific mounts or credentials into the image.

## Code orientation

| Area | Responsibility |
| --- | --- |
| `backend/app/routes/` | Authenticated API orchestration and settings endpoints. |
| `backend/app/config.py`, `db.py` | Environment/user settings and SQLite persistence. |
| `backend/app/services/` | Metadata providers, scanning, NFO/artwork, matching, renaming, jobs, and automation. |
| `backend/tests/` | Regression tests and disposable QA server. |
| `frontend/src/views/` | Library/detail/settings workflows and feature components. |
| `frontend/src/components/`, `lib/` | Shared controls, navigation, API/auth, and state helpers. |
| `frontend/e2e/` | Browser workflows using the isolated backend. |
| `.github/workflows/` | Checks, container publishing, and releases. |

Keep file operations inside validated media boundaries. Preserve preview,
collision, foreign-NFO, and recovery behavior when changing filesystem code;
use temporary directories for regression tests. The
[engineering audit](engineering-audit.md) records the redesign's findings,
verification, and remaining limitations.
