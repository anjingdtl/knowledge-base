"""add canonical wiki v2 projection tables (6 tables + fts)

Revision ID: j001_wiki_v2_projection
Revises: i001_version_conflict
Create Date: 2026-07-08

新增 Canonical Wiki v2 SQLite 投影层（Phase 2）:
- wiki_pages_v2 / wiki_claims / wiki_claim_evidence / wiki_page_claims
  / wiki_dependencies / wiki_projection_state + wiki_pages_v2_fts
全部 if_not_exists=True:db.py _SCHEMA 已用 CREATE TABLE IF NOT EXISTS
建同名表(app 启动即建),此处必须幂等,否则在已跑过 app 的库上执行
alembic upgrade head 会报 "table already exists"。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "j001_wiki_v2_projection"
down_revision: Union[str, Sequence[str], None] = "i001_version_conflict"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 6 张 v2 投影表 + FTS 虚拟表(全部幂等)。"""
    # 1. wiki_pages_v2
    op.create_table(
        "wiki_pages_v2",
        sa.Column("page_id", sa.Text, primary_key=True),
        sa.Column("path", sa.Text, nullable=False, unique=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("page_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("aliases_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("tags_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("source_ids_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("claim_ids_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        if_not_exists=True,
    )
    op.create_index("idx_wiki_pages_v2_type", "wiki_pages_v2", ["page_type"], if_not_exists=True)
    op.create_index("idx_wiki_pages_v2_status", "wiki_pages_v2", ["status"], if_not_exists=True)

    # 2. wiki_claims
    op.create_table(
        "wiki_claims",
        sa.Column("claim_id", sa.Text, primary_key=True),
        sa.Column("statement", sa.Text, nullable=False),
        sa.Column("normalized_statement", sa.Text, nullable=False),
        sa.Column("claim_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("confidence", sa.REAL, nullable=False),
        sa.Column("claim_scope", sa.Text),
        sa.Column("valid_from", sa.Text),
        sa.Column("valid_to", sa.Text),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        if_not_exists=True,
    )
    op.create_index("idx_wiki_claims_status", "wiki_claims", ["status"], if_not_exists=True)
    op.create_index("idx_wiki_claims_normalized", "wiki_claims", ["normalized_statement"], if_not_exists=True)

    # 3. wiki_claim_evidence
    op.create_table(
        "wiki_claim_evidence",
        sa.Column("evidence_id", sa.Text, primary_key=True),
        sa.Column("claim_id", sa.Text, nullable=False),
        sa.Column("stance", sa.Text, nullable=False),
        sa.Column("knowledge_id", sa.Text, nullable=False),
        sa.Column("block_id", sa.Text),
        sa.Column("location_json", sa.Text, nullable=False),
        sa.Column("source_revision", sa.Text, nullable=False),
        sa.Column("excerpt_hash", sa.Text),
        sa.Column("observed_at", sa.Text, nullable=False),
        sa.UniqueConstraint(
            "claim_id", "knowledge_id", "block_id", "stance", "source_revision",
            name="uq_wiki_evidence_triple",
        ),
        if_not_exists=True,
    )
    op.create_index("idx_wiki_evidence_claim", "wiki_claim_evidence", ["claim_id"], if_not_exists=True)
    op.create_index("idx_wiki_evidence_kid", "wiki_claim_evidence", ["knowledge_id"], if_not_exists=True)

    # 4. wiki_page_claims
    op.create_table(
        "wiki_page_claims",
        sa.Column("page_id", sa.Text, nullable=False),
        sa.Column("claim_id", sa.Text, nullable=False),
        sa.Column("display_order", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("page_id", "claim_id", name="pk_wiki_page_claims"),
        if_not_exists=True,
    )
    op.create_index("idx_wiki_page_claims_claim", "wiki_page_claims", ["claim_id"], if_not_exists=True)

    # 5. wiki_dependencies
    op.create_table(
        "wiki_dependencies",
        sa.Column("from_type", sa.Text, nullable=False),
        sa.Column("from_id", sa.Text, nullable=False),
        sa.Column("to_type", sa.Text, nullable=False),
        sa.Column("to_id", sa.Text, nullable=False),
        sa.Column("relation", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint(
            "from_type", "from_id", "to_type", "to_id", "relation",
            name="pk_wiki_dependencies",
        ),
        if_not_exists=True,
    )
    op.create_index("idx_wiki_deps_from", "wiki_dependencies", ["from_type", "from_id"], if_not_exists=True)
    op.create_index("idx_wiki_deps_to", "wiki_dependencies", ["to_type", "to_id"], if_not_exists=True)

    # 6. wiki_projection_state (key-value)
    op.create_table(
        "wiki_projection_state",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        if_not_exists=True,
    )

    # 7. wiki_pages_v2_fts (虚拟表, op.execute 直接 DDL)
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS wiki_pages_v2_fts USING fts5("
        "page_id UNINDEXED, title, content, tokenize='unicode61')"
    )


def downgrade() -> None:
    """回滚:删 v2 投影层(全部 IF EXISTS),不碰旧 wiki_* 表。"""
    op.execute("DROP TABLE IF EXISTS wiki_pages_v2_fts")
    op.drop_table("wiki_projection_state")
    op.drop_index("idx_wiki_deps_to", table_name="wiki_dependencies")
    op.drop_index("idx_wiki_deps_from", table_name="wiki_dependencies")
    op.drop_table("wiki_dependencies")
    op.drop_index("idx_wiki_page_claims_claim", table_name="wiki_page_claims")
    op.drop_table("wiki_page_claims")
    op.drop_index("idx_wiki_evidence_kid", table_name="wiki_claim_evidence")
    op.drop_index("idx_wiki_evidence_claim", table_name="wiki_claim_evidence")
    op.drop_table("wiki_claim_evidence")
    op.drop_index("idx_wiki_claims_normalized", table_name="wiki_claims")
    op.drop_index("idx_wiki_claims_status", table_name="wiki_claims")
    op.drop_table("wiki_claims")
    op.drop_index("idx_wiki_pages_v2_status", table_name="wiki_pages_v2")
    op.drop_index("idx_wiki_pages_v2_type", table_name="wiki_pages_v2")
    op.drop_table("wiki_pages_v2")
