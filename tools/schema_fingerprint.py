#!/usr/bin/env python3
"""Compute a stable schema-only fingerprint of a SQLite database.

Includes tables (columns + constraints), indexes, triggers, virtual tables,
and alembic_version. Excludes all business data rows.

Usage:
  python tools/schema_fingerprint.py --db path/to/db.sqlite --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    # URI readonly — never create or write
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _read_revision(conn: sqlite3.Connection) -> str | None:
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "alembic_version" not in tables:
        return None
    row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    return str(row[0]) if row else None


def _table_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cols: list[dict[str, Any]] = []
    for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
        # cid, name, type, notnull, dflt_value, pk
        cols.append(
            {
                "name": row[1],
                "type": row[2] or "",
                "notnull": bool(row[3]),
                "default": row[4],
                "pk": int(row[5] or 0),
            }
        )
    return cols


def compute_schema_fingerprint(db_path: str | Path) -> dict[str, Any]:
    """Return a deterministic schema fingerprint dict (no business rows)."""
    path = Path(db_path)
    conn = _open_readonly(path)
    try:
        master = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        ).fetchall()

        tables: dict[str, list[dict[str, Any]]] = {}
        indexes: dict[str, dict[str, Any]] = {}
        triggers: dict[str, dict[str, Any]] = {}
        virtual_tables: list[str] = []

        for type_, name, tbl_name, sql in master:
            if not name:
                continue
            sql_text = sql or ""
            if type_ == "table":
                # Detect virtual/FTS tables via CREATE VIRTUAL TABLE
                if "virtual table" in sql_text.lower():
                    virtual_tables.append(name)
                tables[name] = _table_columns(conn, name)
            elif type_ == "index":
                # Skip autoindexes that appear in master with null sql sometimes
                indexes[name] = {
                    "table": tbl_name,
                    "sql": sql_text,
                    "columns": [
                        r[2]
                        for r in conn.execute(
                            f'PRAGMA index_info("{name}")'
                        ).fetchall()
                        if r[2] is not None
                    ],
                }
            elif type_ == "trigger":
                triggers[name] = {
                    "table": tbl_name,
                    "sql": sql_text,
                }

        virtual_tables = sorted(set(virtual_tables))

        return {
            "revision": _read_revision(conn),
            "tables": {k: tables[k] for k in sorted(tables)},
            "indexes": {k: indexes[k] for k in sorted(indexes)},
            "triggers": {k: triggers[k] for k in sorted(triggers)},
            "virtual_tables": virtual_tables,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Schema-only fingerprint of a SQLite database"
    )
    parser.add_argument(
        "--db",
        type=Path,
        required=True,
        help="Path to SQLite database (required; no default user path)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (default behavior; kept for Spec CLI contract)",
    )
    args = parser.parse_args(argv)

    try:
        fp = compute_schema_fingerprint(args.db)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"sqlite error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(fp, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
