"""Helpers for migration integration tests."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def alembic_cmd(
    *args: str,
    url: str,
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SHINEHE_TEST_ALEMBIC_URL"] = url
    # Ensure gate doesn't interfere with nested pytest if any
    env.pop("SHINEHE_ENFORCE_MIGRATION_GATE", None)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        combined = (result.stdout or "") + (result.stderr or "")
        raise AssertionError(
            f"alembic {' '.join(args)} failed (code={result.returncode}):\n{combined[-3000:]}"
        )
    return result


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def read_revision(db_path: Path) -> str | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "alembic_version" not in tables:
            return None
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()


def table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
    finally:
        conn.close()


def head_revision() -> str:
    from src.storage.migration_status import get_alembic_head

    return get_alembic_head(ROOT)
