"""baseline_v1_schema — 捕获 v1.0 全部 schema 作为基线

现有数据库用 `alembic stamp head` 标记为最新，无需执行 DDL。
新数据库通过 `alembic upgrade head` 创建全部表。

Revision ID: c6c24120aff5
Revises:
Create Date: 2026-06-01
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'c6c24120aff5'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 v1.0 全部表结构 — 仅在空数据库上执行"""
    # Knowledge Items
    op.create_table('knowledge_items',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('title', sa.Text, nullable=False),
        sa.Column('content', sa.Text),
        sa.Column('source_type', sa.Text),
        sa.Column('source_path', sa.Text),
        sa.Column('file_type', sa.Text),
        sa.Column('tags', sa.Text),
        sa.Column('version', sa.Integer, server_default='1'),
        sa.Column('file_size', sa.Integer, server_default='0'),
        sa.Column('content_hash', sa.Text, server_default=''),
        sa.Column('quality', sa.Text, server_default=''),
        sa.Column('file_created_at', sa.Text, server_default=''),
        sa.Column('file_modified_at', sa.Text, server_default=''),
        sa.Column('created_at', sa.Text),
        sa.Column('updated_at', sa.Text),
    )

    # Knowledge Versions
    op.create_table('knowledge_versions',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('knowledge_id', sa.Text, sa.ForeignKey('knowledge_items.id', ondelete='CASCADE')),
        sa.Column('version', sa.Integer),
        sa.Column('title', sa.Text),
        sa.Column('content', sa.Text),
        sa.Column('tags', sa.Text),
        sa.Column('created_at', sa.Text),
    )
    op.create_index('idx_versions_kid', 'knowledge_versions', ['knowledge_id', 'version'])

    # Knowledge Chunks
    op.create_table('knowledge_chunks',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('knowledge_id', sa.Text, sa.ForeignKey('knowledge_items.id', ondelete='CASCADE')),
        sa.Column('chunk_index', sa.Integer),
        sa.Column('chunk_text', sa.Text),
        sa.Column('created_at', sa.Text),
    )
    op.create_index('idx_chunks_kid', 'knowledge_chunks', ['knowledge_id'])

    # Conversations
    op.create_table('conversations',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('title', sa.Text),
        sa.Column('created_at', sa.Text),
    )

    # Chat Messages
    op.create_table('chat_messages',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('conversation_id', sa.Text, sa.ForeignKey('conversations.id', ondelete='CASCADE')),
        sa.Column('role', sa.Text),
        sa.Column('content', sa.Text),
        sa.Column('sources', sa.Text),
        sa.Column('created_at', sa.Text),
    )
    op.create_index('idx_msgs_cid', 'chat_messages', ['conversation_id'])

    # Categories
    op.create_table('categories',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('name', sa.Text, nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('parent_id', sa.Text, sa.ForeignKey('categories.id', ondelete='SET NULL')),
        sa.Column('created_at', sa.Text),
    )

    # Knowledge Categories
    op.create_table('knowledge_categories',
        sa.Column('knowledge_id', sa.Text, sa.ForeignKey('knowledge_items.id', ondelete='CASCADE')),
        sa.Column('category_id', sa.Text, sa.ForeignKey('categories.id', ondelete='CASCADE')),
        sa.PrimaryKeyConstraint('knowledge_id', 'category_id'),
    )

    # Wiki Pages
    op.create_table('wiki_pages',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('title', sa.Text, nullable=False),
        sa.Column('content', sa.Text),
        sa.Column('source_ids', sa.Text, server_default='[]'),
        sa.Column('tags', sa.Text, server_default='[]'),
        sa.Column('concept_summary', sa.Text),
        sa.Column('status', sa.Text, server_default='active'),
        sa.Column('lint_score', sa.Float, server_default='1.0'),
        sa.Column('created_at', sa.Text),
        sa.Column('updated_at', sa.Text),
    )

    # Wiki Links
    op.create_table('wiki_links',
        sa.Column('source_page_id', sa.Text, sa.ForeignKey('wiki_pages.id', ondelete='CASCADE')),
        sa.Column('target_page_id', sa.Text, sa.ForeignKey('wiki_pages.id', ondelete='CASCADE')),
        sa.Column('link_type', sa.Text, server_default='related'),
        sa.Column('weight', sa.Float, server_default='1.0'),
        sa.PrimaryKeyConstraint('source_page_id', 'target_page_id'),
    )
    op.create_index('idx_wiki_links_src', 'wiki_links', ['source_page_id'])
    op.create_index('idx_wiki_links_tgt', 'wiki_links', ['target_page_id'])

    # Wiki Ops Log
    op.create_table('wiki_ops_log',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('op_type', sa.Text),
        sa.Column('target_id', sa.Text),
        sa.Column('detail', sa.Text),
        sa.Column('created_at', sa.Text),
    )

    # Wiki Workflow
    op.create_table('wiki_workflow',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('page_id', sa.Text, sa.ForeignKey('wiki_pages.id', ondelete='CASCADE')),
        sa.Column('from_status', sa.Text, nullable=False),
        sa.Column('to_status', sa.Text, nullable=False),
        sa.Column('operator', sa.Text, server_default='system'),
        sa.Column('comment', sa.Text, server_default=''),
        sa.Column('created_at', sa.Text),
    )
    op.create_index('idx_wiki_workflow_page', 'wiki_workflow', ['page_id'])

    # Wiki Page Versions
    op.create_table('wiki_page_versions',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('page_id', sa.Text, sa.ForeignKey('wiki_pages.id', ondelete='CASCADE')),
        sa.Column('version', sa.Integer, nullable=False),
        sa.Column('title', sa.Text, nullable=False),
        sa.Column('content', sa.Text),
        sa.Column('concept_summary', sa.Text),
        sa.Column('tags', sa.Text, server_default='[]'),
        sa.Column('created_at', sa.Text),
    )
    op.create_index('idx_wiki_page_versions_page', 'wiki_page_versions', ['page_id', 'version'])

    # Async Jobs
    op.create_table('async_jobs',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('job_type', sa.Text, nullable=False),
        sa.Column('status', sa.Text, server_default='pending'),
        sa.Column('params', sa.Text, server_default='{}'),
        sa.Column('progress', sa.Integer, server_default='0'),
        sa.Column('progress_message', sa.Text, server_default=''),
        sa.Column('result', sa.Text),
        sa.Column('error_message', sa.Text, server_default=''),
        sa.Column('retry_count', sa.Integer, server_default='0'),
        sa.Column('max_retries', sa.Integer, server_default='3'),
        sa.Column('priority', sa.Integer, server_default='0'),
        sa.Column('created_at', sa.Text),
        sa.Column('started_at', sa.Text),
        sa.Column('completed_at', sa.Text),
    )
    op.create_index('idx_async_jobs_status', 'async_jobs', ['status'])
    op.create_index('idx_async_jobs_type', 'async_jobs', ['job_type'])
    op.create_index('idx_async_jobs_created', 'async_jobs', ['created_at'])

    # Knowledge Graphs
    op.create_table('knowledge_graphs',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('name', sa.Text, nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('source_type', sa.Text, server_default='manual'),
        sa.Column('created_at', sa.Text),
        sa.Column('updated_at', sa.Text),
    )

    # Knowledge Graph Nodes
    op.create_table('knowledge_graph_nodes',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('graph_id', sa.Text, sa.ForeignKey('knowledge_graphs.id', ondelete='CASCADE')),
        sa.Column('knowledge_id', sa.Text, sa.ForeignKey('knowledge_items.id', ondelete='CASCADE')),
        sa.Column('x', sa.Float),
        sa.Column('y', sa.Float),
        sa.Column('is_pinned', sa.Integer, server_default='0'),
        sa.UniqueConstraint('graph_id', 'knowledge_id'),
    )

    # Knowledge Graph Relations
    op.create_table('knowledge_graph_relations',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('graph_id', sa.Text, sa.ForeignKey('knowledge_graphs.id', ondelete='CASCADE')),
        sa.Column('source_knowledge_id', sa.Text, nullable=False),
        sa.Column('target_knowledge_id', sa.Text, nullable=False),
        sa.Column('relation_type', sa.Text, server_default='related'),
        sa.Column('description', sa.Text),
        sa.Column('weight', sa.Float, server_default='1.0'),
    )
    op.create_index('idx_graph_rel_src', 'knowledge_graph_relations', ['graph_id', 'source_knowledge_id'])
    op.create_index('idx_graph_rel_tgt', 'knowledge_graph_relations', ['graph_id', 'target_knowledge_id'])

    # Dedup index
    op.create_index('idx_knowledge_hash', 'knowledge_items', ['content_hash'])

    # FTS tables — 使用原生 SQL（Alembic 对 FTS5 支持有限）
    op.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            title, content, tags, content=knowledge_items, content_rowid=rowid, tokenize='unicode61')
    """)
    op.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
            fts_segmented, knowledge_id UNINDEXED, chunk_id UNINDEXED, tokenize='unicode61')
    """)
    op.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
            title, content, concept_summary, content=wiki_pages, content_rowid=rowid, tokenize='unicode61')
    """)


def downgrade() -> None:
    """回退全部 v1 表"""
    for table in [
        'knowledge_graph_relations', 'knowledge_graph_nodes', 'knowledge_graphs',
        'async_jobs', 'wiki_page_versions', 'wiki_workflow', 'wiki_ops_log',
        'wiki_fts', 'chunk_fts', 'knowledge_fts',
        'wiki_links', 'wiki_pages', 'knowledge_categories', 'categories',
        'chat_messages', 'conversations', 'knowledge_chunks', 'knowledge_versions',
        'knowledge_items',
    ]:
        try:
            op.drop_table(table)
        except Exception:
            pass
