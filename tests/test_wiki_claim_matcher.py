"""Tests for ClaimMatcher — cross-source claim merge action classification.

Fixture-based TDD: 7 spec fixtures anchor each action, plus edge cases.
All tests inject deterministic scores (CD1) — no real embedding dependency.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.models.wiki_v2 import Claim, ClaimStatus, Evidence, EvidenceStance, normalize_statement
from src.services.wiki_claim_matcher import ClaimMatcher


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
        normalized_statement=normalized or normalize_statement(statement),
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
# 11. Exact match survives low score (M-3 regression test)
# ---------------------------------------------------------------------------
class TestExactMatchSurvivesLowScore:
    """When embedding is unavailable and scores are all 0, exact hash match must
    still be detected before the score guard short-circuits to 'new'.
    Regression test for CD1 degradation scenario (T3.3 container fallback).
    """

    def test_exact_match_survives_low_score(self):
        matcher = ClaimMatcher()
        statement = "FTTR下行速率可达100Mbps"
        existing = _claim("c1", statement, object_refs=["100Mbps"])
        new = _claim("n1", statement, object_refs=["100Mbps"])
        # Inject low score (< unresolved_threshold 0.72) — should still be duplicate
        decision = matcher.match(new, candidates=[existing], scores={"c1": 0.5})
        assert decision.action == "duplicate"
        assert decision.target_claim_id == "c1"
        assert decision.score == 1.0
        assert "exact normalized_statement match" in decision.reasons[0]


# ---------------------------------------------------------------------------
# 12. Reasons populated and stable
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
# 13. Embedding path — fake embedding service, no crash
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


# ---------------------------------------------------------------------------
# 14. C1: 每种 action 必须填充稳定 reason_code
# ---------------------------------------------------------------------------
class TestReasonCodes:
    def test_no_candidates_reason_code(self):
        matcher = ClaimMatcher()
        new = _claim("n1", "FTTR下行速率可达100Mbps")
        d = matcher.match(new, candidates=[], scores=None)
        assert d.action == "new"
        assert "no_candidates" in d.reason_codes

    def test_exact_duplicate_reason_code(self):
        matcher = ClaimMatcher()
        statement = "FTTR下行速率可达100Mbps"
        existing = _claim("c1", statement, object_refs=["100Mbps"])
        new = _claim("n1", statement, object_refs=["100Mbps"])
        d = matcher.match(new, candidates=[existing], scores={"c1": 1.0})
        assert d.action == "duplicate"
        assert "exact_normalized_match" in d.reason_codes

    def test_exact_object_conflict_reason_code(self):
        matcher = ClaimMatcher()
        statement = "FTTR下行速率可达100Mbps"
        existing = _claim("c1", statement, object_refs=["100Mbps"])
        new = _claim("n1", statement, object_refs=["200Mbps"])
        d = matcher.match(new, candidates=[existing], scores={"c1": 1.0})
        assert d.action == "contradicts"
        assert "exact_normalized_match" in d.reason_codes
        assert "object_refs_conflict" in d.reason_codes

    def test_low_confidence_reason_code(self):
        matcher = ClaimMatcher()
        existing = _claim("c1", "完全不同的甲陈述")
        new = _claim("n1", "完全不同的乙陈述")
        d = matcher.match(new, candidates=[existing], scores={"c1": 0.3})
        assert d.action == "new"
        assert "low_confidence" in d.reason_codes

    def test_ambiguous_unresolved_reason_code(self):
        matcher = ClaimMatcher()
        existing = _claim("c1", "FTTR下行速率可达100Mbps")
        new = _claim("n1", "FTTR下行速率可达大约100兆")
        d = matcher.match(new, candidates=[existing], scores={"c1": 0.8})
        assert d.action == "unresolved"
        assert "ambiguous_candidates" in d.reason_codes

    def test_temporal_supersedes_reason_code(self):
        matcher = ClaimMatcher()
        existing = _claim("c1", "旧标准速率", subject_refs=["entity:X"],
                         predicate="speed", object_refs=["100Mbps"], valid_from="2020-01-01")
        new = _claim("n1", "新标准速率", subject_refs=["entity:X"],
                     predicate="speed", object_refs=["200Mbps"], valid_from="2024-01-01")
        d = matcher.match(new, candidates=[existing], scores={"c1": 0.95})
        assert d.action == "supersedes"
        assert "temporal_supersedes" in d.reason_codes

    def test_refines_reason_code(self):
        matcher = ClaimMatcher()
        # subject_refs 真超集 + object_refs 相同(避免被先判的 objects_conflict 遮蔽)。
        # 注:refines 的 object 超集分支当前被 objects_conflict(set!=)遮蔽不可达,
        # 留 C2 黄金集判定是否需细化(契约 §5 保守复核)。
        existing = _claim("c1", "X的速度是100", subject_refs=["entity:X"],
                         predicate="speed", object_refs=["100Mbps"])
        new = _claim("n1", "X型号设备的速度是100", subject_refs=["entity:X", "model:A"],
                     predicate="speed", object_refs=["100Mbps"])
        d = matcher.match(new, candidates=[existing], scores={"c1": 0.95})
        assert d.action == "refines"
        assert "refines_superset" in d.reason_codes

    def test_supports_fallback_reason_code(self):
        matcher = ClaimMatcher()
        existing = _claim("c1", "FTTR的速度是100兆",
                         subject_refs=["entity:FTTR"], predicate="speed", object_refs=["100Mbps"])
        new = _claim("n1", "FTTR能够达到100Mbps下行",
                     subject_refs=["entity:FTTR"], predicate="speed", object_refs=["100Mbps"])
        d = matcher.match(new, candidates=[existing], scores={"c1": 0.95})
        assert d.action == "supports"
        assert "supports_fallback" in d.reason_codes

    def test_new_has_contradicts_evidence_reason_code(self):
        matcher = ClaimMatcher()
        existing = _claim("c1", "FTTR的速度是100兆",
                         subject_refs=["entity:FTTR"], predicate="speed", object_refs=["100Mbps"])
        new = _claim("n1", "FTTR能够达到200兆下行",
                     subject_refs=["entity:FTTR"], predicate="speed",
                     object_refs=["100Mbps"], stance=EvidenceStance.CONTRADICTS)
        d = matcher.match(new, candidates=[existing], scores={"c1": 0.95})
        assert d.action == "contradicts"
        assert "new_has_contradicts_evidence" in d.reason_codes


# ---------------------------------------------------------------------------
# 15. C1: matcher 与 extractor 共用 models.normalize_statement(禁止各自重造)
# ---------------------------------------------------------------------------
class TestNormalizeShared:
    def test_normalize_statement_canonical(self):
        from src.models.wiki_v2 import normalize_statement
        assert normalize_statement("FTTR的速度，是 100Mbps！") == "fttr的速度是 100mbps"
        assert normalize_statement("  Hello   World  ") == "hello world"
        assert normalize_statement("A.B-C") == "abc"

    def test_extractor_normalize_delegates_to_canonical(self):
        """extractor._normalize 必须委托 models.normalize_statement,不得重造 re.sub。"""
        import inspect

        from src.services.wiki_claim_extractor import ClaimExtractor
        src = inspect.getsource(ClaimExtractor._normalize)
        assert "normalize_statement" in src, "extractor._normalize 未委托共用 normalize_statement"
        assert "re.sub" not in src, "extractor._normalize 重造了 normalize(违反 C1 契约)"

    def test_matcher_uses_canonical_normalize(self):
        """matcher._exact_hash 必须用 models.normalize_statement。"""
        import inspect

        from src.services.wiki_claim_matcher import ClaimMatcher
        src = inspect.getsource(ClaimMatcher._exact_hash)
        assert "normalize_statement" in src, "matcher._exact_hash 未使用共用 normalize_statement"


