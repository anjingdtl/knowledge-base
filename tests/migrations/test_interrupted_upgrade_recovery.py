"""Multi-step upgrade recovers to head (simulated incomplete upgrade path)."""
from __future__ import annotations

from pathlib import Path

from tests.migrations._helpers import (
    alembic_cmd,
    head_revision,
    read_revision,
    sqlite_url,
)


def test_partial_upgrade_then_head_recovers(tmp_path: Path):
    """Upgrade to an intermediate revision, then complete to head twice."""
    db = tmp_path / "partial.db"
    url = sqlite_url(db)

    # Intermediate stamp (pre-head)
    alembic_cmd("upgrade", "j001_wiki_v2_projection", url=url)
    mid = read_revision(db)
    assert mid == "j001_wiki_v2_projection"
    assert mid != head_revision()

    # Complete
    alembic_cmd("upgrade", "head", url=url)
    assert read_revision(db) == head_revision()

    # Re-run as if process restarted after finishing / near-finish
    alembic_cmd("upgrade", "head", url=url)
    assert read_revision(db) == head_revision()
