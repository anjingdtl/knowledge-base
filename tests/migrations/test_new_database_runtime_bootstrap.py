"""WP3-T3: new databases initialized exclusively through Alembic."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.migrations._helpers import ROOT, alembic_cmd, head_revision, read_revision, sqlite_url


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


def test_create_container_empty_db_via_alembic_matches_direct_upgrade(
    tmp_path: Path, monkeypatch, enforce_env
):
    from src.core import container as container_mod
    from src.services.db import Database
    from src.utils.config import Config

    # Reference DB created only by alembic
    ref = tmp_path / "ref_alembic.db"
    alembic_cmd("upgrade", "head", url=sqlite_url(ref))
    ref_fp = _fingerprint(ref)

    runtime_db = tmp_path / "runtime_new.db"
    assert not runtime_db.exists()

    Database._instance = None
    monkeypatch.setattr(Config, "get_db_path", lambda *a, **k: runtime_db)

    c = container_mod.create_container()
    try:
        assert runtime_db.is_file()
        assert read_revision(runtime_db) == head_revision()
        assert c.db._readonly is False
        # Can read schema objects
        tables = {
            r[0]
            for r in c.db.get_conn().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "knowledge_items" in tables
        assert "alembic_version" in tables
        assert _fingerprint(runtime_db) == ref_fp
    finally:
        try:
            c.db.close()
        except Exception:
            pass
        Database._instance = None


def test_create_container_empty_db_idempotent_second_boot(
    tmp_path: Path, monkeypatch, enforce_env
):
    from src.core import container as container_mod
    from src.services.db import Database
    from src.utils.config import Config

    runtime_db = tmp_path / "runtime_idem.db"
    Database._instance = None
    monkeypatch.setattr(Config, "get_db_path", lambda *a, **k: runtime_db)

    c1 = container_mod.create_container()
    try:
        fp1 = _fingerprint(runtime_db)
    finally:
        c1.db.close()
        Database._instance = None

    c2 = container_mod.create_container()
    try:
        fp2 = _fingerprint(runtime_db)
        assert fp2 == fp1
        assert read_revision(runtime_db) == head_revision()
    finally:
        c2.db.close()
        Database._instance = None


def test_auto_upgrade_empty_false_blocks_missing_db(
    tmp_path: Path, monkeypatch, enforce_env
):
    from src.core import container as container_mod
    from src.services.db import Database
    from src.storage.startup_gate import MigrationGateError
    from src.utils.config import Config

    runtime_db = tmp_path / "no_auto.db"
    Database._instance = None
    monkeypatch.setattr(Config, "get_db_path", lambda *a, **k: runtime_db)

    real_get = Config.get

    def get_no_auto(*args, **kwargs):
        # dualmethod: Config.get(key, default) or instance.get(key, default)
        if args and isinstance(args[0], str):
            key, default = args[0], (args[1] if len(args) > 1 else kwargs.get("default"))
            if key == "storage.migration_gate.auto_upgrade_empty":
                return False
            return real_get(key, default)
        # bound call: (self, key, default)
        if len(args) >= 2 and isinstance(args[1], str):
            key = args[1]
            default = args[2] if len(args) > 2 else kwargs.get("default")
            if key == "storage.migration_gate.auto_upgrade_empty":
                return False
            return real_get(args[0], key, default) if False else real_get(key, default)
        return real_get(*args, **kwargs)

    monkeypatch.setattr(Config, "get", get_no_auto)

    with pytest.raises(MigrationGateError):
        container_mod.create_container()
    assert not runtime_db.exists()
