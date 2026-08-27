from pathlib import Path

import pytest


@pytest.fixture
def touch(tmp_path: Path):
    """Create a file (and parents) under tmp_path; returns its Path."""
    def _touch(rel: str, text: str = "") -> Path:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p
    return _touch
