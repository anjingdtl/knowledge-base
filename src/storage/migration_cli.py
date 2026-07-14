"""Safe legacy DB migration workflow (WP4): status/backup/migrate/stamp/verify."""
from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.storage.alembic_runner import (
    stamp_to_revision,
    upgrade_to_head,
)
from src.storage.legacy_schema_detector import (
    detect_legacy_schema,
    version_to_stamp_revision,
)
from src.storage.migration_status import (
    get_alembic_head,
    get_current_revision,
    get_migration_status,
)
from src.storage.startup_gate import resolve_allow_unstamped, resolve_readonly

logger = logging.getLogger(__name__)

_CRITICAL_TABLES = (
    "knowledge_items",
    "blocks",
    "wiki_pages",
    "async_jobs",
)


class MigrationWorkflowError(RuntimeError):
    """Safe migration workflow failure."""


@dataclass
class RowCounts:
    tables: dict[str, int] = field(default_factory=dict)

    def get(self, table: str) -> int:
        return int(self.tables.get(table, 0))


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        return sqlite3.connect(uri, uri=True)
    return sqlite3.connect(str(path))


def integrity_check(db_path: str | Path) -> tuple[bool, str]:
    path = Path(db_path)
    conn = _connect(path, readonly=True)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        ok = bool(row and str(row[0]).lower() == "ok")
        return ok, str(row[0]) if row else "no result"
    finally:
        conn.close()


def foreign_key_check(db_path: str | Path) -> list[tuple]:
    path = Path(db_path)
    conn = _connect(path, readonly=True)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        return list(conn.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        conn.close()


def count_business_rows(db_path: str | Path) -> RowCounts:
    path = Path(db_path)
    conn = _connect(path, readonly=True)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        counts: dict[str, int] = {}
        for t in _CRITICAL_TABLES:
            if t in tables:
                counts[t] = int(
                    conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                )
            else:
                counts[t] = 0
        return RowCounts(tables=counts)
    finally:
        conn.close()


def schema_fingerprint(db_path: str | Path) -> dict[str, Any]:
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    tool = root / "tools" / "schema_fingerprint.py"
    spec = importlib.util.spec_from_file_location("schema_fingerprint", tool)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.compute_schema_fingerprint(db_path)
    return dict(result)


def backup_database(db_path: str | Path) -> Path:
    """Create ``<db>.backup-YYYYMMDD-HHMMSS.sqlite`` via SQLite Backup API."""
    path = Path(db_path)
    if not path.is_file():
        raise MigrationWorkflowError(f"database not found: {path}")
    dest = path.with_name(f"{path.name}.backup-{_now_stamp()}.sqlite")
    src = sqlite3.connect(str(path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    ok, msg = integrity_check(dest)
    if not ok:
        dest.unlink(missing_ok=True)
        raise MigrationWorkflowError(f"backup integrity_check failed: {msg}")
    logger.info("Backup created: %s", dest)
    return dest


def restore_from_backup(
    db_path: str | Path,
    backup_path: str | Path,
    *,
    failed_keep: bool = True,
) -> Path | None:
    """Replace db with backup. Optionally keep failed DB as .failed-migration-*.sqlite."""
    path = Path(db_path)
    backup = Path(backup_path)
    if not backup.is_file():
        raise MigrationWorkflowError(f"backup not found: {backup}")
    failed_copy: Path | None = None
    if path.is_file() and failed_keep:
        failed_copy = path.with_name(f"{path.name}.failed-migration-{_now_stamp()}.sqlite")
        shutil.copy2(path, failed_copy)
    # Close is caller's responsibility; replace file bytes via copy
    shutil.copy2(backup, path)
    ok, msg = integrity_check(path)
    if not ok:
        raise MigrationWorkflowError(
            f"restore integrity_check failed: {msg}; failed_copy={failed_copy}"
        )
    return failed_copy


def db_status(db_path: str | Path, *, config: Any = None) -> dict[str, Any]:
    path = Path(db_path)
    status = get_migration_status(path)
    match = detect_legacy_schema(path) if path.is_file() else None
    ro = resolve_readonly(config)
    allow_u = resolve_allow_unstamped(config)
    write_allowed = True
    recommend = "ok"
    if not path.is_file():
        recommend = "run create_container / alembic upgrade head (empty init)"
        write_allowed = not ro
    elif status.unstamped and status.table_count > 0:
        write_allowed = bool(allow_u) and not ro
        if match and match.confidence == "high":
            recommend = (
                f"write blocked by default; run: shinehe db migrate "
                f"(detected {match.matched_version} → stamp {match.stamp_revision})"
            )
        else:
            recommend = (
                "write blocked; unknown unstamped schema — manual review required "
                "(do not stamp head)"
            )
    elif not status.at_head:
        write_allowed = ro is True  # only diagnostic
        recommend = "behind head: alembic upgrade head or start readonly"
    else:
        recommend = "at head"

    return {
        "db_path": str(path.resolve()) if path.exists() else str(path),
        "revision": status.current,
        "head": status.head,
        "unstamped": status.unstamped and status.table_count > 0,
        "at_head": status.at_head,
        "table_count": status.table_count,
        "legacy_version": match.matched_version if match else None,
        "legacy_confidence": match.confidence if match else None,
        "stamp_revision": match.stamp_revision if match else None,
        "write_allowed": write_allowed and not ro,
        "readonly_config": ro,
        "allow_unstamped": allow_u,
        "recommend": recommend,
        "message": status.message,
    }


def verify_database(
    db_path: str | Path,
    *,
    before_counts: RowCounts | None = None,
    expect_at_head: bool = True,
) -> dict[str, Any]:
    """Integrity + row-count + head checks after migration."""
    path = Path(db_path)
    ok_int, int_msg = integrity_check(path)
    fk_violations = foreign_key_check(path)
    counts = count_business_rows(path)
    rev = get_current_revision(path)
    head = get_alembic_head()
    tables_ok = True
    missing: list[str] = []
    conn = _connect(path, readonly=True)
    try:
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for t in _CRITICAL_TABLES:
            if t not in existing:
                # async_jobs may be named differently historically — soft
                if t == "async_jobs" and "async_jobs" not in existing:
                    continue
                if t in ("knowledge_items", "blocks", "wiki_pages"):
                    tables_ok = False
                    missing.append(t)
    finally:
        conn.close()

    row_ok = True
    row_issues: list[str] = []
    if before_counts is not None:
        for t, n in before_counts.tables.items():
            after = counts.get(t)
            if after < n:
                row_ok = False
                row_issues.append(f"{t}: {n} → {after}")

    at_head = rev == head
    fp = schema_fingerprint(path)
    # Compare to a temp head reference is expensive; check revision + critical tables
    passed = (
        ok_int
        and not fk_violations
        and tables_ok
        and row_ok
        and (at_head if expect_at_head else True)
    )
    return {
        "passed": passed,
        "integrity": int_msg,
        "foreign_key_violations": len(fk_violations),
        "revision": rev,
        "head": head,
        "at_head": at_head,
        "row_counts": counts.tables,
        "missing_tables": missing,
        "row_issues": row_issues,
        "fingerprint_revision": fp.get("revision"),
    }


def stamp_database(
    db_path: str | Path,
    *,
    from_version: str,
    force: bool = False,
) -> dict[str, Any]:
    """Stamp only when detector matches the requested version (never free-form head)."""
    if from_version.strip().lower() == "head":
        raise MigrationWorkflowError(
            "refusing 'shinehe db stamp head' — specify --from-version with a known legacy id"
        )
    path = Path(db_path)
    match = detect_legacy_schema(path)
    if match.confidence != "high" or not match.stamp_revision:
        raise MigrationWorkflowError(
            f"refusing stamp: detector confidence={match.confidence} "
            f"version={match.matched_version} reasons={match.reasons}"
        )
    wanted = version_to_stamp_revision(from_version)
    if wanted is None:
        raise MigrationWorkflowError(f"unknown --from-version: {from_version}")
    if wanted != match.stamp_revision and not force:
        raise MigrationWorkflowError(
            f"version mismatch: requested stamp {wanted} but detector wants "
            f"{match.stamp_revision} for {match.matched_version}"
        )
    rev = stamp_to_revision(path, match.stamp_revision)
    return {
        "db_path": str(path.resolve()),
        "stamped_revision": rev,
        "matched_version": match.matched_version,
    }


def migrate_database(db_path: str | Path) -> dict[str, Any]:
    """Backup → detect → stamp if needed → upgrade head → verify; restore on failure."""
    path = Path(db_path)
    if not path.is_file():
        raise MigrationWorkflowError(f"database not found: {path}")

    status = get_migration_status(path)
    before_counts = count_business_rows(path)
    backup = backup_database(path)
    log_lines: list[str] = [f"backup={backup}"]

    try:
        if status.unstamped and status.table_count > 0:
            match = detect_legacy_schema(path)
            log_lines.append(
                f"detect version={match.matched_version} "
                f"confidence={match.confidence} stamp={match.stamp_revision}"
            )
            if match.confidence != "high" or not match.stamp_revision:
                raise MigrationWorkflowError(
                    "refusing auto-migrate for unknown/low-confidence unstamped schema: "
                    + "; ".join(match.reasons)
                )
            stamp_to_revision(path, match.stamp_revision)
            log_lines.append(f"stamped {match.stamp_revision}")

        up = upgrade_to_head(path)
        log_lines.append(
            f"upgrade before={up.before_revision} after={up.after_revision} "
            f"upgraded={up.upgraded}"
        )
        verification = verify_database(path, before_counts=before_counts)
        if not verification["passed"]:
            raise MigrationWorkflowError(
                f"post-migration verification failed: {verification}"
            )
        return {
            "ok": True,
            "backup": str(backup),
            "log": log_lines,
            "verification": verification,
        }
    except Exception as exc:
        logger.exception("Migration failed; restoring backup")
        log_lines.append(f"error={exc}")
        try:
            failed = restore_from_backup(path, backup, failed_keep=True)
            log_lines.append(f"restored_from={backup} failed_copy={failed}")
        except Exception as restore_exc:
            log_lines.append(f"restore_failed={restore_exc}")
            raise MigrationWorkflowError(
                f"migration failed ({exc}); restore also failed ({restore_exc}); "
                f"backup at {backup}"
            ) from restore_exc
        raise MigrationWorkflowError(
            f"migration failed and backup restored: {exc}; log={log_lines}"
        ) from exc
