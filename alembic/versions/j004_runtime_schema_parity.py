"""Add runtime schema objects missing from Alembic head (WP3 parity).

Closes gap between Database._SCHEMA and Alembic so empty-db init via
alembic upgrade head includes block_fts, tag DAG, property schemas, and
operation_logs required by production write paths.

Revision ID: j004_runtime_schema_parity
Revises: j003_maintenance_control_plane
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision = "j004_runtime_schema_parity"
down_revision: Union[str, None] = "j003_maintenance_control_plane"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Block full-text index (used by create/update/search write paths)
    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS block_fts USING fts5(
            fts_segmented,
            page_id UNINDEXED,
            block_id UNINDEXED,
            tokenize='unicode61'
        )
        """
    )

    op.create_table(
        "tag_relations",
        sa.Column("parent_tag", sa.Text, nullable=False),
        sa.Column("child_tag", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text),
        sa.PrimaryKeyConstraint("parent_tag", "child_tag"),
        sa.CheckConstraint("parent_tag <> child_tag"),
        if_not_exists=True,
    )
    op.create_index(
        "idx_tag_relations_parent",
        "tag_relations",
        ["parent_tag"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_tag_relations_child",
        "tag_relations",
        ["child_tag"],
        if_not_exists=True,
    )

    op.create_table(
        "property_schemas",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("scope_type", sa.Text, nullable=False),
        sa.Column("scope_id", sa.Text, server_default=""),
        sa.Column("property_name", sa.Text, nullable=False),
        sa.Column("property_type", sa.Text, nullable=False),
        sa.Column("required", sa.Integer, server_default="0"),
        sa.Column("default_value", sa.Text),
        sa.Column("choices", sa.Text),
        sa.Column("constraints", sa.Text),
        sa.Column("created_at", sa.Text),
        sa.UniqueConstraint("scope_type", "scope_id", "property_name"),
        if_not_exists=True,
    )
    op.create_index(
        "idx_property_schemas_scope",
        "property_schemas",
        ["scope_type", "scope_id"],
        if_not_exists=True,
    )

    op.create_table(
        "effective_property_index",
        sa.Column(
            "block_id",
            sa.Text,
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("prop_key", sa.Text, primary_key=True),
        sa.Column("prop_value", sa.Text),
        sa.Column("value_type", sa.Text, server_default="string"),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, server_default=""),
        sa.Column("inherited", sa.Integer, server_default="0"),
        sa.Column("updated_at", sa.Text),
        if_not_exists=True,
    )
    op.create_index(
        "idx_effective_prop_key_val",
        "effective_property_index",
        ["prop_key", "prop_value"],
        if_not_exists=True,
    )

    op.create_table(
        "operation_logs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("operation", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text, nullable=False),
        sa.Column("target_id", sa.Text, nullable=False),
        sa.Column("operator", sa.Text, nullable=False, server_default="system"),
        sa.Column("source", sa.Text, nullable=False, server_default="mcp"),
        sa.Column("snapshot_before", sa.Text),
        sa.Column("snapshot_after", sa.Text),
        sa.Column("metadata", sa.Text, server_default="{}"),
        sa.Column("created_at", sa.Text, nullable=False),
        if_not_exists=True,
    )
    op.create_index(
        "idx_oplog_target",
        "operation_logs",
        ["target_type", "target_id"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_oplog_time",
        "operation_logs",
        ["created_at"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_oplog_operation",
        "operation_logs",
        ["operation"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_oplog_operation", table_name="operation_logs")
    op.drop_index("idx_oplog_time", table_name="operation_logs")
    op.drop_index("idx_oplog_target", table_name="operation_logs")
    op.drop_table("operation_logs")
    op.drop_index("idx_effective_prop_key_val", table_name="effective_property_index")
    op.drop_table("effective_property_index")
    op.drop_index("idx_property_schemas_scope", table_name="property_schemas")
    op.drop_table("property_schemas")
    op.drop_index("idx_tag_relations_child", table_name="tag_relations")
    op.drop_index("idx_tag_relations_parent", table_name="tag_relations")
    op.drop_table("tag_relations")
    op.execute("DROP TABLE IF EXISTS block_fts")
