"""Storage / migration startup concerns (WP2+)."""
from src.storage.alembic_runner import (
    AlembicUpgradeError,
    AlembicUpgradeResult,
    stamp_to_revision,
    upgrade_to_head,
)
from src.storage.database_bootstrap import (
    DatabaseBootstrapPlan,
    enforce_bootstrap_plan,
    inspect_database_bootstrap,
)
from src.storage.legacy_schema_detector import LegacySchemaMatch, detect_legacy_schema
from src.storage.migration_status import MigrationStatus, get_migration_status
from src.storage.startup_gate import MigrationGateError, enforce_startup_gate

__all__ = [
    "AlembicUpgradeError",
    "AlembicUpgradeResult",
    "DatabaseBootstrapPlan",
    "LegacySchemaMatch",
    "MigrationGateError",
    "MigrationStatus",
    "detect_legacy_schema",
    "enforce_bootstrap_plan",
    "enforce_startup_gate",
    "get_migration_status",
    "inspect_database_bootstrap",
    "stamp_to_revision",
    "upgrade_to_head",
]
