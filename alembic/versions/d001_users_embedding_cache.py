"""add users and embedding cache

Revision ID: d001_users_embedding_cache
Revises: b001_block_model
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa

revision = "d001_users_embedding_cache"
down_revision = "b001_block_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "embedding_cache",
        sa.Column("content_hash", sa.Text, nullable=False, primary_key=True),
        sa.Column("model", sa.Text, nullable=False, primary_key=True),
        sa.Column("embedding", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("username", sa.Text, primary_key=True),
        sa.Column("hashed", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("embedding_cache")
