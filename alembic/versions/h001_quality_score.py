"""add quality_score column to knowledge_items

Phase 1 / data-heal: 内容质量评分字段。
- quality_score INTEGER, 默认 NULL, 范围 0-100
- 0 = 空/无效内容, 100 = 最优
- 与原有 quality TEXT 字段并存（quality 存 ok/garbled, quality_score 存数值评分）
- 加索引 idx_kb_quality_score 便于过滤低质量条目

Revision ID: h001_quality_score
Revises: 711b03e11f10
Create Date: 2026-06-23
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "h001_quality_score"
down_revision: Union[str, Sequence[str], None] = "711b03e11f10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为 knowledge_items 加 quality_score 列 + 索引。"""
    op.add_column(
        "knowledge_items",
        sa.Column("quality_score", sa.Integer, nullable=True),
    )
    op.create_index("idx_kb_quality_score", "knowledge_items", ["quality_score"])


def downgrade() -> None:
    """回滚：删索引 + 删列。"""
    op.drop_index("idx_kb_quality_score", table_name="knowledge_items")
    op.drop_column("knowledge_items", "quality_score")
