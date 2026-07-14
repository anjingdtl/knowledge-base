"""Read Alembic revision state for a SQLite database path."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MigrationStatus:
    """Snapshot of migration position relative to Alembic head."""

    db_path: str
    head: str
    current: str | None
    at_head: bool
    unstamped: bool
    db_exists: bool
    table_count: int
    message: str

    @property
    def behind(self) -> bool:
        if self.unstamped:
            return self.table_count > 0
        return not self.at_head


def get_alembic_heads(project_root: Path | None = None) -> list[str]:
    """Return head revision ids from the project alembic scripts."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = project_root or Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    return list(script.get_heads())


def get_alembic_head(project_root: Path | None = None) -> str:
    heads = get_alembic_heads(project_root)
    if not heads:
        raise RuntimeError("No Alembic head revisions found under alembic/versions")
    if len(heads) > 1:
        # Prefer sorted stability; multi-head is a packaging bug
        heads = sorted(heads)
    return heads[0]


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def _open_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite file read-only (never creates or mutates)."""
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def get_current_revision(db_path: str | Path) -> str | None:
    """Return alembic_version.version_num or None if unstamped/missing."""
    path = Path(db_path)
    if not path.is_file():
        return None
    conn = _open_readonly(path)
    try:
        tables = _table_names(conn)
        if "alembic_version" not in tables:
            return None
        row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
        if not row:
            return None
        return str(row[0])
    finally:
        conn.close()


def get_migration_status(
    db_path: str | Path,
    *,
    project_root: Path | None = None,
    head: str | None = None,
) -> MigrationStatus:
    """Compare DB current revision to Alembic head.

    Existing files are opened read-only so inspection never creates WAL or
    mutates schema.
    """
    path = Path(db_path)
    head_rev = head or get_alembic_head(project_root)
    if not path.is_file():
        return MigrationStatus(
            db_path=str(path),
            head=head_rev,
            current=None,
            at_head=False,
            unstamped=True,
            db_exists=False,
            table_count=0,
            message="database file does not exist yet",
        )

    conn = _open_readonly(path)
    try:
        tables = _table_names(conn)
        table_count = len(tables)
        current: str | None = None
        if "alembic_version" in tables:
            row = conn.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
            if row:
                current = str(row[0])
    finally:
        conn.close()

    if current is None:
        unstamped = True
        at_head = False
        if table_count == 0:
            msg = "empty database (no alembic stamp)"
        else:
            msg = (
                "database has schema tables but no alembic_version "
                f"(unstamped; head={head_rev})"
            )
    else:
        unstamped = False
        at_head = current == head_rev
        if at_head:
            msg = f"at head ({head_rev})"
        else:
            msg = f"behind head: current={current} head={head_rev}"

    return MigrationStatus(
        db_path=str(path),
        head=head_rev,
        current=current,
        at_head=at_head,
        unstamped=unstamped,
        db_exists=True,
        table_count=table_count,
        message=msg,
    )


def upgrade_hint(status: MigrationStatus) -> str:
    """User-facing upgrade instructions."""
    return (
        f"Database migration required: {status.message}\n"
        f"  DB: {status.db_path}\n"
        "  Fix: run from project root:\n"
        "    alembic upgrade head\n"
        "  Or set storage.readonly=true / SHINEHE_READONLY=1 for diagnostics only.\n"
        "  Emergency skip (not recommended): SHINEHE_SKIP_MIGRATION_GATE=1"
    )
