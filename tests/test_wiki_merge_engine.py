"""Tests for WikiMergeEngine — cross-source claim merge application.

Fixture-based: real WikiRepository + temp wiki_dir.
All tests use hand-crafted Claims + ClaimMatchDecisions (no LLM/embedding).
"""
from __future__ import annotations

import pytest

from src.models.wiki_v2 import (
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    PageStatus,
    PageType,
    WikiPage,
)
from src.services.wiki_claim_matcher import ClaimMatchDecision, _normalize
from src.services.wiki_merge_engine import WikiMergeEngine
from src.services.wiki_repository import WikiRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
NOW = "2026-07-08T12:00:00+08:00"


def _make_page(page_id: str = "concepts/fttr") -> WikiPage:
    return WikiPage(
        schema_version=1,
        page_id=page_id,
        title="FTTR",
        page_type=PageType.CONCEPTS,
        status=PageStatus.PUBLISHED,
        revision=1,
        aliases=[],
        tags=[],
        source_ids=[],
        claim_ids=[],
        created_at=NOW,
        updated_at=NOW,
        content_hash="h0",
        body="",
    )


def _claim(
    claim_id: str,
    statement: str,
    status: ClaimStatus = ClaimStatus.ACTIVE,
    knowledge_id: str = "k1",
    block_id: str | None = "b1",
    stance: EvidenceStance = EvidenceStance.SUPPORTS,
    subject_refs: list[str] | None = None,
    predicate: str = "",
    object_refs: list[str] | None = None,
    normalized: str | None = None,
) -> Claim:
    ev = Evidence(
        evidence_id=f"ev_{claim_id}",
        stance=stance,
        knowledge_id=knowledge_id,
        block_id=block_id,
    )
    return Claim(
        schema_version=1,
        claim_id=claim_id,
        statement=statement,
        normalized_statement=normalized or _normalize(statement),
        claim_type="fact",
        status=status,
        confidence=0.9,
        valid_from=None,
        valid_to=None,
        subject_refs=subject_refs or [],
        predicate=predicate,
        object_refs=object_refs or [],
        evidence=[ev],
        relations=[],
        created_at=NOW,
        updated_at=NOW,
        revision=1,
    )


def _decision(
    action: str, target_claim_id: str | None = None, score: float = 0.9
) -> ClaimMatchDecision:
    return ClaimMatchDecision(
        action=action,
        target_claim_id=target_claim_id,
        score=score,
        reasons=["test"],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def repo(tmp_path):
    wiki_dir = tmp_path / "wiki"
    return WikiRepository(
        wiki_dir=wiki_dir,
        registry_path=wiki_dir / "_meta" / "pages.json",
        redirects_path=wiki_dir / "_meta" / "redirects.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )


@pytest.fixture
def engine(repo):
    return WikiMergeEngine(repository=repo)


# ---------------------------------------------------------------------------
# 1. supports: adds evidence, no new claim
# ---------------------------------------------------------------------------
class TestSupports:
    def test_supports_adds_evidence_no_new_claim(self, repo, engine):
        existing = _claim("c1", "FTTR下行速率可达100Mbps")
        repo.save_claim(existing, expected_revision=None)

        new_claim = _claim(
            "n1",
            "FTTR下行速率可达100Mbps",
            knowledge_id="k_b",
            block_id="b2",
        )
        decision = _decision("supports", target_claim_id="c1")

        result = engine.apply([(new_claim, decision)], now=NOW)

        assert result.committed is True
        assert result.claims_created == []
        assert result.claims_updated == ["c1"]

        saved = repo.get_claim("c1")
        assert saved is not None
        assert saved.status == ClaimStatus.ACTIVE
        assert len(saved.evidence) == 2
        assert saved.revision == 2
        assert saved.updated_at == NOW
        assert any(e.knowledge_id == "k_b" for e in saved.evidence)
        assert "+evidence" in result.diff


# ---------------------------------------------------------------------------
# 2. duplicate: no new claim, dedupes evidence
# ---------------------------------------------------------------------------
class TestDuplicate:
    def test_duplicate_no_new_claim_dedupes_evidence(self, repo, engine):
        existing = _claim("c1", "FTTR下行速率可达100Mbps", knowledge_id="k_a", block_id="b1")
        repo.save_claim(existing, expected_revision=None)

        new_claim = _claim("n1", "FTTR下行速率可达100Mbps", knowledge_id="k_a", block_id="b1")
        decision = _decision("duplicate", target_claim_id="c1")

        result = engine.apply([(new_claim, decision)], now=NOW)

        assert result.committed is True
        assert result.claims_created == []
        saved = repo.get_claim("c1")
        assert saved is not None
        # evidence should NOT have been duplicated
        assert len(saved.evidence) == 1
        assert len(result.skipped) > 0
        # repo should still have only 1 claim
        assert len(repo.list_claims()) == 1


# ---------------------------------------------------------------------------
# 3. contradicts: marks disputed
# ---------------------------------------------------------------------------
class TestContradicts:
    def test_contradicts_marks_disputed(self, repo, engine):
        existing = _claim("c1", "FTTR下行速率可达100Mbps")
        repo.save_claim(existing, expected_revision=None)

        new_claim = _claim(
            "n1",
            "FTTR下行速率可达100Mbps",
            stance=EvidenceStance.CONTRADICTS,
        )
        decision = _decision("contradicts", target_claim_id="c1")

        result = engine.apply([(new_claim, decision)], now=NOW)

        assert result.committed is True
        saved = repo.get_claim("c1")
        assert saved is not None
        assert saved.status == ClaimStatus.DISPUTED
        assert saved.revision == 2
        assert len(result.review_items) > 0
        assert "active -> disputed" in result.diff


# ---------------------------------------------------------------------------
# 4. supersedes: creates new, links old
# ---------------------------------------------------------------------------
class TestSupersedes:
    def test_supersedes_creates_new_and_links(self, repo, engine):
        existing = _claim("c1", "FTTR下行速率可达100Mbps")
        repo.save_claim(existing, expected_revision=None)
        page = _make_page()
        page.claim_ids = ["c1"]
        repo.save_page(page, expected_revision=None)

        new_claim = _claim("n1", "FTTR下行速率可达100Mbps")
        decision = _decision("supersedes", target_claim_id="c1")

        result = engine.apply([(new_claim, decision)], page=page, now=NOW)

        assert result.committed is True
        assert "n1" in result.claims_created
        assert "c1" in result.claims_superseded

        # old claim superseded
        old = repo.get_claim("c1")
        assert old is not None
        assert old.status == ClaimStatus.SUPERSEDED
        assert any(r.relation == "superseded_by" and r.target_claim_id == "n1" for r in old.relations)

        # new claim active with supersedes relation
        new = repo.get_claim("n1")
        assert new is not None
        assert new.status == ClaimStatus.ACTIVE
        assert any(r.relation == "supersedes" and r.target_claim_id == "c1" for r in new.relations)

        # page claim_ids replaced
        updated_page = repo.get_page(page.page_id)
        assert updated_page is not None
        assert "n1" in updated_page.claim_ids
        assert "c1" not in updated_page.claim_ids


# ---------------------------------------------------------------------------
# 5. transaction rollback on failure
# ---------------------------------------------------------------------------
class TestTransactionRollback:
    def test_transaction_rollback_on_failure(self, repo, engine, monkeypatch):
        existing = _claim("c1", "FTTR下行速率可达100Mbps")
        repo.save_claim(existing, expected_revision=None)

        new_claim = _claim("n1", "FTTR下行速率可达100Mbps", knowledge_id="k_b", block_id="b2")
        decision = _decision("supports", target_claim_id="c1")

        # Patch WikiTransaction.commit to raise — simulates failure during flush
        def _failing_commit(self_tx):
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(
            "src.services.wiki_repository.WikiTransaction.commit",
            _failing_commit,
        )

        # Apply should propagate the error (transaction context manager re-raises)
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            engine.apply([(new_claim, decision)], now=NOW)

        # Verify rollback: staging was discarded, existing claim unchanged
        # Unpatch to read from filesystem normally — but monkeypatch is on the class,
        # so get_claim reads YAML directly (not through save_claim)
        raw = repo.get_claim("c1")
        assert raw is not None
        assert raw.status == ClaimStatus.ACTIVE
        assert len(raw.evidence) == 1
        assert raw.revision == 1
        # No new claim should exist
        assert repo.get_claim("n1") is None


# ---------------------------------------------------------------------------
# 6. page claim_ids updated
# ---------------------------------------------------------------------------
class TestPageClaimIds:
    def test_page_claim_ids_updated(self, repo, engine):
        page = _make_page()
        repo.save_page(page, expected_revision=None)

        new_claim = _claim("n1", "FTTR下行速率可达100Mbps")
        decision = _decision("new")

        result = engine.apply([(new_claim, decision)], page=page, now=NOW)

        assert result.committed is True
        updated_page = repo.get_page(page.page_id)
        assert updated_page is not None
        assert "n1" in updated_page.claim_ids


# ---------------------------------------------------------------------------
# 7. diff stable
# ---------------------------------------------------------------------------
class TestDiffStable:
    def test_diff_stable(self, repo, engine):
        existing = _claim("c1", "FTTR下行速率可达100Mbps")
        repo.save_claim(existing, expected_revision=None)

        new_claim = _claim("n1", "FTTR下行速率可达100Mbps", knowledge_id="k_b", block_id="b2")
        decision = _decision("supports", target_claim_id="c1")

        result1 = engine.apply([(new_claim, decision)], now=NOW)
        assert result1.committed is True

        # Reset: undo the merge by re-saving original state
        original = _claim("c1", "FTTR下行速率可达100Mbps")
        repo.save_claim(original, expected_revision=None)

        result2 = engine.apply([(new_claim, decision)], now=NOW)
        assert result2.committed is True

        assert result1.diff == result2.diff


# ---------------------------------------------------------------------------
# 8. unresolved: skips and reviews
# ---------------------------------------------------------------------------
class TestUnresolved:
    def test_unresolved_skips_and_reviews(self, repo, engine):
        existing = _claim("c1", "FTTR下行速率可达100Mbps")
        repo.save_claim(existing, expected_revision=None)

        new_claim = _claim("n1", "FTTR下行速率可达100Mbps")
        decision = _decision("unresolved", target_claim_id="c1")

        result = engine.apply([(new_claim, decision)], now=NOW)

        assert result.committed is True
        # No new claim should be saved
        assert repo.get_claim("n1") is None
        assert len(result.review_items) > 0
        assert "n1" in result.skipped


# ---------------------------------------------------------------------------
# 9. new claim saved as draft
# ---------------------------------------------------------------------------
class TestNewClaim:
    def test_new_claim_saved_as_draft(self, repo, engine):
        new_claim = _claim("n1", "FTTR下行速率可达100Mbps")
        decision = _decision("new")

        result = engine.apply([(new_claim, decision)], now=NOW)

        assert result.committed is True
        assert "n1" in result.claims_created

        saved = repo.get_claim("n1")
        assert saved is not None
        assert saved.status == ClaimStatus.DRAFT
        assert "created (draft)" in result.diff


# ---------------------------------------------------------------------------
# 10. refines: creates new draft claim with relation
# ---------------------------------------------------------------------------
class TestRefines:
    def test_refines_creates_new_draft_and_links(self, repo, engine):
        existing = _claim("c1", "FTTR下行速率可达100Mbps")
        repo.save_claim(existing, expected_revision=None)
        page = _make_page()
        page.claim_ids = ["c1"]
        repo.save_page(page, expected_revision=None)

        new_claim = _claim("n1", "FTTR下行速率可达100Mbps")
        decision = _decision("refines", target_claim_id="c1")

        result = engine.apply([(new_claim, decision)], page=page, now=NOW)

        assert result.committed is True
        assert "n1" in result.claims_created

        # target should have refined_by relation
        target = repo.get_claim("c1")
        assert target is not None
        assert any(r.relation == "refined_by" and r.target_claim_id == "n1" for r in target.relations)

        # new claim should be draft
        new = repo.get_claim("n1")
        assert new is not None
        assert new.status == ClaimStatus.DRAFT

        # page should append new claim_id
        updated_page = repo.get_page(page.page_id)
        assert updated_page is not None
        assert "n1" in updated_page.claim_ids
        assert "c1" in updated_page.claim_ids


# ---------------------------------------------------------------------------
# 11. batch aggregation: same target accumulates evidence
# ---------------------------------------------------------------------------
class TestBatchAggregation:
    def test_batch_same_target_accumulates_evidence(self, repo, engine):
        existing = _claim("c1", "FTTR下行速率可达100Mbps")
        repo.save_claim(existing, expected_revision=None)

        new_a = _claim("n1", "FTTR下行速率可达100Mbps", knowledge_id="k_a", block_id="b1")
        new_b = _claim("n2", "FTTR下行速率可达100Mbps", knowledge_id="k_b", block_id="b2")

        decision_a = _decision("supports", target_claim_id="c1")
        decision_b = _decision("supports", target_claim_id="c1")

        result = engine.apply([(new_a, decision_a), (new_b, decision_b)], now=NOW)

        assert result.committed is True
        saved = repo.get_claim("c1")
        assert saved is not None
        # Original 1 + 2 new = 3 evidence
        assert len(saved.evidence) == 3
        # Should only be staged once (revision +1, not +2)
        assert saved.revision == 2


# ---------------------------------------------------------------------------
# 12. validate failure is tolerated (not thrown)
# ---------------------------------------------------------------------------
class TestValidateTolerance:
    def test_validate_failure_recorded_as_error(self, repo, engine, monkeypatch):
        existing = _claim("c1", "FTTR下行速率可达100Mbps")
        repo.save_claim(existing, expected_revision=None)

        # A normal "supports" item that should succeed
        good_claim = _claim("g1", "FTTR下行速率可达100Mbps", knowledge_id="k_good", block_id="b_good")
        good_decision = _decision("supports", target_claim_id="c1")

        # Force validate() to fail on claim "bad" — monkeypatch class method
        original_validate = Claim.validate

        def _failing_validate(self):
            if self.claim_id == "bad":
                return ["invariant violation: forced failure"]
            return original_validate(self)

        monkeypatch.setattr(Claim, "validate", _failing_validate)

        bad_claim = _claim("bad", "FTTR下行速率可达100Mbps")
        bad_decision = _decision("new")

        result = engine.apply(
            [(bad_claim, bad_decision), (good_claim, good_decision)],
            now=NOW,
        )

        # Engine should commit (other items succeed)
        assert result.committed is True
        # Bad claim should be in errors
        assert any("invariant violation" in e for e in result.errors)
        assert "bad" in result.skipped
        # Bad claim should NOT be in created/updated
        assert "bad" not in result.claims_created
        assert "bad" not in result.claims_updated
        # Good claim should still succeed (failure doesn't block other items)
        assert "c1" in result.claims_updated
