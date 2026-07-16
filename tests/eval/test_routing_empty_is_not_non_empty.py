from __future__ import annotations

import pytest

from scripts.production_pilot_mcp_harness import classify_task_outcome


@pytest.mark.parametrize(
    ("tool", "payload"),
    [
        ("search", {"data": []}),
        ("ask", {"data": {"answer": "", "answer_mode": "answer"}}),
        ("graph_traverse", {"data": {"nodes": [], "edges": []}}),
        ("execute_query", {"data": {"rows": []}}),
    ],
)
def test_empty_payloads_are_classified_honestly(tool, payload) -> None:
    assert classify_task_outcome(tool, payload) == "empty"


def test_non_empty_search_is_non_empty() -> None:
    assert classify_task_outcome("search", {"data": [{"id": "K1"}]}) == "non_empty"

