"""WP4-T5: unstamped legacy migration matrix."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.migrations._helpers import alembic_cmd, head_revision, read_revision, sqlite_url


@pytest.fixture
def enforce_env(monkeypatch):
    monkeypatch.setenv("SHINEHE_ENFORCE_MIGRATION_GATE", "1")
    monkeypatch.delenv("SHINEHE_SKIP_MIGRATION_GATE", raising=False)
    monkeypatch.delenv("SHINEHE_READONLY", raising=False)


def _make_v19_unstamped(path: Path) -> None:
    """Create a v1.9-like unstamped DB: alembic upgrade j002 then drop alembic_version."""
    alembic_cmd("upgrade", "j002_evidence_stale", url=sqlite_url(path))
    conn = sqlite3.connect(str(path))
    conn.execute("DELETE FROM alembic_version")
    conn.execute("DROP TABLE alembic_version")
    # seed business row
    conn.execute(
        "INSERT OR IGNORE INTO knowledge_items "
        "(id, title, content, created_at, updated_at) "
        "VALUES ('k-legacy-1', 'Legacy Title', 'legacy body', "
        "datetime('now'), datetime('now'))"
    )
    if conn.execute(
        "SELECT name FROM sqlite_master WHERE name='blocks'"
    ).fetchone():
        conn.execute(
            "INSERT OR IGNORE INTO blocks (id, page_id, content, block_type, order_idx) "
            "VALUES ('b1', 'k-legacy-1', 'block text', 'text', 0)"
        )
    conn.commit()
    conn.close()
    assert read_revision(path) is None


def test_known_v19_unstamped_write_boot_blocked(tmp_path: Path, enforce_env, monkeypatch):
    from src.core import container as container_mod
    from src.services.db import Database
    from src.storage.startup_gate import MigrationGateError
    from src.utils.config import Config

    db = tmp_path / "v19.db"
    _make_v19_unstamped(db)
    Database._instance = None
    monkeypatch.setattr(Config, "get_db_path", lambda *a, **k: db)
    with pytest.raises(MigrationGateError):
        container_mod.create_container()
    assert Database._instance is None
    assert read_revision(db) is None


def test_known_v19_cli_migrate_preserves_data(tmp_path: Path, enforce_env):
    from src.storage.legacy_schema_detector import detect_legacy_schema
    from src.storage.migration_cli import (
        count_business_rows,
        migrate_database,
        verify_database,
    )

    db = tmp_path / "v19m.db"
    _make_v19_unstamped(db)
    match = detect_legacy_schema(db)
    assert match.confidence == "high"
    assert match.matched_version == "v1.9.x"
    assert match.stamp_revision == "j002_evidence_stale"

    before = count_business_rows(db)
    assert before.get("knowledge_items") >= 1

    result = migrate_database(db)
    assert result["ok"] is True
    assert read_revision(db) == head_revision()
    after = count_business_rows(db)
    assert after.get("knowledge_items") >= before.get("knowledge_items")
    verification = verify_database(db, before_counts=before)
    assert verification["passed"] is True
    # backup exists
    backups = list(tmp_path.glob("v19m.db.backup-*.sqlite"))
    assert backups


def test_unknown_unstamped_migrate_refused(tmp_path: Path):
    from src.storage.migration_cli import MigrationWorkflowError, migrate_database

    db = tmp_path / "unknown.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE totally_weird (x INTEGER)")
    conn.execute("INSERT INTO totally_weird VALUES (1)")
    conn.commit()
    conn.close()

    with pytest.raises(MigrationWorkflowError) as ei:
        migrate_database(db)
    assert "unknown" in str(ei.value).lower() or "confidence" in str(ei.value).lower()
    assert read_revision(db) is None


def test_unknown_unstamped_readonly_boot_allowed(
    tmp_path: Path, enforce_env, monkeypatch
):
    from src.core import container as container_mod
    from src.services.db import Database
    from src.utils.config import Config

    db = tmp_path / "unk_ro.db"
    conn = sqlite3.connect(str(db))
    # Minimal core so open doesn't explode on missing everything — still unstamped
    # and detector unknown if missing full v19 set
    conn.execute(
        "CREATE TABLE knowledge_items (id TEXT PRIMARY KEY, title TEXT, content TEXT)"
    )
    conn.execute("INSERT INTO knowledge_items VALUES ('a','t','c')")
    conn.commit()
    conn.close()

    Database._instance = None
    monkeypatch.setattr(Config, "get_db_path", lambda *a, **k: db)
    monkeypatch.setenv("SHINEHE_READONLY", "1")
    c = container_mod.create_container()
    try:
        assert c.db._readonly is True
        assert getattr(c, "write_allowed", True) is False
        n = c.db.get_conn().execute(
            "SELECT COUNT(*) FROM knowledge_items"
        ).fetchone()[0]
        assert n == 1
    finally:
        c.db.close()
        Database._instance = None
        monkeypatch.delenv("SHINEHE_READONLY", raising=False)


def test_hand_modified_schema_not_high_confidence(tmp_path: Path):
    from src.storage.legacy_schema_detector import detect_legacy_schema
    from src.storage.migration_cli import MigrationWorkflowError, stamp_database

    db = tmp_path / "modified.db"
    _make_v19_unstamped(db)
    # Hand-modify: drop a required marker table
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE wiki_pages")
    conn.commit()
    conn.close()

    match = detect_legacy_schema(db)
    assert match.confidence != "high"
    with pytest.raises(MigrationWorkflowError):
        stamp_database(db, from_version="v1.9.x")


def test_migrate_failure_restores_backup(tmp_path: Path, monkeypatch):
    from src.storage import migration_cli
    from src.storage.migration_cli import MigrationWorkflowError, migrate_database

    db = tmp_path / "fail.db"
    _make_v19_unstamped(db)
    conn = sqlite3.connect(str(db))
    title_before = conn.execute(
        "SELECT title FROM knowledge_items WHERE id='k-legacy-1'"
    ).fetchone()[0]
    conn.close()

    def boom(*a, **k):
        raise RuntimeError("simulated upgrade failure")

    # migrate_database binds upgrade_to_head at import time
    monkeypatch.setattr(migration_cli, "upgrade_to_head", boom)

    with pytest.raises(MigrationWorkflowError) as ei:
        migrate_database(db)
    assert "restor" in str(ei.value).lower() or "failed" in str(ei.value).lower()
    # Data still readable and unstamped (restored)
    conn = sqlite3.connect(str(db))
    title_after = conn.execute(
        "SELECT title FROM knowledge_items WHERE id='k-legacy-1'"
    ).fetchone()[0]
    conn.close()
    assert title_after == title_before
    # failed copy kept
    failed = list(tmp_path.glob("fail.db.failed-migration-*.sqlite"))
    assert failed or list(tmp_path.glob("*.backup-*.sqlite"))


def test_stamp_head_forbidden(tmp_path: Path):
    from src.storage.migration_cli import MigrationWorkflowError, stamp_database

    db = tmp_path / "s.db"
    _make_v19_unstamped(db)
    with pytest.raises(MigrationWorkflowError):
        stamp_database(db, from_version="head")
