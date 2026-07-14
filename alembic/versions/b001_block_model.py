"""add block model

Revision ID: b001_block_model
Revises: c6c24120aff5
Create Date: 2026-06-01
"""
import sqlalchemy as sa

from alembic import op

revision = "b001_block_model"
down_revision = "c6c24120aff5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blocks",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("parent_id", sa.Text, sa.ForeignKey("blocks.id", ondelete="CASCADE"), nullable=True),
        sa.Column("page_id", sa.Text, nullable=True),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("block_type", sa.Text, server_default="text"),
        sa.Column("properties", sa.Text, server_default="{}"),
        sa.Column("order_idx", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.Text, nullable=True),
        sa.Column("updated_at", sa.Text, nullable=True),
    )
    op.create_index("idx_blocks_page", "blocks", ["page_id"])
    op.create_index("idx_blocks_parent", "blocks", ["parent_id"])

    op.create_table(
        "block_refs",
        sa.Column("source_id", sa.Text, sa.ForeignKey("blocks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("target_id", sa.Text, sa.ForeignKey("blocks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("ref_type", sa.Text, server_default="link", primary_key=True),
    )

    op.create_table(
        "entity_refs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text, nullable=False),
        sa.Column("target_id", sa.Text, nullable=False),
        sa.Column("ref_type", sa.Text, server_default="mention"),
        sa.Column("weight", sa.Float, server_default="1.0"),
        sa.Column("created_at", sa.Text, nullable=True),
        sa.UniqueConstraint("source_type", "source_id", "target_type", "target_id", "ref_type"),
    )
    op.create_index("idx_entity_refs_source", "entity_refs", ["source_type", "source_id"])
    op.create_index("idx_entity_refs_target", "entity_refs", ["target_type", "target_id"])

    op.create_table(
        "block_property_index",
        sa.Column("block_id", sa.Text, sa.ForeignKey("blocks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("prop_key", sa.Text, primary_key=True),
        sa.Column("prop_value", sa.Text, nullable=True),
        sa.Column("value_type", sa.Text, server_default="string"),
    )
    op.create_index("idx_prop_key_val", "block_property_index", ["prop_key", "prop_value"])


def downgrade() -> None:
    op.drop_index("idx_prop_key_val", table_name="block_property_index")
    op.drop_table("block_property_index")
    op.drop_index("idx_entity_refs_target", table_name="entity_refs")
    op.drop_index("idx_entity_refs_source", table_name="entity_refs")
    op.drop_table("entity_refs")
    op.drop_table("block_refs")
    op.drop_index("idx_blocks_parent", table_name="blocks")
    op.drop_index("idx_blocks_page", table_name="blocks")
    op.drop_table("blocks")
