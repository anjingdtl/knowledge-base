"""empty database → alembic upgrade head."""
from __future__ import annotations

from pathlib import Path

from tests.migrations._helpers import (
    alembic_cmd,
    head_revision,
    read_revision,
    sqlite_url,
    table_names,
)


def test_empty_to_head(tmp_path: Path):
    db = tmp_path / "empty.db"
    url = sqlite_url(db)
    alembic_cmd("upgrade", "head", url=url)

    rev = read_revision(db)
    assert rev == head_revision()
    tables = table_names(db)
    assert "alembic_version" in tables
    # baseline schema should include knowledge items
    assert "knowledge_items" in tables or "knowledge" in tables or len(tables) >= 3
