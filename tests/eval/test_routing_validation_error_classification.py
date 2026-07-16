from __future__ import annotations

import pytest

from scripts import production_pilot_mcp_harness as harness


def test_validation_payload_is_not_success() -> None:
    assert harness.classify_task_outcome(
        "execute_query", {"error_code": "VALIDATION_ERROR", "data": {}}
    ) == "validation_error"


@pytest.mark.asyncio
async def test_transport_error_is_distinct_and_incomplete(monkeypatch) -> None:
    async def fake_call_tool(client, tool, arguments):
        return {
            "response_ok": False,
            "payload": None,
            "error_code": "TRANSPORT_ERROR",
            "elapsed_ms": 2,
        }

    monkeypatch.setattr(harness, "call_tool", fake_call_tool)
    result = await harness.execute_recommended_route(
        object(), tool="search", recommended_arguments={"query": "q"}
    )
    assert result["protocol_ok"] is False
    assert result["task_outcome"] == "transport_error"
    assert result["task_completed"] is False

