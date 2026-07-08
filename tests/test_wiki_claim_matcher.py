"""Tests for ClaimMatcher — cross-source claim merge action classification.

Fixture-based TDD: 7 spec fixtures anchor each action, plus edge cases.
All tests inject deterministic scores (CD1) — no real embedding dependency.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.models.wiki_v2 import Claim, ClaimStatus, Evidence, EvidenceStance
from src.services.wiki_claim_matcher import ClaimMatcher, _normalize


# ---------------------------------------------------------------------------
# Helper: construct Claim with minimal boilerplate
# ---------------------------------------------------------------------------
def _claim(
    claim_id: str,
    statement: str,
    subject_refs: list[str] | None = None,
    predicate: str = "",
    object_refs: list[str] | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    stance: EvidenceStance = EvidenceStance.SUPPORTS,
    normalized: str | None = None,
) -> Claim:
    ev = Evidence(
        evidence_id="ev_" + claim_id,
        stance=stance,
        knowledge_id="k1",
        block_id="b1",
    )
    return Claim(
        schema_version=1,
        claim_id=claim_id,
        statement=statement,
        normalized_statement=normalized or _normalize(statement),
        claim_type="fact",
        status=ClaimStatus.ACTIVE,
        confidence=0.9,
        valid_from=valid_from,
        valid_to=valid_to,
        subject_refs=subject_refs or [],
        predicate=predicate,
        object_refs=object_refs or [],
        evidence=[ev],
        relations=[],
        created_at="2026-07-08T00:00:00+08:00",
        updated_at="2026-07-08T00:00:00+08:00",
        revision=1,
    )


# ---------------------------------------------------------------------------
# 1. No candidates → new
# ---------------------------------------------------------------------------
class TestNoCandidates:
    def test_no_candidates_returns_new(self):
        matcher = ClaimMatcher()
        new = _claim("n1", "FTTR下行速率可达100Mbps")
        decision = matcher.match(new, candidates=[], scores=None)
        assert decision.action == "new"
        assert decision.target_claim_id is None
        assert decision.score == 0.0


# ---------------------------------------------------------------------------
# 2. Exact same → duplicate
# ---------------------------------------------------------------------------
class TestExactDuplicate:
    def test_exact_same_returns_duplicate(self):
        matcher = ClaimMatcher()
        statement = "FTTR下行速率可达100Mbps"
        existing = _claim("c1", statement, object_refs=["100Mbps"])
        new = _claim("n1", statement, object_refs=["100Mbps"])
        decision = matcher.match(new, candidates=[existing], scores={"c1": 1.0})
        assert decision.action == "duplicate"
        assert decision.target_claim_id == "c1"
        assert decision.score == 1.0


# ---------------------------------------------------------------------------
# 3. Synonym → supports
# ---------------------------------------------------------------------------
class TestSynonymSupports:
    def test_synonym_supports(self):
        matcher = ClaimMatcher()
        existing = _claim(
            "c1",
            "FTTR下行速率可达100Mbps",
            subject_refs=["FTTR"],
            predicate="下行速率",
            object_refs=["100Mbps"],
        )
        new = _claim(
            "n1",
            "光纤到房间下行带宽为100兆",
            subject_refs=["FTTR"],
            predicate="下行速率",
            object_refs=["100Mbps"],
        )
        decision = matcher.match(new, candidates=[existing], scores={"c1": 0.92})
        assert decision.action == "supports"
        assert decision.target_claim_id == "c1"


# ---------------------------------------------------------------------------
# 4. Numeric conflict → contradicts
# ---------------------------------------------------------------------------
class TestNumericConflict:
    def test_numeric_conflict_contradicts(self):
        matcher = ClaimMatcher()
        existing = _claim(
            "c1",
            "FTTR下行速率可达100Mbps",
            subject_refs=["FTTR"],
            predicate="下行速率",
            object_refs=["100"],
        )
        new = _claim(
            "n1",
            "FTTR下行速率可达120Mbps",
            subject_refs=["FTTR"],
            predicate="下行速率",
            object_refs=["120"],
        )
        decision = matcher.match(new, candidates=[existing], scores={"c1": 0.95})
        assert decision.action == "contradicts"
        assert decision.target_claim_id == "c1"


# ---------------------------------------------------------------------------
# 5. Temporal update → supersedes
# ---------------------------------------------------------------------------
class TestTemporalSupersedes:
    def test_temporal_update_supersedes(self):
        matcher = ClaimMatcher()
        existing = _claim(
            "c1",
            "FTTR下行速率100Mbps",
            subject_refs=["FTTR"],
            predicate="下行速率",
            object_refs=["100Mbps"],
            valid_from="2026-07-01",
        )
        new = _claim(
            "n1",
            "FTTR下行速率120Mbps",
            subject_refs=["FTTR"],
            predicate="下行速率",
            object_refs=["120Mbps"],
            valid_from="2026-08-01",
        )
        decision = matcher.match(new, candidates=[existing], scores={"c1": 0.90})
        assert decision.action == "supersedes"
        assert decision.target_claim_id == "c1"


# ---------------------------------------------------------------------------
# 6. Extra qualifier → refines
# ---------------------------------------------------------------------------
class TestRefines:
    def test_extra_qualifier_refines(self):
        matcher = ClaimMatcher()
        existing = _claim(
            "c1",
            "FTTR下行速率100Mbps",
            subject_refs=["FTTR"],
            predicate="下行速率",
            object_refs=["100Mbps"],
        )
        new = _claim(
            "n1",
            "FTTR家庭场景下行速率100Mbps",
            subject_refs=["FTTR", "家庭场景"],
            predicate="下行速率",
            object_refs=["100Mbps"],
        )
        decision = matcher.match(new, candidates=[existing], scores={"c1": 0.89})
        assert decision.action == "refines"
        assert decision.target_claim_id == "c1"


# ---------------------------------------------------------------------------
# 7. Explicit supersedes
# ---------------------------------------------------------------------------
class TestExplicitSupersedes:
    def test_explicit_supersedes(self):
        matcher = ClaimMatcher()
        existing = _claim(
            "c1",
            "FTTR下行速率100Mbps",
            subject_refs=["FTTR"],
            predicate="下行速率",
            object_refs=["100Mbps"],
            valid_from="2026-06-01",
        )
        new = _claim(
            "n1",
            "FTTR下行速率120Mbps",
            subject_refs=["FTTR"],
            predicate="下行速率",
            object_refs=["120Mbps"],
            valid_from="2026-09-01",
        )
        decision = matcher.match(new, candidates=[existing], scores={"c1": 0.93})
        assert decision.action == "supersedes"
        assert decision.target_claim_id == "c1"


# ---------------------------------------------------------------------------
# 8. Low confidence (0.72 <= score < 0.88) → unresolved
# ---------------------------------------------------------------------------
class TestUnresolved:
    def test_low_confidence_unresolved(self):
        matcher = ClaimMatcher()
        existing = _claim("c1", "FTTR下行速率100Mbps")
        new = _claim("n1", "FTTR下行速率约100Mbps")
        decision = matcher.match(new, candidates=[existing], scores={"c1": 0.75})
        assert decision.action == "unresolved"
        assert decision.target_claim_id == "c1"


# ---------------------------------------------------------------------------
# 9. Below unresolved threshold → new
# ---------------------------------------------------------------------------
class TestBelowUnresolved:
    def test_below_unresolved_returns_new(self):
        matcher = ClaimMatcher()
        existing = _claim("c1", "FTTR下行速率100Mbps")
        new = _claim("n1", "5G基站覆盖范围扩大")
        decision = matcher.match(new, candidates=[existing], scores={"c1": 0.60})
        assert decision.action == "new"
        assert decision.target_claim_id is None


# ---------------------------------------------------------------------------
# 10. Exact normalized_statement + object_refs conflict → contradicts
# ---------------------------------------------------------------------------
class TestExactWithConflict:
    def test_exact_with_object_conflict_contradicts(self):
        matcher = ClaimMatcher()
        # Same normalized statement but different object_refs
        statement = "FTTR下行速率可达100Mbps"
        existing = _claim("c1", statement, object_refs=["100"])
        new = _claim("n1", statement, object_refs=["120"])
        decision = matcher.match(new, candidates=[existing], scores={"c1": 1.0})
        assert decision.action == "contradicts"
        assert decision.target_claim_id == "c1"


# ---------------------------------------------------------------------------
# 11. Reasons populated and stable
# ---------------------------------------------------------------------------
class TestReasons:
    def test_reasons_populated_and_stable(self):
        matcher = ClaimMatcher()
        existing = _claim("c1", "FTTR下行速率可达100Mbps", object_refs=["100Mbps"])
        new = _claim("n1", "FTTR下行速率可达100Mbps", object_refs=["100Mbps"])

        d1 = matcher.match(new, candidates=[existing], scores={"c1": 1.0})
        assert isinstance(d1.reasons, list)
        assert len(d1.reasons) > 0
        assert all(isinstance(r, str) for r in d1.reasons)

        # Same input, same reasons (stable)
        d2 = matcher.match(new, candidates=[existing], scores={"c1": 1.0})
        assert d1.reasons == d2.reasons

    def test_contradicts_reasons_show_conflict(self):
        matcher = ClaimMatcher()
        existing = _claim(
            "c1",
            "FTTR下行速率可达100Mbps",
            subject_refs=["FTTR"],
            predicate="下行速率",
            object_refs=["100"],
        )
        new = _claim(
            "n1",
            "FTTR下行速率可达120Mbps",
            subject_refs=["FTTR"],
            predicate="下行速率",
            object_refs=["120"],
        )
        decision = matcher.match(new, candidates=[existing], scores={"c1": 0.95})
        assert decision.action == "contradicts"
        assert any("conflict" in r.lower() or "object_refs" in r.lower() for r in decision.reasons)


# ---------------------------------------------------------------------------
# 12. Embedding path — fake embedding service, no crash
# ---------------------------------------------------------------------------
class TestEmbeddingPath:
    def test_embedding_path_optional(self):
        """Verify _embed_scores path doesn't crash with a fake embedding service."""
        fake_embedding = FakeEmbeddingService()
        matcher = ClaimMatcher(embedding=fake_embedding)
        existing = _claim("c1", "FTTR下行速率可达100Mbps")
        new = _claim("n1", "FTTR下行速率可达100Mbps")
        # Don't pass scores → matcher must compute via embedding
        decision = matcher.match(new, candidates=[existing], scores=None)
        # Don't assert action — fake vector similarity is meaningless
        assert decision.action in ("new", "supports", "duplicate", "refines",
                                   "contradicts", "supersedes", "unresolved")

    def test_embedding_path_no_embedding_no_crash(self):
        """No embedding + no scores → pure lexical/exact fallback, no crash."""
        matcher = ClaimMatcher(embedding=None)
        existing = _claim("c1", "FTTR下行速率可达100Mbps")
        new = _claim("n1", "完全不同的句子")
        decision = matcher.match(new, candidates=[existing], scores=None)
        assert decision.action in ("new", "supports", "duplicate", "refines",
                                   "contradicts", "supersedes", "unresolved")


@dataclass
class FakeEmbeddingService:
    """Minimal fake embedding service for testing."""

    def embed(self, text: str) -> list[float]:
        return [1.0] * 8
