from __future__ import annotations

from scripts.freeze_production_pilot_datasets import validate_reviewed_row
from tests.eval.test_ground_truth_review_metadata import _approved_retrieval


def test_reviewed_snapshot_must_match_freeze_snapshot() -> None:
    row = _approved_retrieval()
    errors = validate_reviewed_row(row, "retrieval", "kb.db:different")
    assert "corpus_snapshot_sha_mismatch" in errors

