"""Detect known unstamped legacy SQLite schemas (WP4).

Only high-confidence matches of supported historical shapes are returned.
Unknown or hand-modified structures must not be auto-stamped.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LegacySchemaMatch:
    matched_version: str | None
    confidence: str  # high | medium | low | none
    fingerprint: dict[str, Any]
    reasons: tuple[str, ...]
    stamp_revision: str | None = None


# Signature table sets for supported unstamped profiles.
# stamp_revision is the Alembic revision to stamp before upgrade head.
_CORE_REQUIRED = frozenset({
    "knowledge_items",
    "knowledge_chunks",
    "blocks",
    "wiki_pages",
})

_V19_REQUIRED = _CORE_REQUIRED | frozenset({
    "chunk_fts",
    "knowledge_fts",
    "wiki_fts",
    "block_property_index",
    "block_refs",
})

# Maintenance control-plane (j003) — present in recent _SCHEMA unstamped DBs
_MAINTENANCE = frozenset({
    "maintenance_jobs",
    "maintenance_reviews",
    "maintenance_schedules",
    "maintenance_source_events",
})

# j004 parity objects
_J004_EXTRAS = frozenset({
    "block_fts",
    "tag_relations",
    "property_schemas",
    "effective_property_index",
    "operation_logs",
})


def _open_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except sqlite3.Error:
        return set()


def _fingerprint(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = sorted(_table_names(conn))
    columns: dict[str, list[str]] = {}
    for t in tables:
        if t.endswith("_data") or t.endswith("_idx") or t.endswith("_docsize") or t.endswith("_config") or t.endswith("_content"):
            continue  # FTS shadow noise
        cols = sorted(_column_names(conn, t))
        if cols:
            columns[t] = cols
    return {
        "tables": tables,
        "columns": columns,
        "table_count": len(tables),
    }


def detect_legacy_schema(db_path: str | Path) -> LegacySchemaMatch:
    """Return a high-confidence match only for explicitly supported shapes."""
    path = Path(db_path)
    if not path.is_file():
        return LegacySchemaMatch(
            matched_version=None,
            confidence="none",
            fingerprint={},
            reasons=("database file does not exist",),
            stamp_revision=None,
        )

    conn = _open_readonly(path)
    try:
        fp = _fingerprint(conn)
        tables = set(fp["tables"])
        reasons: list[str] = []

        if "alembic_version" in tables:
            return LegacySchemaMatch(
                matched_version=None,
                confidence="none",
                fingerprint=fp,
                reasons=("database already has alembic_version (stamped)",),
                stamp_revision=None,
            )

        missing_core = sorted(_CORE_REQUIRED - tables)
        if missing_core:
            return LegacySchemaMatch(
                matched_version=None,
                confidence="none",
                fingerprint=fp,
                reasons=(f"missing core tables: {', '.join(missing_core)}",),
                stamp_revision=None,
            )

        # knowledge_items must have id + title (sanity against totally wrong schema)
        ki_cols = _column_names(conn, "knowledge_items")
        if not {"id", "title"}.issubset(ki_cols):
            return LegacySchemaMatch(
                matched_version=None,
                confidence="none",
                fingerprint=fp,
                reasons=("knowledge_items missing required columns id/title",),
                stamp_revision=None,
            )

        missing_v19 = sorted(_V19_REQUIRED - tables)
        if missing_v19:
            return LegacySchemaMatch(
                matched_version=None,
                confidence="low",
                fingerprint=fp,
                reasons=(
                    "partial legacy shape; missing v1.9 markers: "
                    + ", ".join(missing_v19),
                ),
                stamp_revision=None,
            )

        has_maint = bool(_MAINTENANCE & tables)
        has_j004 = bool(_J004_EXTRAS & tables)
        # Hand-modified: has unexpected empty shell only
        reasons.append("has core v1.9 table set")

        if has_j004 and has_maint:
            # Full runtime _SCHEMA dump without stamp — stamp at head revision
            from src.storage.migration_status import get_alembic_head

            head = get_alembic_head()
            reasons.append("has maintenance + j004 parity tables")
            return LegacySchemaMatch(
                matched_version="v1.10.x-unstamped-full",
                confidence="high",
                fingerprint=fp,
                reasons=tuple(reasons),
                stamp_revision=head,
            )

        if has_maint and not has_j004:
            reasons.append("has maintenance tables (j003 era)")
            return LegacySchemaMatch(
                matched_version="v1.9.x+maintenance",
                confidence="high",
                fingerprint=fp,
                reasons=tuple(reasons),
                stamp_revision="j003_maintenance_control_plane",
            )

        # Classic v1.9 without maintenance control plane
        reasons.append("no maintenance/j004 extras — treat as pre-j003 v1.9")
        return LegacySchemaMatch(
            matched_version="v1.9.x",
            confidence="high",
            fingerprint=fp,
            reasons=tuple(reasons),
            stamp_revision="j002_evidence_stale",
        )
    finally:
        conn.close()


def version_to_stamp_revision(version: str) -> str | None:
    """Map user-facing --from-version to Alembic revision id."""
    v = version.strip().lower()
    mapping = {
        "v1.9": "j002_evidence_stale",
        "v1.9.0": "j002_evidence_stale",
        "v1.9.x": "j002_evidence_stale",
        "v1.9.x+maintenance": "j003_maintenance_control_plane",
        "v1.10": None,  # resolved dynamically to head
        "v1.10.x": None,
        "v1.10.x-unstamped-full": None,
        "j002_evidence_stale": "j002_evidence_stale",
        "j003_maintenance_control_plane": "j003_maintenance_control_plane",
    }
    if v in mapping:
        rev = mapping[v]
        if rev is None:
            from src.storage.migration_status import get_alembic_head

            return get_alembic_head()
        return rev
    return None
