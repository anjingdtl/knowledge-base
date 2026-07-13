"""add stale/stale_at to wiki_claim_evidence (Phase 5)

Revision ID: j002_evidence_stale
Revises: j001_wiki_v2_projection
Create Date: 2026-07-13

Phase 5 来源失效传播:标记 stale evidence(stale/stale_at)。
db.py _SCHEMA 已补这两列(新库直接建全),此迁移对老库幂等补列。
downgrade 保守不删列(stale 是审计信息,对齐 d03)。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "j002_evidence_stale"
down_revision: Union[str, None] = "j001_wiki_v2_projection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("wiki_claim_evidence")}
    if "stale" not in cols:
        op.add_column(
            "wiki_claim_evidence",
            sa.Column("stale", sa.Integer, nullable=False, server_default="0"),
        )
    if "stale_at" not in cols:
        op.add_column(
            "wiki_claim_evidence",
            sa.Column("stale_at", sa.Text, nullable=False, server_default=""),
        )


def downgrade() -> None:
    # 保守不删列:stale 是审计信息,downgrade 保留(对齐 d03)
    pass
