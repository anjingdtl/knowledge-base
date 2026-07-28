"""Independent retrieval_passages layer for semantic RAG (SPEC v3).

Graph ``blocks`` remain the structure/read unit; retrieval_passages are the
semantic unit for hybrid search and answer evidence.

Revision ID: k001_retrieval_passages
Revises: j004_runtime_schema_parity
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision = "k001_retrieval_passages"
down_revision: Union[str, None] = "j004_runtime_schema_parity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retrieval_passages",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("knowledge_id", sa.Text, nullable=False),
        sa.Column("document_family_id", sa.Text, server_default=""),
        sa.Column("family_confidence", sa.Float, server_default="0"),
        sa.Column("family_basis", sa.Text, server_default=""),
        sa.Column("source_version", sa.Text, server_default=""),
        sa.Column("version_year", sa.Integer),
        sa.Column("passage_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("text_hash", sa.Text, server_default=""),
        sa.Column("char_count", sa.Integer, server_default="0"),
        sa.Column("short_passage", sa.Integer, server_default="0"),
        sa.Column("title_prefix", sa.Text, server_default=""),
        sa.Column("section_path", sa.Text, server_default=""),
        sa.Column("block_ids_json", sa.Text, server_default="[]"),
        sa.Column("block_ranges_json", sa.Text, server_default="[]"),
        sa.Column("effective_year", sa.Integer),
        sa.Column("status", sa.Text, server_default="active"),
        sa.Column("created_at", sa.Text),
        sa.Column("updated_at", sa.Text),
        sa.Column("deleted_at", sa.Text),
        if_not_exists=True,
    )
    op.create_index(
        "idx_retrieval_passages_kid",
        "retrieval_passages",
        ["knowledge_id"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_retrieval_passages_family",
        "retrieval_passages",
        ["document_family_id"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_retrieval_passages_status",
        "retrieval_passages",
        ["status", "deleted_at"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_retrieval_passages_kid_idx",
        "retrieval_passages",
        ["knowledge_id", "passage_index"],
        unique=True,
        if_not_exists=True,
    )

    # FTS for passages (jieba-segmented text stored in fts_segmented).
    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS passage_fts USING fts5(
            fts_segmented,
            knowledge_id UNINDEXED,
            passage_id UNINDEXED,
            tokenize='unicode61'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS passage_fts")
    op.drop_index("idx_retrieval_passages_kid_idx", table_name="retrieval_passages")
    op.drop_index("idx_retrieval_passages_status", table_name="retrieval_passages")
    op.drop_index("idx_retrieval_passages_family", table_name="retrieval_passages")
    op.drop_index("idx_retrieval_passages_kid", table_name="retrieval_passages")
    op.drop_table("retrieval_passages")
