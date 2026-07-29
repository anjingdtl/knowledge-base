"""Freeze gate and formal harness integrity tests (Task 2.0.3 / 2.0.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.hit_rate_v2.validation import (
    build_review_manifest_hash,
    dataset_hash,
    validate_formal_frozen_dataset,
    validate_formal_golden_path,
    validate_freeze_row,
    write_jsonl,
)
from scripts.hit_rate_test_harness import (
    _manifest_compatible,
    validate_formal_run_inputs,
)


def _evidence_checked_for_answerable() -> list[dict]:
    return [
        {
            "type": "source",
            "knowledge_id": "k1",
            "passage_id": "p1",
            "source_role": "primary",
            "decision": "accepted",
        },
        {
            "type": "fact",
            "fact_id": "f1",
            "decision": "supported",
            "passage_checked": True,
        },
    ]


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
            "evidence_checked": _evidence_checked_for_answerable(),
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


def test_rejected_status_cannot_freeze():
    row = _reviewed_answerable()
    row["review"]["status"] = "rejected"
    errors = validate_freeze_row(row, expected_corpus_sha="kb.db:deadbeefdeadbeef")
    assert "review.status_not_approved" in errors


def test_evidence_checked_required():
    row = _reviewed_answerable()
    row["review"]["evidence_checked"] = []
    errors = validate_freeze_row(row, expected_corpus_sha="kb.db:deadbeefdeadbeef")
    assert "review.evidence_checked" in errors


def test_adjudicator_must_differ_from_reviewers():
    row = _reviewed_answerable()
    row["review"]["disagreement"] = True
    row["review"]["adjudicator"] = "alice"
    row["review"]["adjudicated_at"] = "2026-07-29T03:00:00Z"
    row["review"]["decision_notes"] = "resolved for bob"
    errors = validate_freeze_row(row, expected_corpus_sha="kb.db:deadbeefdeadbeef")
    assert "review.adjudicator_must_differ" in errors


def test_adjudication_requires_record():
    row = _reviewed_answerable()
    row["review"]["disagreement"] = True
    row["review"]["adjudicator"] = "carol"
    row["review"]["adjudicated_at"] = "2026-07-29T03:00:00Z"
    row["review"]["decision_notes"] = ""
    row["review"]["disagreement_summary"] = ""
    errors = validate_freeze_row(row, expected_corpus_sha="kb.db:deadbeefdeadbeef")
    assert "review.adjudication_record_missing" in errors


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
    # evidence_checked still required; no_answer has no sources/facts to cover.
    row["review"]["evidence_checked"] = [
        {"type": "source", "knowledge_id": "none", "decision": "no_evidence"}
    ]
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


def test_formal_path_name_alone_is_insufficient(tmp_path: Path):
    """Fake .../frozen/... path with unreviewed rows must be rejected."""
    frozen = tmp_path / "frozen" / "fake.jsonl"
    frozen.parent.mkdir(parents=True)
    write_jsonl(
        frozen,
        [
            {
                "case_id": "KB-X",
                "schema_version": "2.0",
                "split": "validation",
                "category": "t",
                "risk_level": "P1",
                "query": "q",
                "answerability": "answerable",
                "annotation_source": "candidate",  # not human_reviewed
            }
        ],
    )
    # Path gate alone passes (directory is frozen/)
    assert validate_formal_golden_path(frozen) == []
    errors, _meta = validate_formal_frozen_dataset(
        frozen, expected_corpus_sha="kb.db:deadbeefdeadbeef"
    )
    assert any("annotation_source" in e for e in errors)
    with pytest.raises(SystemExit):
        validate_formal_run_inputs(
            frozen,
            formal=True,
            expected_corpus_sha="kb.db:deadbeefdeadbeef",
        )


def test_formal_rejects_empty_frozen(tmp_path: Path):
    frozen = tmp_path / "frozen" / "empty.jsonl"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("", encoding="utf-8")
    errors, _ = validate_formal_frozen_dataset(frozen)
    assert "formal_frozen_empty" in errors


def test_formal_rejects_corpus_inconsistency(tmp_path: Path):
    frozen = tmp_path / "frozen" / "mixed.jsonl"
    frozen.parent.mkdir(parents=True)
    a = _reviewed_answerable(case_id="KB-A")
    b = _reviewed_answerable(case_id="KB-B")
    b["corpus_snapshot"] = {"sha": "kb.db:ffffffffffff"}
    write_jsonl(frozen, [a, b])
    errors, _ = validate_formal_frozen_dataset(
        frozen, expected_corpus_sha="kb.db:deadbeefdeadbeef"
    )
    assert "formal_corpus_inconsistent" in errors or any(
        "corpus_snapshot_sha_mismatch" in e for e in errors
    )


def test_formal_rejects_mixed_split(tmp_path: Path):
    frozen = tmp_path / "frozen" / "splits.jsonl"
    frozen.parent.mkdir(parents=True)
    a = _reviewed_answerable(case_id="KB-A", split="validation")
    b = _reviewed_answerable(case_id="KB-B", split="holdout")
    write_jsonl(frozen, [a, b])
    errors, _ = validate_formal_frozen_dataset(
        frozen, expected_corpus_sha="kb.db:deadbeefdeadbeef"
    )
    assert "formal_mixed_split" in errors


def test_formal_accepts_valid_frozen_and_fills_manifest(tmp_path: Path):
    frozen = tmp_path / "frozen" / "ok.jsonl"
    frozen.parent.mkdir(parents=True)
    row = _reviewed_answerable()
    write_jsonl(frozen, [row])
    meta = validate_formal_run_inputs(
        frozen,
        formal=True,
        expected_corpus_sha="kb.db:deadbeefdeadbeef",
        schema_hash="schemahash",
    )
    assert meta["review_manifest_hash"]
    assert meta["corpus_snapshot"] == "kb.db:deadbeefdeadbeef"
    assert meta["dataset_hash"]
    assert meta["split"] == "validation"
    # Deterministic review manifest
    expected_hash = build_review_manifest_hash(
        [row],
        dataset_hash_value=dataset_hash([row]),
        schema_hash_value="schemahash",
    )
    assert meta["review_manifest_hash"] == expected_hash


def test_resume_rejects_review_manifest_change():
    prev = {
        "git_revision": "a",
        "dirty_patch_sha256": "b",
        "production_source_sha256": "c",
        "golden_sha256": "d",
        "scorer_sha256": "e",
        "scorer_contract_version": "2.0",
        "schema_hash": "s",
        "dataset_hash": "ds",
        "split": "validation",
        "review_manifest_hash": "rev1",
        "corpus_snapshot": "kb.db:deadbeefdeadbeef",
        "config_hash": "cfg",
        "index_revision": "i",
        "db_revision": "db",
        "process_start_id": "p",
        "python_version": "3",
        "dependency_lock_sha256": "l",
        "retrieval_mode": "unified",
        "rerank_mode": "deterministic-baseline",
        "rerank_profile": "deterministic-baseline",
        "timeout_settings": {},
        "reuse_snapshot": True,
        "read_mode": "unique",
        "workers": 1,
        "formal": True,
    }
    cur = dict(prev)
    cur["review_manifest_hash"] = "rev2-tampered"
    ok, reason = _manifest_compatible(prev, cur)
    assert ok is False
    assert "review_manifest_hash" in reason


def test_formal_rejects_review_manifest_empty_on_path_only(tmp_path: Path):
    """Empty frozen file → no review manifest → fail closed."""
    frozen = tmp_path / "frozen" / "blank.jsonl"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("\n", encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        validate_formal_run_inputs(
            frozen,
            formal=True,
            expected_corpus_sha="kb.db:deadbeefdeadbeef",
        )
    assert "formal" in str(ei.value).lower() or "frozen" in str(ei.value).lower()
