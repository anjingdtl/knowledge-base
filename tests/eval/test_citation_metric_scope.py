"""Citation metrics only apply to real non-empty answers with human facts."""
from __future__ import annotations

from evals.production_pilot_metrics import score_answer_citations


def test_citation_scores_supported_fact() -> None:
    rows = [
        {
            "id": "A1",
            "answer_mode": "answer",
            "answer": "本制度规范企业微信运营管理。",
            "sources": [{"knowledge_id": "d1"}],
            "expected_answer_facts": [
                {
                    "fact_id": "F1",
                    "statement": "企业微信运营管理",
                    "supporting_knowledge_ids": ["d1"],
                }
            ],
            "forbidden_claims": [],
        }
    ]
    m = score_answer_citations(rows)
    assert m["answer_completion_rate"].value == 1.0
    assert m["citation_completeness"].value == 1.0
    assert m["citation_correctness"].value == 1.0
    assert m["source_id_validity"].value == 1.0


def test_unsupported_claim_rate() -> None:
    rows = [
        {
            "id": "A2",
            "answer_mode": "answer",
            "answer": "今日营收是 999 亿。企业微信运营。",
            "sources": [{"knowledge_id": "d1"}],
            "expected_answer_facts": [
                {
                    "fact_id": "F1",
                    "statement": "企业微信运营",
                    "supporting_knowledge_ids": ["d1"],
                }
            ],
            "forbidden_claims": ["今日营收是 999 亿"],
            "unsupported_claims_detected": ["今日营收是 999 亿"],
        }
    ]
    m = score_answer_citations(rows)
    assert m["unsupported_claim_rate"].denominator >= 1
    assert m["unsupported_claim_rate"].value == 1.0
