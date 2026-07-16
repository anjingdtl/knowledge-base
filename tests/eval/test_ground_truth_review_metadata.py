from __future__ import annotations

from scripts.freeze_production_pilot_datasets import validate_reviewed_row


def _approved_retrieval() -> dict:
    return {
        "id": "RET-001",
        "query": "企微运营管理办法",
        "expected_ids": ["K1"],
        "acceptable_ids": [],
        "forbidden_ids": ["K2"],
        "annotation_source": "human_reviewed",
        "corpus_snapshot_sha": "kb.db:abc",
        "review": {
            "status": "approved",
            "primary_reviewer": "reviewer_a",
            "primary_reviewed_at": "2026-07-16T08:00:00Z",
            "secondary_reviewer": "reviewer_b",
            "secondary_reviewed_at": "2026-07-16T09:00:00+00:00",
            "adjudicator": "",
            "adjudicated_at": "",
            "decision_notes": "title and body checked",
            "evidence_checked": [
                {
                    "knowledge_id": "K1",
                    "title": "管理办法",
                    "decision": "expected",
                    "reason": "directly answers query",
                    "checked_title": True,
                    "checked_body": True,
                },
                {
                    "knowledge_id": "K2",
                    "title": "无关文档",
                    "decision": "forbidden",
                    "reason": "plausible distractor",
                    "checked_title": True,
                    "checked_body": True,
                },
            ],
        },
    }


def test_approved_retrieval_requires_complete_double_review() -> None:
    assert validate_reviewed_row(_approved_retrieval(), "retrieval", "kb.db:abc") == []


def test_missing_secondary_review_is_rejected() -> None:
    row = _approved_retrieval()
    row["review"]["secondary_reviewer"] = ""
    errors = validate_reviewed_row(row, "retrieval", "kb.db:abc")
    assert "review.secondary_reviewer" in errors


def test_needs_adjudication_cannot_freeze() -> None:
    row = _approved_retrieval()
    row["review"]["status"] = "needs_adjudication"
    errors = validate_reviewed_row(row, "retrieval", "kb.db:abc")
    assert "review.status_not_approved" in errors

