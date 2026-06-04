"""add soft delete column to knowledge_items

Phase 4 / Sprint 3: 软删除 — knowledge_items 加 deleted_at 列。
- ``deleted_at IS NULL`` 表示未删除
- ``deleted_at`` 设为 ISO 时间戳表示已删除
- 同时建 ``idx_kb_deleted`` 索引加速过滤

Revision ID: e001_soft_delete_knowledge
Revises: d001_users_embedding_cache
Create Date: 2026-06-04
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "e001_soft_delete_knowledge"
down_revision: Union[str, Sequence[str], None] = "d001_users_embedding_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为 knowledge_items 加 deleted_at 列 + 索引。"""
    op.add_column(
        "knowledge_items",
        sa.Column("deleted_at", sa.Text, nullable=True),
    )
    op.create_index("idx_kb_deleted", "knowledge_items", ["deleted_at"])


def downgrade() -> None:
    """回滚：删索引 + 删列。"""
    op.drop_index("idx_kb_deleted", table_name="knowledge_items")
    op.drop_column("knowledge_items", "deleted_at")
