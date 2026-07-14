"""WP2-T1: inspect_database_bootstrap must be pure / non-mutating."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.migrations._helpers import alembic_cmd, sqlite_url


@pytest.fixture
def enforce_env(monkeypatch):
    monkeypatch.setenv("SHINEHE_ENFORCE_MIGRATION_GATE", "1")
    monkeypatch.delenv("SHINEHE_SKIP_MIGRATION_GATE", raising=False)
    monkeypatch.delenv("SHINEHE_READONLY", raising=False)


class _Cfg:
    def __init__(self, mapping: dict | None = None):
        self._m = mapping or {}

    def get(self, key, default=None):
        return self._m.get(key, default)


def test_inspect_missing_db_does_not_create_file(tmp_path: Path, enforce_env):
    from src.storage.database_bootstrap import inspect_database_bootstrap

    db = tmp_path / "missing.db"
    assert not db.exists()
    plan = inspect_database_bootstrap(db, config=_Cfg())
    assert plan.exists is False
    assert plan.empty is True
    assert plan.write_allowed is True
    assert plan.action in {"init_empty", "open_runtime"}
    assert not db.exists(), "inspect must not create the database file"
    # no sidecar files for this path either
    assert not (tmp_path / "missing.db-wal").exists()
    assert not (tmp_path / "missing.db-shm").exists()


def test_inspect_behind_head_plan_is_block_in_write_mode(tmp_path: Path, enforce_env):
    from src.storage.database_bootstrap import inspect_database_bootstrap

    db = tmp_path / "behind.db"
    alembic_cmd("upgrade", "j002_evidence_stale", url=sqlite_url(db))
    before_mtime = db.stat().st_mtime_ns
    before_size = db.stat().st_size

    plan = inspect_database_bootstrap(db, config=_Cfg({"storage.migration_gate.enabled": True}))
    assert plan.exists is True
    assert plan.empty is False
    assert plan.migration_status.at_head is False
    assert plan.write_allowed is False
    assert plan.action == "block"
    assert plan.readonly is False

    assert db.stat().st_mtime_ns == before_mtime
    assert db.stat().st_size == before_size


def test_inspect_at_head_allows_open_runtime(tmp_path: Path, enforce_env):
    from src.storage.database_bootstrap import inspect_database_bootstrap

    db = tmp_path / "head.db"
    alembic_cmd("upgrade", "head", url=sqlite_url(db))
    plan = inspect_database_bootstrap(db, config=_Cfg())
    assert plan.migration_status.at_head is True
    assert plan.write_allowed is True
    assert plan.action == "open_runtime"
    assert plan.readonly is False


def test_inspect_unstamped_does_not_modify_schema(tmp_path: Path, enforce_env):
    from src.storage.database_bootstrap import inspect_database_bootstrap

    db = tmp_path / "unstamped.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE knowledge_items (id TEXT PRIMARY KEY)")
    conn.commit()
    tables_before = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()
    size_before = db.stat().st_size

    plan = inspect_database_bootstrap(db, config=_Cfg())
    assert plan.migration_status.unstamped is True
    # WP4: default allow_unstamped=false → block write; inspect must not stamp
    assert plan.action == "block"
    assert plan.write_allowed is False
    assert "alembic_version" not in tables_before

    conn = sqlite3.connect(str(db))
    tables_after = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()
    assert tables_after == tables_before
    assert db.stat().st_size == size_before


def test_inspect_readonly_behind_head_open_readonly(tmp_path: Path, enforce_env):
    from src.storage.database_bootstrap import inspect_database_bootstrap

    db = tmp_path / "ro_behind.db"
    alembic_cmd("upgrade", "j002_evidence_stale", url=sqlite_url(db))
    plan = inspect_database_bootstrap(
        db,
        config=_Cfg({"storage.readonly": True, "storage.migration_gate.enabled": True}),
    )
    assert plan.readonly is True
    assert plan.write_allowed is False
    assert plan.action == "open_readonly"
