from __future__ import annotations

from scripts.freeze_production_pilot_datasets import validate_reviewed_row


def test_answer_fact_requires_matching_block_and_quote() -> None:
    row = {
        "id": "ANS-001",
        "question": "报销期限是什么？",
        "annotation_source": "human_reviewed",
        "corpus_snapshot_sha": "kb.db:abc",
        "expected_answer_facts": [
            {
                "fact_id": "F1",
                "statement": "应在十个工作日内提交。",
                "supporting_knowledge_ids": ["K1"],
                "supporting_block_ids": ["B1"],
                "supporting_quotes": [
                    {
                        "knowledge_id": "K1",
                        "block_id": "B1",
                        "quote": "十个工作日内提交报销申请",
                        "reason": "direct clause",
                    }
                ],
            }
        ],
        "review": {
            "status": "approved",
            "primary_reviewer": "reviewer_a",
            "primary_reviewed_at": "2026-07-16T08:00:00Z",
            "secondary_reviewer": "reviewer_b",
            "secondary_reviewed_at": "2026-07-16T09:00:00Z",
            "evidence_checked": [
                {
                    "knowledge_id": "K1",
                    "title": "报销办法",
                    "decision": "expected",
                    "reason": "contains the clause",
                    "checked_title": True,
                    "checked_body": True,
                }
            ],
        },
    }
    assert validate_reviewed_row(row, "answer_citations", "kb.db:abc") == []

    row["expected_answer_facts"][0]["supporting_quotes"][0]["block_id"] = "OTHER"
    assert "expected_answer_facts[0].supporting_quote_block_mismatch" in validate_reviewed_row(
        row, "answer_citations", "kb.db:abc"
    )

