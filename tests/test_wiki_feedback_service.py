"""WikiFeedbackService 测试(Phase 6 T6.3)。"""
from __future__ import annotations

from pathlib import Path

from src.models.wiki_v2 import (
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceStance,
)
from src.services.wiki_feedback_service import WikiFeedbackService
from src.services.wiki_repository import WikiRepository


def _repo(tmp: Path) -> WikiRepository:
    wiki = tmp / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    return WikiRepository(
        wiki_dir=wiki,
        registry_path=wiki / "_meta" / "pages.json",
        redirects_path=wiki / "_meta" / "redirects.json",
        outbox_path=tmp / "outbox.jsonl",
    )


def _claim(cid: str, status=ClaimStatus.DRAFT, with_support: bool = True) -> Claim:
    evidence = []
    if with_support:
        evidence = [
            Evidence(
                evidence_id="e1", stance=EvidenceStance.SUPPORTS,
                knowledge_id="k1", block_id="b1", source_revision="v1",
                excerpt_hash="h", observed_at="t",
            )
        ]
    return Claim(
        schema_version=1, claim_id=cid, statement="original statement",
        normalized_statement="original statement", claim_type="fact",
        status=status, confidence=0.8, valid_from=None, valid_to=None,
        subject_refs=["s"], predicate="p", object_refs=["o"],
        evidence=evidence, relations=[], created_at="t", updated_at="t", revision=1,
    )


class _FakeLog:
    def __init__(self):
        self.entries = []

    def log(self, **kw):
        self.entries.append(kw)
        return f"op-{len(self.entries)}"


def test_confirm_with_support_becomes_active(tmp_path):
    repo = _repo(tmp_path)
    with repo.transaction() as tx:
        tx.stage_claim(_claim("c1", ClaimStatus.DRAFT, with_support=True))
    log = _FakeLog()
    fb = WikiFeedbackService(repository=repo, operation_log=log)
    r = fb.apply("c1", "confirm", operator="tester")
    assert not r.errors
    assert r.after_status == "active"
    assert repo.get_claim("c1").status is ClaimStatus.ACTIVE
    assert log.entries and log.entries[0]["operation"] == "wiki_feedback"


def test_confirm_without_support_errors(tmp_path):
    repo = _repo(tmp_path)
    with repo.transaction() as tx:
        tx.stage_claim(_claim("c2", ClaimStatus.DRAFT, with_support=False))
    fb = WikiFeedbackService(repository=repo)
    r = fb.apply("c2", "confirm")
    assert r.errors
    assert repo.get_claim("c2").status is ClaimStatus.DRAFT


def test_reject_retracts(tmp_path):
    repo = _repo(tmp_path)
    with repo.transaction() as tx:
        tx.stage_claim(_claim("c3", ClaimStatus.ACTIVE))
    fb = WikiFeedbackService(repository=repo)
    r = fb.apply("c3", "reject")
    assert r.after_status == "retracted"
    # get_claim 过滤 RETRACTED(软删除语义);底层仍可读
    assert repo.get_claim("c3") is None
    raw = repo._read_claim_raw("c3")
    assert raw is not None and raw.status is ClaimStatus.RETRACTED


def test_correct_updates_statement_to_draft(tmp_path):
    repo = _repo(tmp_path)
    with repo.transaction() as tx:
        tx.stage_claim(_claim("c4", ClaimStatus.ACTIVE))
    fb = WikiFeedbackService(repository=repo)
    r = fb.apply("c4", "correct", correction="corrected fact text")
    assert not r.errors
    after = repo.get_claim("c4")
    assert after.status is ClaimStatus.DRAFT
    assert after.statement == "corrected fact text"
    assert after.normalized_statement == "corrected fact text"


def test_correct_requires_text(tmp_path):
    repo = _repo(tmp_path)
    with repo.transaction() as tx:
        tx.stage_claim(_claim("c5"))
    fb = WikiFeedbackService(repository=repo)
    r = fb.apply("c5", "correct", correction="  ")
    assert r.errors


def test_needs_review_disputes(tmp_path):
    repo = _repo(tmp_path)
    with repo.transaction() as tx:
        tx.stage_claim(_claim("c6", ClaimStatus.ACTIVE))
    fb = WikiFeedbackService(repository=repo)
    r = fb.apply("c6", "needs_review", note="maybe wrong")
    assert r.after_status == "disputed"
    assert repo.get_claim("c6").status is ClaimStatus.DISPUTED


def test_missing_claim_errors(tmp_path):
    repo = _repo(tmp_path)
    fb = WikiFeedbackService(repository=repo)
    r = fb.apply("missing", "confirm")
    assert r.errors
