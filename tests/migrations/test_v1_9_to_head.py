"""Simulate prior-version stamp then upgrade to head.

Uses j002 (pre-maintenance control plane) as a stand-in for a v1.9-era DB
that still needs the latest revision(s).
"""
from __future__ import annotations

from pathlib import Path

from tests.migrations._helpers import (
    alembic_cmd,
    head_revision,
    read_revision,
    sqlite_url,
    table_names,
)

# Last revision before maintenance control-plane (j003)
_PRIOR = "j002_evidence_stale"


def test_v1_9_style_revision_upgrades_to_head(tmp_path: Path):
    db = tmp_path / "v19.db"
    url = sqlite_url(db)

    alembic_cmd("upgrade", _PRIOR, url=url)
    assert read_revision(db) == _PRIOR
    before = table_names(db)

    alembic_cmd("upgrade", "head", url=url)
    assert read_revision(db) == head_revision()
    after = table_names(db)
    # head adds maintenance tables (j003)
    assert after >= before
    assert "alembic_version" in after
