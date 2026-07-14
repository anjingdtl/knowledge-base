"""WP5-T4: production runtime must not mutate schema fingerprint."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.migrations._helpers import ROOT, alembic_cmd, sqlite_url


@pytest.fixture
def enforce_env(monkeypatch):
    monkeypatch.setenv("SHINEHE_ENFORCE_MIGRATION_GATE", "1")
    monkeypatch.delenv("SHINEHE_SKIP_MIGRATION_GATE", raising=False)
    monkeypatch.delenv("SHINEHE_READONLY", raising=False)


def _fingerprint(db: Path) -> dict:
    spec = importlib.util.spec_from_file_location(
        "schema_fingerprint", ROOT / "tools" / "schema_fingerprint.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.compute_schema_fingerprint(db)


def test_container_runtime_does_not_change_schema_fingerprint(
    tmp_path: Path, monkeypatch, enforce_env
):
    from src.core import container as container_mod
    from src.services.db import Database
    from src.utils.config import Config

    db = tmp_path / "head_ro_schema.db"
    alembic_cmd("upgrade", "head", url=sqlite_url(db))
    before = _fingerprint(db)

    Database._instance = None
    monkeypatch.setattr(Config, "get_db_path", lambda *a, **k: db)

    c = container_mod.create_container()
    try:
        # Lightweight service-layer reads (no schema DDL). Avoid hybrid search
        # paths that may touch optional vec0 virtual tables.
        assert c.db is not None
        _ = c.db.get_conn().execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()
        try:
            _ = c.wiki_serving_gate
        except Exception:
            pass
        try:
            _ = c.path_indexer
        except Exception:
            pass
        # Business write must not alter schema
        c.db.get_conn().execute(
            "INSERT OR IGNORE INTO knowledge_items "
            "(id, title, content, created_at, updated_at) "
            "VALUES ('wp5-probe', 't', 'c', datetime('now'), datetime('now'))"
        )
        c.db.get_conn().commit()
    finally:
        try:
            c.db.close()
        except Exception:
            pass
        Database._instance = None

    after = _fingerprint(db)
    assert after == before


def test_open_runtime_rejects_direct_migrate_call(tmp_path: Path):
    from src.services.db import Database

    db = tmp_path / "m.db"
    alembic_cmd("upgrade", "head", url=sqlite_url(db))
    Database._instance = None
    inst = Database.open_runtime(db, readonly=False)
    try:
        with pytest.raises(RuntimeError, match="schema migration removed"):
            inst._migrate()
    finally:
        inst.close()
        Database._instance = None
