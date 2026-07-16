from __future__ import annotations

from scripts.build_production_pilot_datasets import build_routing


def test_rule_generated_rows_are_pending_candidates_not_human() -> None:
    rows = build_routing()
    assert rows
    assert {row["annotation_source"] for row in rows} == {"rule_assisted_candidate"}
    assert {row["human_review_status"] for row in rows} == {"pending"}
    assert all("candidate_expected_mode" in row for row in rows)
    assert all("expected_mode" not in row for row in rows)

