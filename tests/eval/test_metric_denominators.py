"""Each metric must report numerator/denominator and exclude inapplicable rows."""
from __future__ import annotations

from evals.production_pilot_metrics import score_retrieval


def test_recall_only_counts_non_empty_expected() -> None:
    rows = [
        {
            "id": "a",
            "expected_ids": ["1"],
            "acceptable_ids": [],
            "forbidden_ids": [],
            "got_ids": ["1", "2"],
        },
        {
            "id": "b",
            "expected_ids": [],
            "acceptable_ids": [],
            "forbidden_ids": [],
            "got_ids": ["9"],
            "response_ok": True,
        },
        {
            "id": "c",
            "expected_ids": ["3"],
            "acceptable_ids": [],
            "forbidden_ids": [],
            "got_ids": ["4"],
        },
    ]
    m = score_retrieval(rows)
    assert m["recall_at_5"].numerator == 1
    assert m["recall_at_5"].denominator == 2
    assert m["excluded_empty_expected"] == 1


def test_forbidden_hit_rate_denominator() -> None:
    rows = [
        {
            "id": "a",
            "expected_ids": ["1"],
            "acceptable_ids": [],
            "forbidden_ids": ["x"],
            "got_ids": ["x", "1"],
        },
        {
            "id": "b",
            "expected_ids": ["2"],
            "acceptable_ids": [],
            "forbidden_ids": ["y"],
            "got_ids": ["2"],
        },
    ]
    m = score_retrieval(rows)
    assert m["forbidden_hit_rate_at_5"].numerator == 1
    assert m["forbidden_hit_rate_at_5"].denominator == 2
