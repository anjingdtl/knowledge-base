"""WP3-T1: explicit Alembic upgrade_to_head service."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.migrations._helpers import head_revision, read_revision, table_names


def test_upgrade_to_head_creates_schema_at_explicit_path(tmp_path: Path):
    from src.storage.alembic_runner import upgrade_to_head

    db = tmp_path / "explicit.db"
    assert not db.exists()
    result = upgrade_to_head(db)
    assert result.upgraded is True
    assert result.before_revision is None
    assert result.after_revision == head_revision()
    assert Path(result.db_path) == db.resolve() or result.db_path == str(db)
    assert read_revision(db) == head_revision()
    tables = table_names(db)
    assert "alembic_version" in tables
    assert "knowledge_items" in tables


def test_upgrade_to_head_is_idempotent(tmp_path: Path):
    from src.storage.alembic_runner import upgrade_to_head

    db = tmp_path / "idem.db"
    first = upgrade_to_head(db)
    second = upgrade_to_head(db)
    assert first.after_revision == second.after_revision == head_revision()
    assert second.before_revision == head_revision()
    assert second.upgraded is False


def test_upgrade_to_head_does_not_use_config_default_db(
    tmp_path: Path, monkeypatch
):
    """Must never fall back to Config/user default path."""
    from src.storage import alembic_runner
    from src.utils.config import Config

    poison = tmp_path / "poison-user-default.db"
    monkeypatch.setattr(Config, "get_db_path", lambda *a, **k: poison)
    # If runner wrongly uses Config, poison would be created
    target = tmp_path / "target-only.db"
    alembic_runner.upgrade_to_head(target)
    assert target.exists()
    assert not poison.exists()


def test_upgrade_to_head_failure_raises(tmp_path: Path, monkeypatch):
    from src.storage import alembic_runner
    from src.storage.alembic_runner import AlembicUpgradeError, upgrade_to_head

    db = tmp_path / "fail.db"

    def boom(*args, **kwargs):
        raise RuntimeError("simulated alembic failure")

    monkeypatch.setattr(alembic_runner, "_run_alembic_upgrade", boom)
    with pytest.raises(AlembicUpgradeError) as ei:
        upgrade_to_head(db)
    assert "simulated" in str(ei.value).lower() or "alembic" in str(ei.value).lower()
