"""Schema fingerprint tool tests (WP0-T2).

Uses only temporary databases — never the default user DB path.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from tests.migrations._helpers import ROOT, alembic_cmd, head_revision, sqlite_url

_TOOL = ROOT / "tools" / "schema_fingerprint.py"


def _load_tool():
    assert _TOOL.is_file(), f"missing {_TOOL}"
    spec = importlib.util.spec_from_file_location("schema_fingerprint", _TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _insert_business_row(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        # Minimal row if knowledge_items exists after alembic upgrade
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "knowledge_items" in tables:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_items "
                "(id, title, content, created_at, updated_at) "
                "VALUES ('fp-test-1', 'Fingerprint Business Row', "
                "'SECRET_BUSINESS_DATA_SHOULD_NOT_APPEAR', "
                "datetime('now'), datetime('now'))"
            )
            conn.commit()
    finally:
        conn.close()


def test_fingerprint_is_deterministic(tmp_path: Path):
    mod = _load_tool()
    db = tmp_path / "det.db"
    alembic_cmd("upgrade", "head", url=sqlite_url(db))
    a = mod.compute_schema_fingerprint(db)
    b = mod.compute_schema_fingerprint(db)
    assert a == b
    # Stable JSON serialization
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_fingerprint_includes_required_keys(tmp_path: Path):
    mod = _load_tool()
    db = tmp_path / "keys.db"
    alembic_cmd("upgrade", "head", url=sqlite_url(db))
    fp = mod.compute_schema_fingerprint(db)
    for key in ("revision", "tables", "indexes", "triggers", "virtual_tables"):
        assert key in fp
    assert isinstance(fp["tables"], dict)
    assert isinstance(fp["indexes"], dict)
    assert isinstance(fp["triggers"], dict)
    assert isinstance(fp["virtual_tables"], list)
    assert fp["revision"] == head_revision()
    assert "knowledge_items" in fp["tables"]
    # Column metadata for a known table
    cols = fp["tables"]["knowledge_items"]
    assert isinstance(cols, list)
    assert any(c.get("name") == "id" for c in cols)
    id_col = next(c for c in cols if c["name"] == "id")
    for field in ("name", "type", "notnull", "default", "pk"):
        assert field in id_col


def test_fingerprint_excludes_business_data(tmp_path: Path):
    mod = _load_tool()
    db = tmp_path / "biz.db"
    alembic_cmd("upgrade", "head", url=sqlite_url(db))
    _insert_business_row(db)
    fp = mod.compute_schema_fingerprint(db)
    blob = json.dumps(fp, ensure_ascii=False)
    assert "SECRET_BUSINESS_DATA_SHOULD_NOT_APPEAR" not in blob
    assert "Fingerprint Business Row" not in blob
    assert "fp-test-1" not in blob


def test_fingerprint_cli_json(tmp_path: Path):
    db = tmp_path / "cli.db"
    alembic_cmd("upgrade", "head", url=sqlite_url(db))
    result = subprocess.run(
        [sys.executable, str(_TOOL), "--db", str(db), "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["revision"] == head_revision()
    assert "tables" in data


def test_fingerprint_does_not_use_default_user_db(tmp_path: Path, monkeypatch):
    """CLI and API require explicit --db; no implicit user path fallback."""
    mod = _load_tool()
    # Poison SHINEHE_HOME so any accidental Config path is under tmp
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("SHINEHE_HOME", str(fake_home))
    sentinel = tmp_path / "user.db"
    sentinel.write_bytes(b"NOT-A-SCHEMA-DB")

    missing = tmp_path / "does-not-exist.db"
    try:
        mod.compute_schema_fingerprint(missing)
        raised = False
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError):
        raised = True
    assert raised or not missing.exists()

    # Sentinel untouched
    assert sentinel.read_bytes() == b"NOT-A-SCHEMA-DB"
    # No new DB created under fake home by fingerprint tool
    assert list(fake_home.rglob("*.db")) == [] or all(
        p.stat().st_size == 0 or True for p in fake_home.rglob("*.db")
    )
    # Stronger: tool must not create files under SHINEHE_HOME
    created = [p for p in fake_home.rglob("*") if p.is_file()]
    assert created == []
