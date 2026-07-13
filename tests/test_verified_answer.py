"""Phase 4: conflict disclosure, answer_mode, claim+evidence citations."""
from __future__ import annotations

from src.services.citation_builder import CitationBuilder
from src.services.verified_answer import (
    ANSWER_MODE_CONFLICT,
    ANSWER_MODE_HYBRID,
    ANSWER_MODE_NO_ANSWER,
    ANSWER_MODE_RAW,
    assemble_answer_payload,
    build_claim_citations,
)
from src.services.verified_conflict import (
    claims_conflict,
    detect_claim_conflicts,
    filter_stale_claims,
    is_freshness_sensitive_query,
)


class TestConflictDetection:
    def test_numeric_mismatch(self):
        a = {"claim_id": "c1", "text": "FTTR 上行可达 1Gbps", "evidence": [{"block_id": "b1", "knowledge_id": "k1"}]}
        b = {"claim_id": "c2", "text": "FTTR 上行可达 100Mbps", "evidence": [{"block_id": "b2", "knowledge_id": "k2"}]}
        hit = claims_conflict(a, b)
        assert hit is not None
        assert any(
            r in hit["reason_codes"]
            for r in ("numeric_mismatch", "numeric_unit_mismatch")
        )
        assert hit["evidence_a"] and hit["evidence_b"]

    def test_no_conflict_same_statement(self):
        a = {"claim_id": "c1", "text": "FTTR 支持千兆"}
        b = {"claim_id": "c2", "text": "FTTR 支持千兆"}
        assert claims_conflict(a, b) is None

    def test_detect_pairs(self):
        rows = [
            {"claim_id": "c1", "source": "verified_claim", "text": "速率 1Gbps", "evidence": []},
            {"claim_id": "c2", "source": "verified_claim", "text": "速率 100Mbps", "evidence": []},
            {"claim_id": "c3", "source": "verified_claim", "text": "完全无关的主题说明", "evidence": []},
        ]
        conflicts = detect_claim_conflicts(rows)
        assert len(conflicts) >= 1


class TestFreshness:
    def test_freshness_query(self):
        assert is_freshness_sensitive_query("当前最新的规格是什么")
        assert not is_freshness_sensitive_query("FTTR 定义是什么")

    def test_filter_stale(self):
        kept, dropped = filter_stale_claims([
            {"claim_id": "a", "freshness": "current", "evidence": [{"stale": False}]},
            {"claim_id": "b", "freshness": "stale_partial", "evidence": [{"stale": True}]},
        ])
        assert [c["claim_id"] for c in kept] == ["a"]
        assert [c["claim_id"] for c in dropped] == ["b"]


class TestAnswerAssembly:
    def test_conflict_disclosure_no_single_side(self):
        results = [
            {
                "source": "verified_claim",
                "candidate_type": "claim",
                "claim_id": "c1",
                "text": "峰值 1Gbps",
                "evidence": [{"knowledge_id": "k1", "block_id": "b1", "stance": "supports"}],
                "status": "active",
            },
            {
                "source": "verified_claim",
                "candidate_type": "claim",
                "claim_id": "c2",
                "text": "峰值 100Mbps",
                "evidence": [{"knowledge_id": "k2", "block_id": "b2", "stance": "supports"}],
                "status": "active",
            },
        ]
        payload = assemble_answer_payload("峰值是多少", results)
        assert payload["answer_mode"] == ANSWER_MODE_CONFLICT
        assert payload["conflict_disclosed"] is True
        assert payload["conflicts"]
        # Must not look like a single definitive pick of one number only
        assert "不会自动选择" in payload["answer"] or "分歧" in payload["answer"]
        assert payload["claims_used"]

    def test_hybrid_requires_evidence_on_claims(self):
        results = [
            {
                "source": "verified_claim",
                "claim_id": "c1",
                "text": "FTTR 定义是光纤到房间",
                "evidence": [{"knowledge_id": "k1", "block_id": "b1", "stance": "supports"}],
                "status": "active",
            },
            {
                "source": "knowledge",
                "candidate_type": "raw_block",
                "knowledge_id": "k1",
                "block_id": "b1",
                "title": "手册",
                "text": "光纤到房间",
            },
        ]
        payload = assemble_answer_payload("什么是 FTTR", results)
        assert payload["answer_mode"] == ANSWER_MODE_HYBRID
        assert payload["claims_used"]
        assert all(c.get("evidence") for c in payload["claims_used"])
        assert payload["raw_evidence_used"]

    def test_raw_only(self):
        results = [
            {
                "source": "knowledge",
                "candidate_type": "raw_block",
                "knowledge_id": "k1",
                "block_id": "b1",
                "title": "doc",
                "text": "原始段落",
            },
        ]
        payload = assemble_answer_payload("问题", results)
        assert payload["answer_mode"] == ANSWER_MODE_RAW
        assert not payload["claims_used"]
        assert payload["raw_evidence_used"]

    def test_no_answer(self):
        payload = assemble_answer_payload("完全没有资料的问题", [])
        assert payload["answer_mode"] == ANSWER_MODE_NO_ANSWER
        assert "未找到" in payload["answer"] or "充分证据" in payload["answer"]

    def test_stale_excluded_on_freshness_query(self):
        results = [
            {
                "source": "verified_claim",
                "claim_id": "old",
                "text": "现行指标 100Mbps",
                "freshness": "stale_partial",
                "evidence": [{"knowledge_id": "k1", "block_id": "b1", "stale": True}],
            },
        ]
        payload = assemble_answer_payload("当前最新指标是多少", results)
        # stale claim dropped → no_answer (no raw either)
        assert payload["answer_mode"] in (ANSWER_MODE_NO_ANSWER, ANSWER_MODE_RAW)
        assert any("stale" in w for w in payload["warnings"])


class TestClaimCitationBuilder:
    def test_build_claim_citation_has_evidence_chain(self):
        builder = CitationBuilder(db=None)
        cit = builder.build_claim_citation({
            "claim_id": "c9",
            "text": "statement",
            "status": "active",
            "score": 0.8,
            "evidence": [{
                "knowledge_id": "k1",
                "block_id": "b1",
                "stance": "supports",
                "stale": False,
            }],
        })
        assert cit["citation_layer"] == "claim"
        assert cit["claim_id"] == "c9"
        assert cit["evidence"][0]["block_id"] == "b1"
        assert cit["knowledge_id"] == "k1"

    def test_build_claim_citations_helper(self):
        cites = build_claim_citations([{
            "source": "verified_claim",
            "claim_id": "c1",
            "text": "x",
            "evidence": [{"knowledge_id": "k", "block_id": "b", "stance": "supports"}],
        }])
        assert len(cites) == 1
        assert cites[0]["evidence"]
