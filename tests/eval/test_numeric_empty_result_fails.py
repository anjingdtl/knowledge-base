"""Numeric unit empty results: fail for expected hits, pass for expected no-answer."""
from __future__ import annotations

from evals.production_pilot_metrics import score_numeric_units


def test_expected_hit_empty_fails() -> None:
    m = score_numeric_units(
        [
            {
                "id": "1",
                "expected_no_answer": False,
                "expected_ids": ["a"],
                "expected_units": ["米"],
                "forbidden_units": ["珠/米"],
                "got_ids": [],
                "got_top_texts": [],
            }
        ]
    )
    assert m["top1_unit_accuracy"].value == 0.0
    assert m["top3_expected_document_recall"].value == 0.0


def test_expected_no_answer_empty_passes() -> None:
    m = score_numeric_units(
        [
            {
                "id": "2",
                "expected_no_answer": True,
                "expected_ids": [],
                "expected_units": [],
                "forbidden_units": [],
                "got_ids": [],
                "got_top_texts": [],
                "search_no_match": True,
            }
        ]
    )
    assert m["numeric_no_answer_accuracy"].denominator == 1
    assert m["numeric_no_answer_accuracy"].value == 1.0


def test_forbidden_unit_confusion() -> None:
    m = score_numeric_units(
        [
            {
                "id": "3",
                "expected_no_answer": False,
                "expected_ids": ["meters_doc"],
                "expected_units": ["米"],
                "forbidden_units": ["珠/米"],
                "got_ids": ["beads_doc"],
                "got_top_texts": ["规格 60珠/米 灯带"],
            }
        ]
    )
    assert m["forbidden_unit_confusion_rate"].value == 1.0
