"""Pre-runtime database bootstrap inspection (WP2).

Inspect migration state with read-only SQLite access — never create files
or mutate schema. Runtime open happens only after enforce_bootstrap_plan().
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.storage.migration_status import MigrationStatus, get_migration_status
from src.storage.startup_gate import (
    MigrationGateError,
    resolve_allow_unstamped,
    resolve_gate_enabled,
    resolve_readonly,
    upgrade_hint,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatabaseBootstrapPlan:
    """Result of non-mutating bootstrap inspection."""

    db_path: Path
    exists: bool
    empty: bool
    migration_status: MigrationStatus
    readonly: bool
    write_allowed: bool
    action: str
    reason: str = ""
    skipped: bool = False


def inspect_database_bootstrap(
    db_path: str | Path,
    *,
    config: Any = None,
    project_root: Path | None = None,
) -> DatabaseBootstrapPlan:
    """Inspect DB path and compute gate plan without creating or mutating files.

    Only:
    - checks path existence
    - opens existing DB read-only for sqlite_master / alembic_version
    - computes Gate decision fields
    """
    path = Path(db_path)
    exists = path.is_file()
    status = get_migration_status(path, project_root=project_root)
    empty = (not exists) or status.table_count == 0

    if not resolve_gate_enabled(config):
        ro = resolve_readonly(config)
        return DatabaseBootstrapPlan(
            db_path=path,
            exists=exists,
            empty=empty,
            migration_status=status,
            readonly=ro,
            write_allowed=True,
            action="open_readonly" if ro else "open_runtime",
            reason="migration gate disabled",
            skipped=True,
        )

    ro = resolve_readonly(config)
    allow_unstamped = resolve_allow_unstamped(config)

    # Empty / missing DB: create_container runs Alembic upgrade head (WP3).
    # Inspect must not create the file.
    if empty:
        return DatabaseBootstrapPlan(
            db_path=path,
            exists=exists,
            empty=True,
            migration_status=status,
            readonly=ro,
            write_allowed=not ro,
            action="init_empty" if not ro else "open_readonly",
            reason="empty or missing database — deferred to schema init",
            skipped=False,
        )

    if status.at_head:
        return DatabaseBootstrapPlan(
            db_path=path,
            exists=True,
            empty=False,
            migration_status=status,
            readonly=ro,
            write_allowed=not ro,
            action="open_readonly" if ro else "open_runtime",
            reason="at head",
            skipped=False,
        )

    # Unstamped non-empty
    if status.unstamped:
        if allow_unstamped or ro:
            logger.warning(
                "Migration gate: %s — allowing %s mode (set "
                "storage.migration_gate.allow_unstamped=false to enforce stamp)",
                status.message,
                "readonly" if ro else "write",
            )
            return DatabaseBootstrapPlan(
                db_path=path,
                exists=True,
                empty=False,
                migration_status=status,
                readonly=ro,
                write_allowed=not ro,
                action="open_readonly" if ro else "open_runtime",
                reason="unstamped allowed",
                skipped=False,
            )
        return DatabaseBootstrapPlan(
            db_path=path,
            exists=True,
            empty=False,
            migration_status=status,
            readonly=False,
            write_allowed=False,
            action="block",
            reason="unstamped blocked",
            skipped=False,
        )

    # Stamped but behind head
    if ro:
        logger.warning(
            "Migration gate: schema behind head; starting in readonly diagnostic mode. %s",
            status.message,
        )
        return DatabaseBootstrapPlan(
            db_path=path,
            exists=True,
            empty=False,
            migration_status=status,
            readonly=True,
            write_allowed=False,
            action="open_readonly",
            reason="behind head — readonly diagnostic",
            skipped=False,
        )

    return DatabaseBootstrapPlan(
        db_path=path,
        exists=True,
        empty=False,
        migration_status=status,
        readonly=False,
        write_allowed=False,
        action="block",
        reason="behind head",
        skipped=False,
    )


def enforce_bootstrap_plan(plan: DatabaseBootstrapPlan) -> DatabaseBootstrapPlan:
    """Raise MigrationGateError when write boot is blocked; otherwise return plan."""
    if plan.action == "block":
        raise MigrationGateError(
            plan.migration_status,
            message=upgrade_hint(plan.migration_status),
        )
    return plan
