from __future__ import annotations

import pytest

from scripts import production_pilot_mcp_harness as harness


@pytest.mark.asyncio
async def test_search_then_graph_flow_passes_previous_ids(monkeypatch) -> None:
    calls = []

    async def fake_call_tool(client, tool, arguments):
        calls.append((tool, arguments))
        if tool == "search":
            return {
                "response_ok": True,
                "payload": {"data": [{"knowledge_id": "K1"}]},
                "elapsed_ms": 1,
            }
        return {
            "response_ok": True,
            "payload": {"data": {"nodes": [{"id": "K1"}], "edges": []}},
            "elapsed_ms": 1,
        }

    monkeypatch.setattr(harness, "call_tool", fake_call_tool)
    flow = [
        {"tool": "search", "arguments": {"query": "企微", "top_k": 1}},
        {
            "tool": "graph_traverse",
            "arguments": {"max_depth": 2},
            "arguments_from_previous": {
                "start_ids": {"path": "data", "field": "knowledge_id", "as_list": True}
            },
        },
    ]
    result = await harness.execute_recommended_route(
        object(), tool="graph_traverse", recommended_arguments={}, recommended_flow=flow
    )

    assert calls == [
        ("search", {"query": "企微", "top_k": 1}),
        ("graph_traverse", {"max_depth": 2, "start_ids": ["K1"]}),
    ]
    assert result["recommended_flow_executed"] is True
    assert result["task_outcome"] == "graph_result"
    assert result["task_completed"] is True

