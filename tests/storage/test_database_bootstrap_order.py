"""WP2-T4: bootstrap order matrix — gate before mutation.

Covers behind-head write block, readonly behind-head, missing file inspect,
and unstamped non-mutating inspect. Uses tmp_path only.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from tests.migrations._helpers import ROOT, alembic_cmd, sqlite_url


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


def _fingerprint(db: Path) -> dict:
    spec = importlib.util.spec_from_file_location(
        "schema_fingerprint", ROOT / "tools" / "schema_fingerprint.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.compute_schema_fingerprint(db)


def test_behind_head_write_blocks_without_open_runtime(
    tmp_path: Path, monkeypatch, enforce_env
):
    from src.core import container as container_mod
    from src.services.db import Database
    from src.storage.startup_gate import MigrationGateError
    from src.utils.config import Config

    db = tmp_path / "behind.db"
    alembic_cmd("upgrade", "j002_evidence_stale", url=sqlite_url(db))
    fp_before = _fingerprint(db)

    open_calls: list = []

    def ban_open(cls, db_path, *, readonly: bool = False):
        open_calls.append((str(db_path), readonly))
        raise AssertionError("open_runtime must not run when gate blocks")

    monkeypatch.setattr(Database, "open_runtime", classmethod(ban_open))
    Database._instance = None
    monkeypatch.setattr(Config, "get_db_path", lambda *a, **k: db)

    with pytest.raises(MigrationGateError):
        container_mod.create_container()

    assert open_calls == []
    assert Database._instance is None
    assert _fingerprint(db) == fp_before


def test_behind_head_readonly_allows_open_without_schema_change(
    tmp_path: Path, monkeypatch, enforce_env
):
    from src.core import container as container_mod
    from src.services.db import Database
    from src.utils.config import Config

    db = tmp_path / "ro_behind.db"
    alembic_cmd("upgrade", "j002_evidence_stale", url=sqlite_url(db))
    fp_before = _fingerprint(db)

    Database._instance = None
    monkeypatch.setattr(Config, "get_db_path", lambda *a, **k: db)
    # Force readonly via config key used by resolve_readonly
    real_get = Config.get

    def get_with_ro(self_or_key=None, key=None, default=None, **kwargs):
        # dualmethod: Config.get(key) or self.get(key)
        if isinstance(self_or_key, str) and key is None:
            k, d = self_or_key, default
            if k == "storage.readonly":
                return True
            return real_get(k, d)
        k = key if key is not None else self_or_key
        d = default
        if k == "storage.readonly":
            return True
        try:
            return real_get(self_or_key, k, d)
        except TypeError:
            return real_get(k, d)

    monkeypatch.setattr(Config, "get", get_with_ro)
    monkeypatch.setenv("SHINEHE_READONLY", "1")

    container = container_mod.create_container()
    try:
        assert getattr(container, "write_allowed", True) is False
        assert container.db._readonly is True
        # Diagnostic read works
        n = container.db.get_conn().execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        assert n > 0
        assert _fingerprint(db) == fp_before
    finally:
        # Do not fully shutdown if it closes shared fixtures — just close this db
        try:
            container.db.close()
        except Exception:
            pass
        Database._instance = None
        monkeypatch.delenv("SHINEHE_READONLY", raising=False)


def test_missing_db_inspect_does_not_create_file(tmp_path: Path, enforce_env):
    from src.storage.database_bootstrap import (
        enforce_bootstrap_plan,
        inspect_database_bootstrap,
    )

    missing = tmp_path / "not_created_yet.db"
    plan = inspect_database_bootstrap(missing, config=_Cfg())
    enforce_bootstrap_plan(plan)  # write mode empty is allowed (deferred init)
    assert plan.empty is True
    assert plan.exists is False
    assert not missing.exists()
    assert plan.action == "init_empty"


def test_unstamped_inspect_does_not_modify(tmp_path: Path, enforce_env):
    from src.storage.database_bootstrap import inspect_database_bootstrap

    db = tmp_path / "unstamped.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE knowledge_items (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO knowledge_items(id) VALUES ('x')")
    conn.commit()
    tables_before = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    count_before = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
    conn.close()

    plan = inspect_database_bootstrap(db, config=_Cfg())
    assert plan.migration_status.unstamped is True
    # WP2: default allow_unstamped remains True — must not stamp during inspect
    assert plan.action != "block" or plan.write_allowed is False

    conn = sqlite3.connect(str(db))
    tables_after = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    count_after = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
    conn.close()
    assert tables_after == tables_before
    assert "alembic_version" not in tables_after
    assert count_after == count_before == 1
