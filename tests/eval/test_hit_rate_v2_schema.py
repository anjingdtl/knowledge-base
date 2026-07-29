"""Golden V2 schema validation tests."""

from __future__ import annotations

from evals.hit_rate_v2.validation import validate_case_schema


def _valid_case(**overrides):
    row = {
        "case_id": "KB-001",
        "schema_version": "2.0",
        "split": "development",
        "category": "精确关键词",
        "risk_level": "P2",
        "query": "测试查询",
        "answerability": "answerable",
        "intent": "fact",
        "expected_action": "answer",
        "expected_sources": [
            {
                "knowledge_id": "k1",
                "passage_id": None,
                "passage_missing_reason": "pending",
                "source_role": "primary",
            }
        ],
        "required_fact_groups": [
            {
                "fact_id": "KB-001-F01",
                "object_text": "事实",
                "match_policy": "normalized",
                "required": True,
            }
        ],
        "forbidden_assertions": [],
        "acceptable_variants": [],
        "ambiguity": {"status": "clear", "reason": ""},
        "corpus_snapshot": {},
        "annotation_source": "candidate",
        "review": {},
    }
    row.update(overrides)
    return row


def test_valid_candidate_passes_schema():
    assert validate_case_schema(_valid_case()) == []


def test_missing_passage_without_reason_fails():
    row = _valid_case(
        expected_sources=[{"knowledge_id": "k1", "source_role": "primary"}]
    )
    assert "expected_sources[0].passage_id_or_reason" in validate_case_schema(row)


def test_bad_split_fails():
    assert "split" in validate_case_schema(_valid_case(split="train"))


def test_schema_version_must_be_2():
    assert "schema_version" in validate_case_schema(
        _valid_case(schema_version="1.0")
    )
