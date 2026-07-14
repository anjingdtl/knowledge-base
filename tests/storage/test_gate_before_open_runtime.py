"""WP2-T2: Migration Gate must run before Database.open_runtime()."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.migrations._helpers import alembic_cmd, sqlite_url


@pytest.fixture
def enforce_env(monkeypatch):
    monkeypatch.setenv("SHINEHE_ENFORCE_MIGRATION_GATE", "1")
    monkeypatch.delenv("SHINEHE_SKIP_MIGRATION_GATE", raising=False)
    monkeypatch.delenv("SHINEHE_READONLY", raising=False)


def test_behind_head_write_never_calls_open_runtime(
    tmp_path: Path, monkeypatch, enforce_env
):
    from src.core import container as container_mod
    from src.services.db import Database
    from src.storage.startup_gate import MigrationGateError
    from src.utils.config import Config

    db = tmp_path / "behind.db"
    alembic_cmd("upgrade", "j002_evidence_stale", url=sqlite_url(db))

    open_calls: list[tuple] = []
    init_calls: list[int] = []

    def fake_open(cls, db_path, *, readonly: bool = False):
        open_calls.append((str(db_path), readonly))
        raise AssertionError("open_runtime must not be called when gate blocks")

    orig_init = Database.__init__

    def tracking_init(self, db_path):
        init_calls.append(1)
        return orig_init(self, db_path)

    monkeypatch.setattr(Database, "open_runtime", classmethod(fake_open))
    monkeypatch.setattr(Database, "__init__", tracking_init)
    Database._instance = None

    # Point Config db path at behind-head DB (covers class and instance dualmethod)
    monkeypatch.setattr(
        Config,
        "get_db_path",
        lambda *args, **kwargs: db,
    )

    with pytest.raises(MigrationGateError):
        container_mod.create_container()

    assert open_calls == [], f"open_runtime was called: {open_calls}"
    assert init_calls == [], f"Database.__init__ was called: {init_calls}"
    assert Database._instance is None
