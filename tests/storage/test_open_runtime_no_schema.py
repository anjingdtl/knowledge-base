"""WP3-T4: open_runtime must not execute _SCHEMA or _migrate."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.migrations._helpers import alembic_cmd, head_revision, read_revision, sqlite_url


def test_write_open_runtime_skips_schema_and_migrate(tmp_path: Path, monkeypatch):
    from src.services.db import Database

    db = tmp_path / "head.db"
    alembic_cmd("upgrade", "head", url=sqlite_url(db))
    Database._instance = None

    monkeypatch.setattr(
        Database,
        "_migrate",
        lambda self: (_ for _ in ()).throw(AssertionError("_migrate must not run")),
    )
    monkeypatch.setattr(
        Database,
        "_connect_internal",
        lambda self: (_ for _ in ()).throw(
            AssertionError("_connect_internal must not run")
        ),
    )

    inst = Database.open_runtime(db, readonly=False)
    try:
        assert inst._readonly is False
        assert read_revision(db) == head_revision()
        n = inst.get_conn().execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        assert n >= 3
    finally:
        inst.close()
        Database._instance = None


def test_write_open_runtime_missing_file_raises(tmp_path: Path):
    from src.services.db import Database

    Database._instance = None
    missing = tmp_path / "absent.db"
    with pytest.raises(FileNotFoundError):
        Database.open_runtime(missing, readonly=False)
    assert not missing.exists()
