"""Run the backend with disposable media, settings and cached fictional metadata.

Use only for local QA: python backend/tests/qa_server.py. Token: pnb-local-qa.
All files live in a newly created temporary directory; no real media is read.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "backend"))
    with tempfile.TemporaryDirectory(prefix="pnb-qa-") as directory:
        workspace = Path(directory).resolve()
        media = workspace / "media"
        config = workspace / "config"
        media.mkdir()
        config.mkdir()
        os.environ.update(
            MEDIA_ROOT=str(media),
            CONFIG_DIR=str(config),
            API_TOKEN="pnb-local-qa",
            WATCHER_ENABLED="false",
            WATCHER_KILL_SWITCH="1",
            TRUSTED_HOSTS="127.0.0.1,localhost",
            CORS_ALLOW_ORIGINS="",
            TVDB_API_KEY="",
            TVDB_PIN="",
            TMDB_API_KEY="",
            FANART_API_KEY="",
        )
        from app import db
        from app.config import UserSettings
        from app.services import scanner
        from app.services.sidecar import write_sidecar
        from app.services.tvdb import TVDBClient

        UserSettings(
            fanart_enabled=False, tmdb_artwork_enabled=False, auto_sweep_orphans=False
        ).save(config / "settings.json")
        titles = [
            "Aurora",
            "The Last Signal",
            "Night Transit",
            "Wild Coast",
            "Orbit Nine",
            "Small Hours",
            "Northbound",
            "The Archive",
            "Distant Shores",
            "Daybreak",
            "Paper Cities",
            "Afterlight",
        ]
        first = None
        for index, title in enumerate(titles):
            folder = media / "Series" / f"{title} (2024)"
            season = folder / "Season 01"
            season.mkdir(parents=True)
            for episode in (1, 2, 3):
                (season / f"{title} - S01E{episode:02}.mkv").write_bytes(
                    b"QA fixture, not real media"
                )
                if index % 3 == 0 or (index % 3 == 1 and episode == 1):
                    (season / f"{title} - S01E{episode:02}.nfo").write_text(
                        "<!-- plex-nfo-builder -->\n<episodedetails/>", encoding="utf-8"
                    )
            if index % 3 != 2:
                (folder / "tvshow.nfo").write_text(
                    "<!-- plex-nfo-builder -->\n<tvshow/>", encoding="utf-8"
                )
            if index == 0:
                first = folder
                (season / "Old release.nfo").write_text(
                    "<episodedetails/>", encoding="utf-8"
                )
            if index % 3 != 2:
                external = str(9000 + index)
                db.upsert_binding(
                    str(folder),
                    "series",
                    "tvdb",
                    external,
                    title=title,
                    year=2024,
                    language="eng",
                )
                series = {
                    "id": int(external),
                    "name": title,
                    "firstAired": "2024-01-01",
                    "overview": "A fictional series for isolated interface testing.",
                    "genres": [{"name": "Drama"}],
                    "artworks": [],
                    "seasons": [],
                }
                episodes = [
                    {
                        "id": 10000 + index * 10 + ep,
                        "seasonNumber": 1,
                        "number": ep,
                        "name": name,
                        "aired": f"2024-01-{ep:02}",
                        "overview": "Fictional episode metadata.",
                    }
                    for ep, name in enumerate(
                        ["The arrival", "A quiet signal", "Beyond the ridge"], 1
                    )
                ]
                for path, params, data in [
                    (
                        f"/series/{external}/extended",
                        {"meta": "translations,episodes"},
                        {"data": series},
                    ),
                    (
                        f"/series/{external}/episodes/default/eng",
                        {"page": 0},
                        {"data": {"episodes": episodes}, "links": {}},
                    ),
                    (
                        f"/series/{external}/episodes/default",
                        {"page": 0},
                        {"data": {"episodes": episodes}, "links": {}},
                    ),
                    (
                        "/search",
                        {
                            "query": title,
                            "type": "series",
                            "limit": 20,
                            "language": "eng",
                        },
                        {
                            "data": [
                                {
                                    "tvdb_id": external,
                                    "name": title,
                                    "year": "2024",
                                    "type": "series",
                                }
                            ]
                        },
                    ),
                    (
                        "/search",
                        {"query": title, "type": "series", "limit": 25},
                        {
                            "data": [
                                {
                                    "tvdb_id": external,
                                    "name": title,
                                    "year": "2024",
                                    "type": "series",
                                }
                            ]
                        },
                    ),
                ]:
                    db.cache_set(TVDBClient._cache_key(path, params), data, ttl=86400)
                write_sidecar(folder)
        for title in ("The Long Way Home", "Red Horizon", "Frequencies"):
            folder = media / "Movies" / f"{title} (2025)"
            folder.mkdir(parents=True)
            (folder / f"{title} (2025).mkv").write_bytes(b"QA fixture, not real media")
        scanner.detect_libraries()
        scanner.scan_library("Series")
        scanner.scan_library("Movies")
        output = root / ".qa"
        output.mkdir(exist_ok=True)
        (output / "fixture.json").write_text(
            json.dumps(
                {
                    "media_root": str(media),
                    "series_path": str(first),
                    "token": "pnb-local-qa",
                }
            ),
            encoding="utf-8",
        )
        import uvicorn

        uvicorn.run("app.main:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
