import os
import subprocess
import sys
from pathlib import Path


def test_isolated_startup_cors_and_shutdown(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    configuration = tmp_path / "config"
    environment = {
        **os.environ,
        "MEDIA_ROOT": str(media), "CONFIG_DIR": str(configuration),
        "API_TOKEN": "test-secret", "WATCHER_ENABLED": "false",
        "CORS_ALLOW_ORIGINS": "http://localhost:5173", "TRUSTED_HOSTS": "testserver",
    }
    code = '''
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import CONFIG_DIR
from app import db
from app.main import app
assert not CONFIG_DIR.exists(), "Import must not initialize configuration"
with TestClient(app) as client:
    response = client.get("/api/version", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    response = client.options("/api/settings", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-api-token",
    })
    assert response.status_code == 200
    assert client.get("/api/version", headers={"Host": "evil.test"}).status_code == 400
    assert client.get("/api/version", headers={"X-API-Token": "test-secret"}).status_code == 200
    assert (CONFIG_DIR / "app.db").exists()
assert db._conn is None, "Shutdown must release SQLite"
with TestClient(app) as client:
    assert client.get("/api/version", headers={"X-API-Token": "test-secret"}).status_code == 200
assert db._conn is None, "Repeated lifespans must release SQLite"
print("startup and shutdown passed")
'''
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        env=environment, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "startup and shutdown passed" in result.stdout
