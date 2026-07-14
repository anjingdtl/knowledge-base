"""add agent_memory table for Phase 4 Agent Memory tools

Phase 4 / Step 4.2: Agent Memory — 新增 agent_memory 表。
存储 Agent（Claude/Cursor/Cline）的长期记忆，支持事实/决策/上下文/任务四类。
- FTS5 全文索引支持语义搜索
- category 索引加速按类型过滤

Revision ID: f001_agent_memory
Revises: e001_soft_delete_knowledge
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f001_agent_memory"
down_revision: Union[str, Sequence[str], None] = "e001_soft_delete_knowledge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 agent_memory 表 + FTS5 索引。"""
    op.create_table(
        "agent_memory",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False, server_default="fact"),
        sa.Column("metadata", sa.Text, server_default="{}"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_index("idx_agent_memory_key", "agent_memory", ["key"])
    op.create_index("idx_agent_memory_category", "agent_memory", ["category"])
    op.create_index("idx_agent_memory_updated", "agent_memory", ["updated_at"])

    # FTS5 全文索引
    op.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS agent_memory_fts USING fts5(
            key, value,
            content=agent_memory,
            content_rowid=rowid,
            tokenize='unicode61'
        )
    """)
    # 自动同步触发器
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS agent_memory_ai AFTER INSERT ON agent_memory BEGIN
            INSERT INTO agent_memory_fts(rowid, key, value)
            VALUES (new.rowid, new.key, new.value);
        END
    """)
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS agent_memory_ad AFTER DELETE ON agent_memory BEGIN
            INSERT INTO agent_memory_fts(agent_memory_fts, rowid, key, value)
            VALUES ('delete', old.rowid, old.key, old.value);
        END
    """)
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS agent_memory_au AFTER UPDATE ON agent_memory BEGIN
            INSERT INTO agent_memory_fts(agent_memory_fts, rowid, key, value)
            VALUES ('delete', old.rowid, old.key, old.value);
            INSERT INTO agent_memory_fts(rowid, key, value)
            VALUES (new.rowid, new.key, new.value);
        END
    """)


def downgrade() -> None:
    """回滚：删触发器 + FTS + 表。"""
    op.execute("DROP TRIGGER IF EXISTS agent_memory_au")
    op.execute("DROP TRIGGER IF EXISTS agent_memory_ad")
    op.execute("DROP TRIGGER IF EXISTS agent_memory_ai")
    op.execute("DROP TABLE IF EXISTS agent_memory_fts")
    op.drop_index("idx_agent_memory_updated", table_name="agent_memory")
    op.drop_index("idx_agent_memory_category", table_name="agent_memory")
    op.drop_index("idx_agent_memory_key", table_name="agent_memory")
    op.drop_table("agent_memory")
