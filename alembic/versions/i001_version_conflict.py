"""add version conflict tables (sessions / pairs / ignores)

Revision ID: i001_version_conflict
Revises: h001_quality_score
Create Date: 2026-06-28

新增三张表用于版本冲突检测与清理：
- conflict_sessions: 扫描会话
- conflict_pairs: 候选对（含 LLM 判断结果与状态机）
- conflict_ignores: 忽略列表（pair_key 归一化，避免 A/B 与 B/A 重复）
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "i001_version_conflict"
down_revision: Union[str, Sequence[str], None] = "h001_quality_score"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 conflict_sessions / conflict_pairs / conflict_ignores 三张表。

    全部 ``if_not_exists=True``:db.py 的 ``_SCHEMA`` 已用 ``CREATE TABLE IF NOT
    EXISTS`` 建同名表(app 启动即建),此处再建必须幂等,否则在已跑过 app 的库上
    执行 ``alembic upgrade head`` 会报 "table already exists"。
    """
    # 表 1：扫描会话
    op.create_table(
        "conflict_sessions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("status", sa.Text, nullable=False, server_default="scanning"),
        sa.Column("total_items_scanned", sa.Integer, server_default="0"),
        sa.Column("candidates_found", sa.Integer, server_default="0"),
        sa.Column("pairs_judged", sa.Integer, server_default="0"),
        sa.Column("pairs_deleted", sa.Integer, server_default="0"),
        sa.Column("pairs_ignored", sa.Integer, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("started_at", sa.Text, nullable=False),
        sa.Column("completed_at", sa.Text),
        if_not_exists=True,
    )

    # 表 2：候选对
    op.create_table(
        "conflict_pairs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("session_id", sa.Text, nullable=False),
        sa.Column("item_a_id", sa.Text, nullable=False),
        sa.Column("item_b_id", sa.Text, nullable=False),
        sa.Column("candidate_source", sa.Text, nullable=False),
        sa.Column("similarity_score", sa.REAL),
        sa.Column("relation_type", sa.Text),
        sa.Column("newer_item_id", sa.Text),
        sa.Column("confidence", sa.REAL),
        sa.Column("reason", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("judged_at", sa.Text),
        sa.Column("resolved_at", sa.Text),
        sa.ForeignKeyConstraint(["session_id"], ["conflict_sessions.id"]),
        if_not_exists=True,
    )
    op.create_index("idx_conflict_pairs_session", "conflict_pairs", ["session_id"], if_not_exists=True)
    op.create_index("idx_conflict_pairs_status", "conflict_pairs", ["status"], if_not_exists=True)
    op.create_index("idx_conflict_pairs_items", "conflict_pairs", ["item_a_id", "item_b_id"], if_not_exists=True)

    # 表 3：忽略列表
    op.create_table(
        "conflict_ignores",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("item_a_id", sa.Text, nullable=False),
        sa.Column("item_b_id", sa.Text, nullable=False),
        sa.Column("pair_key", sa.Text, nullable=False),
        sa.Column("ignored_at", sa.Text, nullable=False),
        sa.Column("source_pair_id", sa.Text),
        if_not_exists=True,
    )
    op.create_index("idx_conflict_ignores_pair", "conflict_ignores", ["pair_key"], if_not_exists=True)
    # pair_key 唯一约束
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_conflict_ignores_pair_unique ON conflict_ignores(pair_key)")


def downgrade() -> None:
    """回滚：删三张表。"""
    op.execute("DROP INDEX IF EXISTS idx_conflict_ignores_pair_unique")
    op.drop_index("idx_conflict_ignores_pair", table_name="conflict_ignores")
    op.drop_table("conflict_ignores")
    op.drop_index("idx_conflict_pairs_items", table_name="conflict_pairs")
    op.drop_index("idx_conflict_pairs_status", table_name="conflict_pairs")
    op.drop_index("idx_conflict_pairs_session", table_name="conflict_pairs")
    op.drop_table("conflict_pairs")
    op.drop_table("conflict_sessions")
