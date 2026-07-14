"""Alembic baseline smoke — empty DB can upgrade to head (strict)."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_alembic_is_importable():
    import alembic  # noqa: F401


def test_alembic_versions_directory_non_empty():
    versions = [
        p for p in (ROOT / "alembic" / "versions").glob("*.py")
        if p.name != "__init__.py"
    ]
    assert len(versions) >= 1


def test_alembic_upgrade_head_on_empty_db_uses_temp_url():
    """Must migrate the temporary DB from SHINEHE_TEST_ALEMBIC_URL, not user default."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        url = f"sqlite:///{db_path.as_posix()}"
        env = os.environ.copy()
        env["SHINEHE_TEST_ALEMBIC_URL"] = url

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        assert result.returncode == 0, combined[-3000:]

        assert db_path.exists(), "temp DB file must be created by alembic"
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
            assert rows, "alembic_version must be populated"
            # head revision should match one of the version files' revision id loosely
            version = rows[0][0]
            assert isinstance(version, str) and version
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "alembic_version" in tables
        finally:
            conn.close()


def test_alembic_env_reads_test_url_constant():
    env_py = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "SHINEHE_TEST_ALEMBIC_URL" in env_py
