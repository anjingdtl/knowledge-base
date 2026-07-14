"""Write-mode startup gate — refuse service boot when schema is behind head."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.storage.migration_status import (
    MigrationStatus,
    get_migration_status,
    upgrade_hint,
)

logger = logging.getLogger(__name__)


class MigrationGateError(RuntimeError):
    """Raised when write-mode startup is blocked by migration state."""

    def __init__(self, status: MigrationStatus, message: str | None = None):
        self.status = status
        text = message or upgrade_hint(status)
        super().__init__(text)


@dataclass(frozen=True)
class GateDecision:
    """Outcome of enforce_startup_gate."""

    status: MigrationStatus
    readonly: bool
    write_allowed: bool
    skipped: bool
    reason: str


def _env_truthy(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _cfg_bool(config: Any, key: str, default: bool) -> bool:
    if config is None:
        return default
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    val = getter(key, default)
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def resolve_readonly(config: Any = None) -> bool:
    if _env_truthy("SHINEHE_READONLY"):
        return True
    return _cfg_bool(config, "storage.readonly", False)


def resolve_gate_enabled(config: Any = None) -> bool:
    """Gate is on by default; auto-off under pytest unless explicitly enforced."""
    if _env_truthy("SHINEHE_SKIP_MIGRATION_GATE"):
        return False
    if _env_truthy("SHINEHE_ENFORCE_MIGRATION_GATE"):
        return True
    # Avoid breaking unit tests that use Database._migrate without Alembic stamp
    if os.environ.get("PYTEST_CURRENT_TEST") and not _env_truthy(
        "SHINEHE_ENFORCE_MIGRATION_GATE"
    ):
        return False
    return _cfg_bool(config, "storage.migration_gate.enabled", True)


def resolve_allow_unstamped(config: Any = None) -> bool:
    """Transition default True so legacy _migrate()-only DBs can still boot with warn."""
    return _cfg_bool(config, "storage.migration_gate.allow_unstamped", True)


def resolve_auto_upgrade_empty(config: Any = None) -> bool:
    """WP3: auto alembic upgrade head for missing/empty DB only (default True)."""
    return _cfg_bool(config, "storage.migration_gate.auto_upgrade_empty", True)


def enforce_startup_gate(
    db_path: str | Path,
    *,
    config: Any = None,
    project_root: Path | None = None,
    readonly: bool | None = None,
) -> GateDecision:
    """Check migration head before allowing write services.

    - skipped: gate disabled
    - readonly: start allowed, write_allowed=False when behind/unstamped-strict
    - write mode + behind head → MigrationGateError
    - unstamped with tables: warn; fail only if allow_unstamped=False and not readonly
    """
    status = get_migration_status(db_path, project_root=project_root)
    if not resolve_gate_enabled(config):
        return GateDecision(
            status=status,
            readonly=bool(readonly if readonly is not None else resolve_readonly(config)),
            write_allowed=True,
            skipped=True,
            reason="migration gate disabled",
        )

    ro = resolve_readonly(config) if readonly is None else readonly
    allow_unstamped = resolve_allow_unstamped(config)

    # Empty / missing DB: Database layer will create schema; do not block boot.
    if not status.db_exists or status.table_count == 0:
        return GateDecision(
            status=status,
            readonly=ro,
            write_allowed=not ro,
            skipped=False,
            reason="empty or missing database — deferred to schema init",
        )

    if status.at_head:
        return GateDecision(
            status=status,
            readonly=ro,
            write_allowed=not ro,
            skipped=False,
            reason="at head",
        )

    # Unstamped legacy DB
    if status.unstamped:
        if allow_unstamped or ro:
            logger.warning(
                "Migration gate: %s — allowing %s mode (set "
                "storage.migration_gate.allow_unstamped=false to enforce stamp)",
                status.message,
                "readonly" if ro else "write",
            )
            return GateDecision(
                status=status,
                readonly=ro,
                write_allowed=not ro,
                skipped=False,
                reason="unstamped allowed",
            )
        raise MigrationGateError(status)

    # Stamped but behind head
    if ro:
        logger.warning(
            "Migration gate: schema behind head; starting in readonly diagnostic mode. %s",
            status.message,
        )
        return GateDecision(
            status=status,
            readonly=True,
            write_allowed=False,
            skipped=False,
            reason="behind head — readonly diagnostic",
        )

    raise MigrationGateError(status)
