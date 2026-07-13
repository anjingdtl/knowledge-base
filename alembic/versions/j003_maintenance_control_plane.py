"""add maintenance control-plane tables

Revision ID: j003_maintenance_control_plane
Revises: j002_evidence_stale
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision = "j003_maintenance_control_plane"
down_revision: Union[str, None] = "j002_evidence_stale"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for name, columns in [
        ("maintenance_source_events", [sa.Column("event_id", sa.Text, primary_key=True), sa.Column("idempotency_key", sa.Text, nullable=False, unique=True), sa.Column("event_type", sa.Text, nullable=False), sa.Column("knowledge_id", sa.Text, nullable=False), sa.Column("source_revision", sa.Text, nullable=False), sa.Column("source_path", sa.Text, nullable=False, server_default=""), sa.Column("correlation_id", sa.Text, nullable=False, server_default=""), sa.Column("created_at", sa.Text, nullable=False), sa.Column("payload_json", sa.Text, nullable=False, server_default="{}")]),
        ("maintenance_jobs", [sa.Column("job_id", sa.Text, primary_key=True), sa.Column("idempotency_key", sa.Text, unique=True), sa.Column("status", sa.Text, nullable=False), sa.Column("risk_level", sa.Text, nullable=False), sa.Column("created_at", sa.Text, nullable=False), sa.Column("lease_until", sa.Text), sa.Column("due_at", sa.Text), sa.Column("payload_json", sa.Text, nullable=False)]),
        ("maintenance_reviews", [sa.Column("review_id", sa.Text, primary_key=True), sa.Column("status", sa.Text, nullable=False), sa.Column("risk_level", sa.Text, nullable=False), sa.Column("job_id", sa.Text, nullable=False, server_default=""), sa.Column("created_at", sa.Text, nullable=False), sa.Column("payload_json", sa.Text, nullable=False)]),
        ("maintenance_dead_letters", [sa.Column("job_id", sa.Text, primary_key=True), sa.Column("failed_at", sa.Text, nullable=False), sa.Column("last_error", sa.Text, nullable=False, server_default=""), sa.Column("payload_json", sa.Text, nullable=False)]),
        ("maintenance_health_snapshots", [sa.Column("snapshot_id", sa.Text, primary_key=True), sa.Column("captured_at", sa.Text, nullable=False), sa.Column("payload_json", sa.Text, nullable=False)]),
        ("maintenance_schedules", [sa.Column("schedule_name", sa.Text, primary_key=True), sa.Column("next_run_at", sa.Text), sa.Column("lease_until", sa.Text), sa.Column("payload_json", sa.Text, nullable=False, server_default="{}")]),
    ]:
        op.create_table(name, *columns, if_not_exists=True)
    op.create_index("idx_maintenance_jobs_status_due", "maintenance_jobs", ["status", "due_at", "created_at"], if_not_exists=True)
    op.create_index("idx_maintenance_reviews_status", "maintenance_reviews", ["status", "created_at"], if_not_exists=True)


def downgrade() -> None:
    # Control-plane history is audit data; downgrade deliberately retains it.
    pass
