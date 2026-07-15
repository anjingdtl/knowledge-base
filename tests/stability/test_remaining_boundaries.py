"""Phase 0/7 — 其余边界：tags offset、extract_tasks、未知参数。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.stability.conftest import insert_knowledge


def test_tags_rejects_negative_offset(patch_container):
    insert_knowledge(title="t1", tags=["a", "b"])
    from src.mcp.tools.ingest import tags

    result = tags(limit=10, offset=-1)
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_tags_limit_none_or_positive_ok(patch_container):
    insert_knowledge(title="t2", tags=["x"])
    from src.mcp.tools.ingest import tags

    full = tags()
    assert full["ok"] is True
    paged = tags(limit=1, offset=0)
    assert paged["ok"] is True
    assert len(paged["data"]) <= 1


def test_extract_tasks_empty_content_validation(monkeypatch):
    from src.mcp.tools import memory

    monkeypatch.setattr(memory, "_check_write_policy", lambda *_a, **_k: None)
    monkeypatch.setattr(
        memory,
        "_get_container",
        lambda: SimpleNamespace(
            agent_memory=SimpleNamespace(
                extract_tasks_from_doc=lambda content: {"total_found": 0, "stored": 0, "tasks": []}
            )
        ),
    )

    result = memory.extract_tasks_from_doc(content="")
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"

    result2 = memory.extract_tasks_from_doc(content="   ")
    assert result2["ok"] is False
    assert result2["error"]["code"] == "VALIDATION_ERROR"


def test_extract_tasks_doc_id_empty_validation(monkeypatch):
    from src.mcp.tools import memory

    monkeypatch.setattr(memory, "_check_write_policy", lambda *_a, **_k: None)
    result = memory.extract_tasks_from_doc(doc_id="")
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_extract_tasks_both_or_neither(monkeypatch):
    from src.mcp.tools import memory

    monkeypatch.setattr(memory, "_check_write_policy", lambda *_a, **_k: None)
    missing = memory.extract_tasks_from_doc()
    both = memory.extract_tasks_from_doc(content="x", doc_id="y")
    assert missing["ok"] is False and missing["error"]["code"] == "VALIDATION_ERROR"
    assert both["ok"] is False and both["error"]["code"] == "VALIDATION_ERROR"
