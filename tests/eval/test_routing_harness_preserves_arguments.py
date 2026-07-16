from __future__ import annotations

import pytest

from scripts import production_pilot_mcp_harness as harness


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search", {"query": "agent rewritten query", "top_k": 7}),
        (
            "ask_with_query",
            {"question": "agent question", "search_query": "agent search query", "top_k": 3},
        ),
    ],
)
async def test_agent_arguments_are_executed_byte_for_byte(monkeypatch, tool, arguments) -> None:
    seen = []

    async def fake_call_tool(client, called_tool, called_arguments):
        seen.append((called_tool, called_arguments))
        return {"response_ok": True, "payload": {"data": [{"id": "K1"}]}, "elapsed_ms": 1}

    monkeypatch.setattr(harness, "call_tool", fake_call_tool)
    result = await harness.execute_recommended_route(
        object(), tool=tool, recommended_arguments=arguments
    )

    assert seen == [(tool, arguments)]
    assert result["recommended_arguments_raw"] == arguments
    assert result["executed_arguments"] == arguments
    assert result["arguments_exact_match"] is True
    assert result["argument_contract"]["raw_equals_executed"] is True

