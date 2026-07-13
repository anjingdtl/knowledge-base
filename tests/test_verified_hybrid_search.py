"""Phase 3: verified hybrid routing, fusion, SearchService integration."""
from __future__ import annotations

from unittest.mock import Mock

from src.models.wiki_v2 import Claim, ClaimStatus, Evidence, EvidenceStance, normalize_statement
from src.services.search_service import SearchService
from src.services.verified_hybrid_fusion import (
    fuse_verified_and_raw,
    normalize_claim_candidate,
)
from src.services.verified_query_router import route_query
from src.services.wiki_claim_extractor import compute_excerpt_hash
from src.services.wiki_serving_gate import WikiServingGate


def _claim(
    cid: str = "c1",
    statement: str = "FTTR 可达 1Gbps",
    *,
    status: ClaimStatus = ClaimStatus.ACTIVE,
    block_id: str = "b1",
    kid: str = "k1",
    excerpt_hash: str | None = None,
) -> Claim:
    return Claim(
        schema_version=1,
        claim_id=cid,
        statement=statement,
        normalized_statement=normalize_statement(statement),
        claim_type="fact",
        status=status,
        confidence=0.9,
        valid_from=None,
        valid_to=None,
        subject_refs=[],
        predicate="p",
        object_refs=[],
        evidence=[
            Evidence(
                evidence_id=f"ev_{cid}",
                stance=EvidenceStance.SUPPORTS,
                knowledge_id=kid,
                block_id=block_id,
                excerpt_hash=excerpt_hash,
            ),
        ],
        relations=[],
        created_at="t",
        updated_at="t",
        revision=1,
    )


class TestQueryRouter:
    def test_definition_prefers_wiki(self):
        d = route_query("什么是 FTTR")
        assert d.intent == "definition"
        assert d.wiki_weight >= d.raw_weight

    def test_exact_prefers_raw(self):
        d = route_query("第 12 页的具体数值是多少")
        assert d.intent in ("exact_lookup", "document_location")
        assert d.raw_weight > d.wiki_weight

    def test_freshness_lowers_wiki(self):
        d = route_query("当前最新的 FTTR 指标")
        assert d.freshness_sensitive is True
        assert d.raw_weight >= d.wiki_weight


class TestFusion:
    def test_claim_must_carry_evidence(self):
        content = "FTTR 可达 1Gbps"
        h = compute_excerpt_hash(content)
        claim = _claim(excerpt_hash=h, statement=content)
        gate = WikiServingGate(
            get_block=lambda bid: {"id": bid, "content": content},
            get_knowledge=lambda kid: {"id": kid, "deleted_at": None},
            wiki_read_enabled=True,
        )
        decision = gate.evaluate(claim)
        assert decision.eligible
        cand = normalize_claim_candidate(claim, decision, query="FTTR")
        assert cand["evidence"]
        assert cand["evidence"][0]["block_id"] == "b1"
        assert cand["candidate_type"] == "claim"

    def test_fuse_rrf_not_score_add(self):
        claims = [{
            "id": "claim:c1",
            "candidate_type": "claim",
            "candidate_id": "c1",
            "score": 0.9,
            "match_channels": ["verified_wiki"],
            "evidence": [{"block_id": "b9", "knowledge_id": "k1", "ok": True}],
            "block_id": "b9",
        }]
        raw = [{
            "id": "b1",
            "candidate_type": "raw_block",
            "candidate_id": "b1",
            "score": 0.99,
            "match_channels": ["raw"],
            "block_id": "b1",
        }]
        fused = fuse_verified_and_raw(claims, raw, wiki_weight=0.4, raw_weight=0.6, top_n=10)
        assert fused
        assert all("rrf_score" in c for c in fused)

    def test_dedupe_claim_evidence_block(self):
        claims = [{
            "id": "claim:c1",
            "candidate_type": "claim",
            "candidate_id": "c1",
            "score": 0.8,
            "match_channels": ["verified_wiki"],
            "evidence": [{"block_id": "b1", "ok": True}],
            "block_id": "b1",
        }]
        raw = [{
            "id": "b1",
            "candidate_type": "raw_block",
            "candidate_id": "b1",
            "score": 0.7,
            "match_channels": ["raw"],
            "block_id": "b1",
        }]
        fused = fuse_verified_and_raw(claims, raw, wiki_weight=0.5, raw_weight=0.5)
        ids = [c["id"] for c in fused]
        assert "claim:c1" in ids
        assert "b1" not in ids  # evidence block de-duped


class TestSearchServiceVerifiedHybrid:
    def _config(self, enabled=True, mode=None):
        cfg = Mock()
        data = {
            "rag.verified_knowledge.raw_weight": 0.6,
            "rag.verified_knowledge.wiki_weight": 0.4,
            "rag.verified_knowledge.raw_candidate_multiplier": 2,
            "rag.verified_knowledge.wiki_candidate_multiplier": 2,
            "rag.verified_knowledge.empty_wiki_fallback_to_raw": True,
            "rag.enable_query_rewriting": False,
            "rag.enable_rerank": False,
            "rag.title_boost": 0,
        }
        if enabled is not None:
            data["rag.verified_knowledge.enabled"] = enabled
        if mode is not None:
            data["knowledge_workflow.mode"] = mode
        cfg.get.side_effect = lambda key, default=None: data.get(key, default)
        return cfg

    def test_wiki_failure_does_not_block_raw(self):
        cfg = self._config(enabled=True)
        db = Mock()
        db.get_knowledge.return_value = {"title": "Doc", "id": "k1"}

        class BoomRepo:
            def list_claims(self):
                raise RuntimeError("wiki down")

        service = SearchService(
            cfg, db, Mock(), Mock(), Mock(),
            wiki_repository=BoomRepo(),
            wiki_serving_gate=Mock(),
        )

        with (
            __import__("unittest.mock", fromlist=["patch"]).patch.object(
                service, "_rewrite_query", return_value=["FTTR"],
            ),
            __import__("unittest.mock", fromlist=["patch"]).patch.object(
                service,
                "_timed_hybrid_search",
                return_value=[{
                    "id": "b1",
                    "text": "raw hit about FTTR",
                    "metadata": {"page_id": "k1", "title": "Doc"},
                    "rrf_score": 0.8,
                }],
            ),
        ):
            results = service.search("FTTR", top_k=5)

        assert results
        assert any(r.get("source") == "knowledge" for r in results)
        assert service.last_search_trace.get("mode") == "hybrid_verified"
        # wiki stage recorded error, raw still present
        wiki_stage = service.last_search_trace["stages"].get("verified_wiki") or {}
        assert wiki_stage.get("error")

    def test_verified_claim_in_results_with_evidence(self):
        content = "FTTR 可达 1Gbps"
        h = compute_excerpt_hash(content)
        claim = _claim(statement=content, excerpt_hash=h)

        class Repo:
            def list_claims(self):
                return [claim]

        gate = WikiServingGate(
            get_block=lambda bid: {"id": bid, "content": content},
            get_knowledge=lambda kid: {"id": kid, "deleted_at": None},
            wiki_read_enabled=True,
            knowledge_mode="verified",
        )
        cfg = self._config(enabled=True)
        db = Mock()
        db.get_knowledge.return_value = {"title": "Spec", "id": "k1"}

        service = SearchService(
            cfg, db, Mock(), Mock(), Mock(),
            wiki_repository=Repo(),
            wiki_serving_gate=gate,
        )

        with (
            __import__("unittest.mock", fromlist=["patch"]).patch.object(
                service, "_rewrite_query", return_value=["FTTR 1Gbps"],
            ),
            __import__("unittest.mock", fromlist=["patch"]).patch.object(
                service,
                "_timed_hybrid_search",
                return_value=[{
                    "id": "b_other",
                    "text": "unrelated raw",
                    "metadata": {"page_id": "k2", "title": "Other"},
                    "rrf_score": 0.1,
                }],
            ),
        ):
            results = service.search("FTTR 1Gbps", top_k=5)

        claim_hits = [r for r in results if r.get("source") == "verified_claim"]
        assert claim_hits, f"expected claim in {results}"
        assert claim_hits[0].get("evidence"), "claim must carry evidence"
        assert claim_hits[0]["evidence"][0].get("block_id")
        assert claim_hits[0].get("claim_id") == "c1"

    def test_evidence_only_disables_hybrid(self):
        cfg = self._config(enabled=True, mode="evidence_only")
        service = SearchService(
            cfg, Mock(), Mock(), Mock(), Mock(),
            wiki_repository=Mock(),
            wiki_serving_gate=Mock(),
        )
        assert service._should_use_verified_hybrid() is False

    def test_legacy_wiki_first_without_explicit_flag_uses_verified_hybrid(self):
        cfg = self._config(enabled=None, mode="wiki_first")
        service = SearchService(
            cfg, Mock(), Mock(), Mock(), Mock(),
            wiki_repository=Mock(), wiki_serving_gate=Mock(),
        )

        assert service._should_use_verified_hybrid() is True

    def test_legacy_path_when_flag_off(self):
        """Regression: existing tests path unchanged when fusion disabled."""
        cfg = self._config(enabled=False)
        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = {"title": "T"}
        service = SearchService(cfg, db, Mock(), Mock(), Mock())
        with (
            __import__("unittest.mock", fromlist=["patch"]).patch.object(
                service, "_rewrite_query", return_value=["q"],
            ),
            __import__("unittest.mock", fromlist=["patch"]).patch.object(
                service,
                "_hybrid_search",
                return_value=[{
                    "id": "b1",
                    "text": "t",
                    "metadata": {"page_id": "k1"},
                    "rrf_score": 0.9,
                }],
            ),
            __import__("unittest.mock", fromlist=["patch"]).patch.object(
                service,
                "_rerank",
                return_value=[{
                    "id": "b1",
                    "text": "t",
                    "metadata": {"page_id": "k1"},
                    "rerank_score": 0.95,
                }],
            ),
        ):
            results = service.search("q", top_k=5)
        assert results[0]["source"] == "knowledge"
        assert service.last_search_trace["mode"] == "legacy_raw"
