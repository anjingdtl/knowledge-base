"""Routing: mode/tool accuracy vs protocol vs task completion separation."""
from __future__ import annotations

from evals.production_pilot_metrics import score_routing


def test_mode_error_not_fixed_by_callable_downstream() -> None:
    m = score_routing(
        [
            {
                "id": "r1",
                "expected_mode": "graph",
                "expected_tool": "graph_traverse",
                "required_argument_keys": [],
                "expected_task_outcome": "graph_result",
                "got_mode": "hybrid",
                "got_tool": "search",
                "got_arguments": {},
                "protocol_ok": True,
                "timed_out": False,
                "task_outcome": "non_empty",
            }
        ]
    )
    assert m["mode_accuracy"].value == 0.0
    assert m["recommended_tool_accuracy"].value == 0.0
    assert m["protocol_execution_rate"].value == 1.0


def test_validation_error_not_semantic_success() -> None:
    m = score_routing(
        [
            {
                "id": "r2",
                "expected_mode": "structured",
                "expected_tool": "execute_query",
                "required_argument_keys": ["file_type"],
                "expected_task_outcome": "structured_result",
                "got_mode": "structured",
                "got_tool": "execute_query",
                "got_arguments": {},
                "protocol_ok": True,
                "timed_out": False,
                "task_outcome": "validation_error",
            }
        ]
    )
    assert m["argument_contract_accuracy"].value == 0.0
    assert m["task_completion_rate"].value == 0.0


def test_happy_path_routing() -> None:
    m = score_routing(
        [
            {
                "id": "r3",
                "expected_mode": "hybrid",
                "expected_tool": "ask",
                "required_argument_keys": [],
                "expected_task_outcome": "non_empty",
                "got_mode": "hybrid",
                "got_tool": "ask",
                "got_arguments": {"query": "x"},
                "protocol_ok": True,
                "timed_out": False,
                "task_outcome": "non_empty",
            }
        ]
    )
    assert m["mode_accuracy"].value == 1.0
    assert m["recommended_tool_accuracy"].value == 1.0
    assert m["argument_contract_accuracy"].value == 1.0
    assert m["task_completion_rate"].value == 1.0
    assert m["timeout_free_completion_rate"].value == 1.0
