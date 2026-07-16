from __future__ import annotations

import pytest

from scripts import production_pilot_mcp_harness as harness


@pytest.mark.asyncio
async def test_raw_execution_record_is_preserved(monkeypatch) -> None:
    raw = {
        "response_ok": True,
        "payload": {"data": {"answer": "grounded", "answer_mode": "answer"}},
        "elapsed_ms": 9,
    }

    async def fake_call_tool(client, tool, arguments):
        return raw

    monkeypatch.setattr(harness, "call_tool", fake_call_tool)
    result = await harness.execute_recommended_route(
        object(), tool="ask", recommended_arguments={"question": "q"}
    )
    assert result["raw_exec_response"] == raw
    assert result["exec_elapsed_ms"] == 9
    assert result["task_outcome"] == "non_empty"

