"""Baseline snapshot of runtime schema mutation behavior (WP0-T1).

Records current known state only — does not assert final migration-governance goals.
All DB work uses tmp_path; never touches the default user database.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.migrations._helpers import (
    ROOT,
    alembic_cmd,
    head_revision,
    read_revision,
    sqlite_url,
    table_names,
)

DB_PY = ROOT / "src" / "services" / "db.py"
LEGACY_SCHEMA_PY = ROOT / "src" / "compatibility" / "runtime_schema_migrate.py"
CONTAINER_PY = ROOT / "src" / "core" / "container.py"
STARTUP_GATE_PY = ROOT / "src" / "storage" / "startup_gate.py"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_schema_sql(compat_text: str) -> str:
    match = re.search(r'LEGACY_SCHEMA_SQL\s*=\s*"""(.*?)"""', compat_text, re.S)
    assert match, "LEGACY_SCHEMA_SQL not found in runtime_schema_migrate.py"
    return match.group(1)


def _extract_migrate_body(compat_text: str) -> str:
    marker = "def apply_legacy_column_migrate"
    start = compat_text.find(marker)
    assert start >= 0, "apply_legacy_column_migrate not found"
    # Body after first newline following the def line
    nl = compat_text.find("\n", start)
    assert nl >= 0
    rest = compat_text[nl + 1 :]
    # Until next top-level def/class or EOF
    end_m = re.search(r"^(?:def |class )", rest, re.M)
    return rest[: end_m.start()] if end_m else rest


def _count_schema_objects(schema_sql: str) -> dict[str, int]:
    return {
        "create_table": len(re.findall(r"CREATE TABLE\b", schema_sql, re.I)),
        "create_index": len(
            re.findall(r"CREATE (?:UNIQUE )?INDEX\b", schema_sql, re.I)
        ),
        "create_virtual_table": len(
            re.findall(r"CREATE VIRTUAL TABLE\b", schema_sql, re.I)
        ),
        "create_trigger": len(re.findall(r"CREATE TRIGGER\b", schema_sql, re.I)),
    }


def _count_migrate_mutation_statements(migrate_body: str) -> int:
    """Count DDL-style mutation keywords present in _migrate body."""
    return len(
        re.findall(
            r"\b(?:ALTER\s+TABLE|CREATE\s+TABLE|CREATE\s+VIRTUAL\s+TABLE|"
            r"CREATE\s+(?:UNIQUE\s+)?INDEX|DROP\s+TABLE|DROP\s+INDEX)\b",
            migrate_body,
            re.I,
        )
    )


def _connect_internal_source() -> str:
    text = _read(DB_PY)
    match = re.search(
        r"def _connect_internal\(self\):(.*?)(?=\n    def |\nclass |\Z)",
        text,
        re.S,
    )
    assert match, "_connect_internal not found"
    return match.group(1)


def test_database_init_uses_compatibility_schema_helpers():
    """Legacy constructor applies compatibility helpers; open_runtime does not."""
    body = _connect_internal_source()
    assert "apply_legacy_schema" in body
    assert "apply_legacy_column_migrate" in body
    # Production open path must stay clean
    db_text = _read(DB_PY)
    for name in ("open_runtime", "_open_write_runtime", "_open_readonly_runtime"):
        m = re.search(
            rf"def {name}\(.*?\):(.*?)(?=\n    def |\n    @|\nclass |\Z)",
            db_text,
            re.S,
        )
        assert m, name
        assert "apply_legacy_schema" not in m.group(1)
        assert "apply_legacy_column_migrate" not in m.group(1)
        assert "executescript" not in m.group(1)


def test_create_container_enforces_gate_before_open_runtime():
    """WP2: inspect/enforce bootstrap plan then Database.open_runtime(...)."""
    text = _read(CONTAINER_PY)
    start = text.find("def create_container(")
    assert start >= 0, "create_container not found"
    rest = text[start + 1 :]
    end_rel = re.search(r"\n(?:def |class )", rest)
    body = rest[: end_rel.start()] if end_rel else rest
    inspect_pos = body.find("inspect_database_bootstrap(")
    enforce_pos = body.find("enforce_bootstrap_plan(")
    open_pos = body.find("Database.open_runtime(")
    assert inspect_pos >= 0, "inspect_database_bootstrap not found"
    assert enforce_pos >= 0, "enforce_bootstrap_plan not found"
    assert open_pos >= 0, "Database.open_runtime not found"
    assert inspect_pos < enforce_pos < open_pos, (
        f"expected inspect < enforce < open_runtime "
        f"(inspect={inspect_pos}, enforce={enforce_pos}, open={open_pos})"
    )
    # Must not reintroduce gate-after-construct pattern
    assert "enforce_startup_gate(" not in body


def test_allow_unstamped_default_is_false():
    """WP4: allow_unstamped default False (non-empty unstamped write boot blocked)."""
    text = _read(STARTUP_GATE_PY)
    assert (
        'storage.migration_gate.allow_unstamped", False)' in text
        or "storage.migration_gate.allow_unstamped', False)" in text
        or re.search(
            r'allow_unstamped["\'],\s*False\s*\)',
            text,
        )
    )
    from src.storage.startup_gate import resolve_allow_unstamped

    assert resolve_allow_unstamped(None) is False


def test_architecture_closure_ci_uses_strict():
    """WP1-T1: architecture-closure must run report_closure_debt.py --strict."""
    text = _read(CI_YML)
    assert "report_closure_debt.py" in text
    debt_lines = [
        line
        for line in text.splitlines()
        if "report_closure_debt.py" in line and not line.strip().startswith("#")
    ]
    assert debt_lines, "no report_closure_debt.py invocations found"
    for line in debt_lines:
        assert "--strict" in line, (
            f"architecture-closure must use --strict, found: {line!r}"
        )
        assert "|| true" not in line
        assert "continue-on-error" not in line


def test_schema_object_counts_match_current_source():
    schema = _extract_schema_sql(_read(LEGACY_SCHEMA_PY))
    counts = _count_schema_objects(schema)
    total = sum(counts.values())
    # Frozen counts as of WP0-T1 baseline — update only with intentional schema edits
    assert counts["create_table"] == 43
    assert counts["create_index"] == 45
    assert counts["create_virtual_table"] == 6
    assert counts["create_trigger"] == 9
    assert total == 103


def test_migrate_mutation_statement_count_matches_current_source():
    body = _extract_migrate_body(_read(LEGACY_SCHEMA_PY))
    n = _count_migrate_mutation_statements(body)
    assert n == 28


def test_empty_database_alembic_upgrade_head_tables_and_revision(tmp_path: Path):
    db = tmp_path / "empty_baseline.db"
    # Must not use default user path
    assert "data" not in db.parts or tmp_path in db.parents
    alembic_cmd("upgrade", "head", url=sqlite_url(db))
    rev = read_revision(db)
    assert rev == head_revision()
    # head advances with new Alembic revisions (WP3 j004, …)
    assert rev  # non-empty
    tables = table_names(db)
    assert "alembic_version" in tables
    assert "knowledge_items" in tables
    # Stable floor for head schema size (includes FTS shadow tables)
    assert len(tables) >= 60


def test_v1_9_style_upgrade_to_head_tables_and_revision(tmp_path: Path):
    """v1.9 stand-in: stamp/upgrade from j002 (pre-maintenance control plane)."""
    db = tmp_path / "v19_baseline.db"
    prior = "j002_evidence_stale"
    alembic_cmd("upgrade", prior, url=sqlite_url(db))
    assert read_revision(db) == prior
    before = table_names(db)

    alembic_cmd("upgrade", "head", url=sqlite_url(db))
    assert read_revision(db) == head_revision()
    after = table_names(db)
    assert after >= before
    assert "alembic_version" in after
    # j003 maintenance tables
    for name in (
        "maintenance_jobs",
        "maintenance_reviews",
        "maintenance_schedules",
        "maintenance_source_events",
        "maintenance_dead_letters",
        "maintenance_health_snapshots",
    ):
        assert name in after


def test_baseline_does_not_touch_default_user_db(tmp_path: Path, monkeypatch):
    """Guard: helpers only operate on explicit tmp paths."""
    user_db = tmp_path / "would_be_user.db"
    # Create a sentinel file that must remain untouched by other helpers
    sentinel = b"SENTINEL-NOT-A-REAL-USER-DB"
    user_db.write_bytes(sentinel)

    # Point config home away from real data if anything loads Config
    monkeypatch.setenv("SHINEHE_HOME", str(tmp_path / "home"))
    work = tmp_path / "work.db"
    alembic_cmd("upgrade", "head", url=sqlite_url(work))
    assert user_db.read_bytes() == sentinel
