"""Repeated upgrade head is a no-op success."""
from __future__ import annotations

from pathlib import Path

from tests.migrations._helpers import (
    alembic_cmd,
    head_revision,
    read_revision,
    sqlite_url,
    table_names,
)


def test_upgrade_head_idempotent(tmp_path: Path):
    db = tmp_path / "idem.db"
    url = sqlite_url(db)

    alembic_cmd("upgrade", "head", url=url)
    rev1 = read_revision(db)
    tables1 = table_names(db)

    alembic_cmd("upgrade", "head", url=url)
    rev2 = read_revision(db)
    tables2 = table_names(db)

    assert rev1 == rev2 == head_revision()
    assert tables1 == tables2
