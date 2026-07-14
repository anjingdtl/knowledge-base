"""WP2-T3: readonly open_runtime uses SQLite mode=ro without schema mutation."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.migrations._helpers import alembic_cmd, sqlite_url


def test_readonly_open_uses_mode_ro_and_skips_schema(tmp_path: Path, monkeypatch):
    from src.services.db import Database

    db = tmp_path / "ro.db"
    alembic_cmd("upgrade", "head", url=sqlite_url(db))
    Database._instance = None

    # Poison _SCHEMA / _migrate so any call fails loudly
    monkeypatch.setattr(
        Database,
        "_migrate",
        lambda self: (_ for _ in ()).throw(AssertionError("_migrate must not run")),
    )

    def ban_connect_internal(self):
        raise AssertionError("_connect_internal (write path) must not run in readonly")

    monkeypatch.setattr(Database, "_connect_internal", ban_connect_internal)

    inst = Database.open_runtime(db, readonly=True)
    try:
        assert inst._readonly is True
        # Read works
        row = inst.get_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1"
        ).fetchone()
        assert row is not None
        # Writes must fail on readonly connection
        with pytest.raises(sqlite3.OperationalError):
            inst.get_conn().execute(
                "CREATE TABLE IF NOT EXISTS __wp2_ro_probe (id INTEGER)"
            )
    finally:
        inst.close()
        Database._instance = None


def test_readonly_open_does_not_create_wal(tmp_path: Path):
    from src.services.db import Database

    db = tmp_path / "nowal.db"
    # Create as non-WAL first
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()
    Database._instance = None

    before = set(tmp_path.iterdir())
    inst = Database.open_runtime(db, readonly=True)
    try:
        inst.get_conn().execute("SELECT 1").fetchone()
    finally:
        inst.close()
        Database._instance = None

    after = set(tmp_path.iterdir())
    new_files = {p.name for p in after - before}
    assert not any(n.endswith("-wal") or n.endswith("-shm") for n in new_files), new_files


def test_readonly_open_missing_file_raises(tmp_path: Path):
    from src.services.db import Database

    Database._instance = None
    missing = tmp_path / "nope.db"
    with pytest.raises(FileNotFoundError):
        Database.open_runtime(missing, readonly=True)
    assert not missing.exists()


def test_readonly_open_preserves_schema_fingerprint(tmp_path: Path):
    import importlib.util

    from src.services.db import Database
    from tests.migrations._helpers import ROOT

    db = tmp_path / "fp.db"
    alembic_cmd("upgrade", "j002_evidence_stale", url=sqlite_url(db))

    spec = importlib.util.spec_from_file_location(
        "schema_fingerprint", ROOT / "tools" / "schema_fingerprint.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    before = mod.compute_schema_fingerprint(db)
    Database._instance = None
    inst = Database.open_runtime(db, readonly=True)
    try:
        list(inst.get_conn().execute("SELECT name FROM sqlite_master"))
    finally:
        inst.close()
        Database._instance = None
    after = mod.compute_schema_fingerprint(db)
    assert after == before
