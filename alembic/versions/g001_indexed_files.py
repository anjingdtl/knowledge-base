"""indexed_files — 文件索引追踪表

记录每个文件的指纹（size/mtime/sha256）和索引状态，
用于增量索引和文件变更检测。

Revision ID: g001
Revises: c6c24120aff5
Create Date: 2026-06-13
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'g001'
down_revision: Union[str, Sequence[str], None] = 'c6c24120aff5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('indexed_files',
        sa.Column('path', sa.Text, primary_key=True),
        sa.Column('knowledge_id', sa.Text),
        sa.Column('size', sa.Integer, nullable=False),
        sa.Column('mtime_ns', sa.Integer, nullable=False),
        sa.Column('sha256', sa.Text, nullable=False),
        sa.Column('status', sa.Text, nullable=False, server_default='pending'),
        sa.Column('last_indexed_at', sa.Text),
        sa.Column('last_error', sa.Text),
    )
    op.create_index('idx_indexed_files_status', 'indexed_files', ['status'])
    op.create_index('idx_indexed_files_knowledge', 'indexed_files', ['knowledge_id'])


def downgrade() -> None:
    try:
        op.drop_index('idx_indexed_files_knowledge', table_name='indexed_files')
    except Exception:
        pass
    try:
        op.drop_index('idx_indexed_files_status', table_name='indexed_files')
    except Exception:
        pass
    try:
        op.drop_table('indexed_files')
    except Exception:
        pass
