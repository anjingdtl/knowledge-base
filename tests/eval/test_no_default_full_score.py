"""RED tests: empty expected / empty results / timeout must never auto-score 1.0."""
from __future__ import annotations

from evals.production_pilot_metrics import (
    MetricValue,
    score_answer_citations,
    score_no_answer,
    score_numeric_units,
    score_retrieval,
    score_routing,
)


def test_empty_expected_ids_excluded_from_retrieval_not_full_score() -> None:
    rows = [
        {
            "id": "X1",
            "expected_ids": [],
            "acceptable_ids": [],
            "forbidden_ids": [],
            "got_ids": ["a", "b"],
            "response_ok": True,
        }
    ]
    m = score_retrieval(rows)
    assert m["recall_at_5"].denominator == 0
    assert m["recall_at_5"].value is None
    assert m["mrr_at_10"].denominator == 0
    assert m["ndcg_at_10"].denominator == 0


def test_empty_results_with_expected_ids_score_zero() -> None:
    rows = [
        {
            "id": "X2",
            "expected_ids": ["doc1"],
            "acceptable_ids": [],
            "forbidden_ids": [],
            "got_ids": [],
            "response_ok": True,
        }
    ]
    m = score_retrieval(rows)
    assert m["recall_at_5"].denominator == 1
    assert m["recall_at_5"].value == 0.0
    assert m["mrr_at_10"].value == 0.0
    assert m["ndcg_at_10"].value == 0.0


def test_no_answer_false_answer_not_full() -> None:
    rows = [
        {
            "id": "N1",
            "expected_no_answer": True,
            "search_no_match": False,
            "ask_answer_mode": "answer",
            "answer": "随便编一个答案",
            "sources": [{"knowledge_id": "x"}],
        }
    ]
    m = score_no_answer(rows)
    assert m["no_answer_accuracy"].value == 0.0
    assert m["false_answer_rate"].value == 1.0


def test_citation_no_answer_excluded() -> None:
    rows = [
        {
            "id": "A0",
            "answer_mode": "no_answer",
            "answer": "",
            "sources": [],
            "expected_answer_facts": [],
        }
    ]
    m = score_answer_citations(rows)
    assert m["citation_completeness"].denominator == 0
    assert m["citation_correctness"].denominator == 0


def test_numeric_empty_result_fails_when_expected_hit() -> None:
    rows = [
        {
            "id": "NUM",
            "expected_no_answer": False,
            "expected_ids": ["d1"],
            "expected_units": ["米"],
            "forbidden_units": ["珠/米"],
            "got_ids": [],
            "got_top_texts": [],
            "response_ok": True,
        }
    ]
    m = score_numeric_units(rows)
    assert m["top1_unit_accuracy"].denominator == 1
    assert m["top1_unit_accuracy"].value == 0.0
    assert m["top3_expected_document_recall"].value == 0.0


def test_routing_timeout_not_task_complete() -> None:
    rows = [
        {
            "id": "R1",
            "expected_mode": "hybrid",
            "expected_tool": "ask",
            "required_argument_keys": [],
            "expected_task_outcome": "non_empty",
            "got_mode": "hybrid",
            "got_tool": "ask",
            "got_arguments": {},
            "protocol_ok": True,
            "timed_out": True,
            "task_outcome": "timeout",
        }
    ]
    m = score_routing(rows)
    assert m["protocol_execution_rate"].value == 1.0
    assert m["task_completion_rate"].value == 0.0
    assert m["timeout_free_completion_rate"].value == 0.0


def test_metric_value_exposes_numerator_denominator() -> None:
    mv = MetricValue(numerator=3, denominator=4)
    assert mv.value == 0.75
    assert mv.as_dict()["numerator"] == 3
    assert mv.as_dict()["denominator"] == 4
