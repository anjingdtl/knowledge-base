from __future__ import annotations

import pytest

from scripts import production_pilot_mcp_harness as harness


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"error_code": "PROVIDER_TIMEOUT"},
        {"data": {"route": {"mode": "timeout"}}},
        {"data": {"answer_mode": "timeout"}},
        {"meta": {"timeout": True}},
        {"warnings": ["provider timeout after 1s"]},
    ],
)
async def test_timeout_flags_are_not_task_completion(monkeypatch, payload) -> None:
    async def fake_call_tool(client, tool, arguments):
        return {"response_ok": True, "payload": payload, "elapsed_ms": 1}

    monkeypatch.setattr(harness, "call_tool", fake_call_tool)
    result = await harness.execute_recommended_route(
        object(), tool="ask", recommended_arguments={"question": "q"}
    )
    assert result["timed_out"] is True
    assert result["task_outcome"] == "timeout"
    assert result["task_completed"] is False

