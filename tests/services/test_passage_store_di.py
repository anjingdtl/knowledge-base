"""PassageStore DI + public Database facade (Phase 0 Task 0.2).

Guarantees:
- Injected db is preferred over any global singleton.
- Class facade path uses public Database.get_conn only.
- Source must not read Database._instance (closure debt gate).
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from src.services.passage_store import PassageStore

ROOT = Path(__file__).resolve().parents[2]
PASSAGE_STORE_SRC = ROOT / "src" / "services" / "passage_store.py"


def test_passage_store_source_does_not_touch_database_instance():
    text = PASSAGE_STORE_SRC.read_text(encoding="utf-8")
    # Match the same debt-gate regex used by tools/report_closure_debt.py
    assert not re.search(r"Database\._instance", text), (
        "PassageStore must not access the private Database singleton field; "
        "use injected db or public Database.get_conn()"
    )


def test_passage_store_prefers_injected_db_over_global():
    mock_conn = MagicMock(name="injected_conn")
    mock_db = MagicMock(name="injected_db")
    mock_db.get_conn.return_value = mock_conn

    store = PassageStore(db=mock_db)
    assert store._get_conn() is mock_conn
    mock_db.get_conn.assert_called_once_with()


def test_passage_store_class_facade_uses_public_get_conn(monkeypatch):
    """Compatibility path: PassageStore() without inject uses Database.get_conn()."""
    sentinel = object()
    calls: list[str] = []

    class FakeDatabase:
        @classmethod
        def get_conn(cls):
            calls.append("get_conn")
            return sentinel

    monkeypatch.setattr(
        "src.services.db.Database",
        FakeDatabase,
        raising=True,
    )
    # Also patch the late import target used inside _get_conn.
    import src.services.db as db_mod

    monkeypatch.setattr(db_mod, "Database", FakeDatabase)

    store = PassageStore()
    store._db = None  # force facade path
    conn = store._get_conn()
    assert conn is sentinel
    assert calls == ["get_conn"]


def test_passage_store_accepts_raw_connection_like_object():
    """Injected object without get_conn is used as the connection itself."""

    class RawConn:
        pass

    raw = RawConn()
    store = PassageStore(db=raw)
    assert store._get_conn() is raw
