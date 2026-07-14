"""Startup migration gate unit tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.storage.migration_status import get_alembic_head, get_migration_status
from src.storage.startup_gate import (
    MigrationGateError,
    enforce_startup_gate,
)
from tests.migrations._helpers import alembic_cmd, head_revision, sqlite_url


@pytest.fixture
def enforce_env(monkeypatch):
    monkeypatch.setenv("SHINEHE_ENFORCE_MIGRATION_GATE", "1")
    monkeypatch.delenv("SHINEHE_SKIP_MIGRATION_GATE", raising=False)
    monkeypatch.delenv("SHINEHE_READONLY", raising=False)


def test_status_empty_file(tmp_path: Path):
    db = tmp_path / "missing.db"
    st = get_migration_status(db)
    assert st.db_exists is False
    assert st.unstamped is True


def test_at_head_allows_write(tmp_path: Path, enforce_env):
    db = tmp_path / "ok.db"
    alembic_cmd("upgrade", "head", url=sqlite_url(db))
    decision = enforce_startup_gate(db, config={"storage": {}})
    # config dict may not nest — use simple object
    class C:
        def get(self, key, default=None):
            return default

    decision = enforce_startup_gate(db, config=C())
    assert decision.status.at_head
    assert decision.write_allowed is True
    assert decision.skipped is False


def test_behind_head_blocks_write(tmp_path: Path, enforce_env):
    db = tmp_path / "behind.db"
    alembic_cmd("upgrade", "j002_evidence_stale", url=sqlite_url(db))
    assert get_migration_status(db).at_head is False

    class C:
        def get(self, key, default=None):
            if key == "storage.migration_gate.allow_unstamped":
                return False
            if key == "storage.migration_gate.enabled":
                return True
            return default

    with pytest.raises(MigrationGateError) as ei:
        enforce_startup_gate(db, config=C())
    assert "alembic upgrade head" in str(ei.value).lower() or "migration" in str(ei.value).lower()


def test_behind_head_readonly_allowed(tmp_path: Path, enforce_env):
    db = tmp_path / "ro.db"
    alembic_cmd("upgrade", "j002_evidence_stale", url=sqlite_url(db))

    class C:
        def get(self, key, default=None):
            if key == "storage.readonly":
                return True
            if key == "storage.migration_gate.enabled":
                return True
            return default

    decision = enforce_startup_gate(db, config=C())
    assert decision.readonly is True
    assert decision.write_allowed is False


def test_unstamped_with_tables_blocked_by_default(tmp_path: Path, enforce_env):
    """WP4: allow_unstamped default False — write boot blocked."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE knowledge_items (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    class C:
        def get(self, key, default=None):
            if key == "storage.migration_gate.enabled":
                return True
            return default

    with pytest.raises(MigrationGateError):
        enforce_startup_gate(db, config=C())


def test_unstamped_strict_blocks(tmp_path: Path, enforce_env):
    db = tmp_path / "legacy2.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE knowledge_items (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    class C:
        def get(self, key, default=None):
            mapping = {
                "storage.migration_gate.enabled": True,
                "storage.migration_gate.allow_unstamped": False,
            }
            return mapping.get(key, default)

    with pytest.raises(MigrationGateError):
        enforce_startup_gate(db, config=C())


def test_skip_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SHINEHE_SKIP_MIGRATION_GATE", "1")
    monkeypatch.delenv("SHINEHE_ENFORCE_MIGRATION_GATE", raising=False)
    db = tmp_path / "x.db"
    decision = enforce_startup_gate(db)
    assert decision.skipped is True
    assert decision.write_allowed is True


def test_head_matches_script_directory():
    assert get_alembic_head() == head_revision()
