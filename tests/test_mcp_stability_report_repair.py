"""Regression contracts from the 2026-07-15 MCP stability report."""
from __future__ import annotations

from types import SimpleNamespace



def _insert_test_knowledge(*, tags: list[str]) -> str:
    import json
    import uuid
    from datetime import datetime

    from src.services.db import Database

    item_id = str(uuid.uuid4())
    Database.insert_knowledge({
        "id": item_id,
        "title": "MCP stability test",
        "content": "MCP stability test content",
        "source_type": "manual",
        "source_path": "",
        "file_type": "txt",
        "file_size": 0,
        "content_hash": "",
        "file_created_at": "",
        "file_modified_at": "",
        "tags": json.dumps(tags),
        "version": 1,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    })
    return item_id


def test_execute_query_rejects_unimplemented_hybrid_without_advertising_it(monkeypatch):
    from src.mcp.tools import retrieval

    monkeypatch.setattr(retrieval, "_get_container", lambda: SimpleNamespace())

    result = retrieval.execute_query({"filter": {}}, type="hybrid")

    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"
    assert result["error"]["message"] == "不支持的 type: hybrid，仅支持 structured / graph"


def test_graph_traverse_invalid_json_includes_start_ids_example(monkeypatch):
    from src.mcp.tools import graph

    monkeypatch.setattr(graph, "_get_container", lambda: SimpleNamespace())

    result = graph.graph_traverse("not-json")

    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"
    assert '["knowledge-id"]' in result["error"]["message"]


def test_graph_traverse_description_documents_json_ids_and_limit():
    from src.mcp.tool_registry import get_definitions

    description = get_definitions()["graph_traverse"].description

    assert "start_ids" in description
    assert '["knowledge-id"]' in description
    assert "limit" in description


def test_ask_with_query_description_requires_a_question_or_search_query():
    from src.mcp.tool_registry import get_definitions

    description = get_definitions()["ask_with_query"].description

    assert "至少提供 question 或 search_query" in description


def test_tags_supports_limit_and_offset():
    from src.mcp.tools.ingest import tags

    _insert_test_knowledge(tags=["alpha", "beta", "gamma"])

    result = tags(limit=2, offset=1)

    assert result["ok"] is True
    assert result["data"] == ["beta", "gamma"]
    assert result["meta"] == {
        "count": 3,
        "limit": 2,
        "offset": 1,
        "next_offset": None,
        "truncated": False,
    }


def test_tags_without_pagination_keeps_full_list():
    from src.mcp.tools.ingest import tags

    _insert_test_knowledge(tags=["alpha", "beta"])

    result = tags()

    assert result["ok"] is True
    assert result["data"] == ["alpha", "beta"]
    assert result["meta"] == {"count": 2}
