"""Freeze gate and formal harness path tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.hit_rate_v2.validation import (
    validate_formal_golden_path,
    validate_freeze_row,
)
from scripts.hit_rate_test_harness import validate_formal_run_inputs


def _reviewed_answerable(**overrides):
    row = {
        "case_id": "KB-100",
        "schema_version": "2.0",
        "split": "validation",
        "category": "test",
        "risk_level": "P1",
        "query": "q",
        "answerability": "answerable",
        "intent": "fact",
        "expected_action": "answer",
        "expected_sources": [
            {
                "knowledge_id": "k1",
                "passage_id": "p1",
                "source_role": "primary",
                "evidence_hash": "abc",
            }
        ],
        "required_fact_groups": [
            {
                "fact_id": "f1",
                "object_text": "x",
                "match_policy": "normalized",
                "required": True,
                "evidence_passage_id": "p1",
            }
        ],
        "forbidden_assertions": [],
        "acceptable_variants": [],
        "ambiguity": {"status": "clear", "reason": ""},
        "corpus_snapshot": {"sha": "kb.db:deadbeefdeadbeef"},
        "annotation_source": "human_reviewed",
        "review": {
            "status": "approved",
            "primary_reviewer": "alice",
            "primary_reviewed_at": "2026-07-29T01:00:00Z",
            "secondary_reviewer": "bob",
            "secondary_reviewed_at": "2026-07-29T02:00:00Z",
            "adjudicator": "",
            "adjudicated_at": "",
            "disagreement": False,
            "decision_notes": "ok",
            "evidence_checked": [],
        },
    }
    row.update(overrides)
    return row


def test_freeze_accepts_complete_dual_review():
    assert (
        validate_freeze_row(
            _reviewed_answerable(),
            expected_corpus_sha="kb.db:deadbeefdeadbeef",
        )
        == []
    )


def test_same_reviewer_cannot_freeze():
    row = _reviewed_answerable()
    row["review"]["secondary_reviewer"] = "alice"
    errors = validate_freeze_row(row, expected_corpus_sha="kb.db:deadbeefdeadbeef")
    assert "review.reviewers_must_differ" in errors


def test_corpus_hash_mismatch_cannot_freeze():
    errors = validate_freeze_row(
        _reviewed_answerable(),
        expected_corpus_sha="kb.db:ffffffffffff",
    )
    assert "corpus_snapshot_sha_mismatch" in errors


def test_disputed_cannot_freeze():
    row = _reviewed_answerable()
    row["ambiguity"] = {"status": "disputed", "reason": "x"}
    errors = validate_freeze_row(row, expected_corpus_sha="kb.db:deadbeefdeadbeef")
    assert "ambiguity.disputed" in errors


def test_answerable_missing_passage_cannot_freeze():
    row = _reviewed_answerable(
        expected_sources=[
            {
                "knowledge_id": "k1",
                "passage_id": None,
                "passage_missing_reason": "missing",
                "source_role": "primary",
            }
        ]
    )
    errors = validate_freeze_row(row, expected_corpus_sha="kb.db:deadbeefdeadbeef")
    assert any("passage_id_required_for_freeze" in e for e in errors)


def test_no_answer_missing_reason_cannot_freeze():
    row = _reviewed_answerable(
        answerability="no_answer",
        expected_action="refuse",
        expected_sources=[],
        required_fact_groups=[],
        no_answer_reason="",
        ambiguity={"status": "clear", "reason": ""},
    )
    errors = validate_freeze_row(row, expected_corpus_sha="kb.db:deadbeefdeadbeef")
    assert "no_answer.reason" in errors


def test_formal_rejects_candidates_path(tmp_path: Path):
    cand = tmp_path / "candidates" / "x.jsonl"
    cand.parent.mkdir(parents=True)
    cand.write_text("{}\n", encoding="utf-8")
    errors = validate_formal_golden_path(cand)
    assert "formal_rejects_candidates" in errors
    with pytest.raises(SystemExit):
        validate_formal_run_inputs(cand, formal=True)


def test_formal_rejects_reviewed_not_frozen(tmp_path: Path):
    rev = tmp_path / "reviewed" / "x.jsonl"
    rev.parent.mkdir(parents=True)
    rev.write_text("{}\n", encoding="utf-8")
    errors = validate_formal_golden_path(rev)
    assert "formal_rejects_unfrozen_reviewed" in errors


def test_formal_accepts_frozen_path(tmp_path: Path):
    frozen = tmp_path / "frozen" / "x.jsonl"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("{}\n", encoding="utf-8")
    assert validate_formal_golden_path(frozen) == []
    validate_formal_run_inputs(frozen, formal=True)  # no raise
