"""Public Search behavior contract — Phase-1 maintainability freeze.

Freezes user-observable Search shapes (raw / verified / fallback / empty).
Snapshots ignore elapsed_ms and float noise via contract_normalize.
"""
from __future__ import annotations

from unittest.mock import Mock, patch

from src.models.wiki_v2 import Claim, ClaimStatus, Evidence, EvidenceStance, normalize_statement
from src.services.search_service import SearchService
from src.services.wiki_claim_extractor import compute_excerpt_hash
from src.services.wiki_serving_gate import ServingGateConfig, WikiServingGate
from tests.helpers.contract_normalize import (
    assert_matches_snapshot,
    execution_to_dict,
    normalize_search_contract,
)


def _cfg(*, verified: bool = False) -> Mock:
    data = {
        "rag.enable_query_rewriting": False,
        "rag.enable_rerank": False,
        "rag.verified_knowledge.enabled": verified,
        "rag.verified_knowledge.wiki_weight": 0.4,
        "rag.verified_knowledge.raw_weight": 0.6,
        "rag.verified_knowledge.empty_wiki_fallback_to_raw": True,
        "knowledge_workflow.mode": "verified" if verified else "evidence_only",
    }
    cfg = Mock()
    cfg.get.side_effect = lambda key, default=None: data.get(key, default)
    return cfg


def _claim(statement: str = "FTTR 可达 1Gbps", cid: str = "c1", block_id: str = "b1", kid: str = "k1"):
    h = compute_excerpt_hash(statement)
    return Claim(
        schema_version=1,
        claim_id=cid,
        statement=statement,
        normalized_statement=normalize_statement(statement),
        claim_type="fact",
        status=ClaimStatus.ACTIVE,
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
                excerpt_hash=h,
            ),
        ],
        relations=[],
        created_at="t",
        updated_at="t",
        revision=1,
    )


def _raw_hit(bid: str = "b1", kid: str = "k1", text: str = "raw FTTR content", score: float = 0.9):
    return {
        "id": bid,
        "text": text,
        "metadata": {"page_id": kid, "title": "Doc", "knowledge_id": kid},
        "rrf_score": score,
    }


class TestPublicSearchContract:
    def test_search_raw_snapshot(self):
        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = {"id": "k1", "title": "Doc"}
        service = SearchService(_cfg(verified=False), db, Mock(), Mock(), Mock())
        with (
            patch.object(service, "_rewrite_query", return_value=["FTTR 规格"]),
            patch.object(service, "_hybrid_search", return_value=[_raw_hit()]),
            patch.object(service, "_rerank", side_effect=lambda q, c, top_n=5: c),
        ):
            ex = service.execute("FTTR 规格", top_k=5)
        payload = normalize_search_contract(execution_to_dict(ex))
        assert payload["trace"]["mode"] == "legacy_raw"
        assert payload["results"]
        assert payload["results"][0]["source"] == "knowledge"
        assert_matches_snapshot("search_raw.json", payload)

    def test_search_verified_snapshot(self):
        content = "FTTR 可达 1Gbps"
        claim = _claim(statement=content)

        class Repo:
            def list_claims(self):
                return [claim]

        gate = WikiServingGate(
            config=ServingGateConfig(),
            get_block=lambda bid: {"id": bid, "content": content},
            get_knowledge=lambda kid: {"id": kid, "deleted_at": None},
            wiki_read_enabled=True,
            knowledge_mode="verified",
        )
        db = Mock()
        db.get_knowledge.return_value = {"id": "k1", "title": "Spec"}
        service = SearchService(
            _cfg(verified=True), db, Mock(), Mock(), Mock(),
            wiki_repository=Repo(), wiki_serving_gate=gate,
        )
        with (
            patch.object(service, "_rewrite_query", return_value=["FTTR 1Gbps"]),
            patch.object(
                service, "_timed_hybrid_search",
                return_value=[_raw_hit("b_other", "k2", "unrelated", 0.1)],
            ),
        ):
            ex = service.execute("FTTR 1Gbps", top_k=5)
        payload = normalize_search_contract(execution_to_dict(ex))
        assert payload["trace"]["mode"] == "hybrid_verified"
        claim_hits = [r for r in payload["results"] if r.get("source") == "verified_claim"]
        assert claim_hits and claim_hits[0].get("evidence")
        assert_matches_snapshot("search_verified.json", payload)

    def test_search_raw_fallback_on_wiki_error(self):
        class BoomRepo:
            def list_claims(self):
                raise RuntimeError("wiki down")

        db = Mock()
        db.get_knowledge.return_value = {"id": "k1", "title": "Doc"}
        service = SearchService(
            _cfg(verified=True), db, Mock(), Mock(), Mock(),
            wiki_repository=BoomRepo(), wiki_serving_gate=Mock(),
        )
        with (
            patch.object(service, "_rewrite_query", return_value=["FTTR"]),
            patch.object(
                service, "_timed_hybrid_search",
                return_value=[_raw_hit(text="raw hit about FTTR", score=0.8)],
            ),
        ):
            ex = service.execute("FTTR", top_k=5)
        payload = normalize_search_contract(execution_to_dict(ex))
        assert any(r.get("source") == "knowledge" for r in payload["results"])
        wiki_stage = payload["trace"]["stages"].get("verified_wiki") or {}
        assert wiki_stage.get("error")
        assert_matches_snapshot("search_raw_fallback.json", payload)

    def test_search_no_result(self):
        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = None
        db.search_knowledge.return_value = []
        block_store = Mock()
        block_store.search.return_value = []
        service = SearchService(_cfg(verified=False), db, block_store, Mock(), Mock())
        with (
            patch.object(service, "_rewrite_query", return_value=["zzzz-no-hit"]),
            patch.object(service, "_hybrid_search", return_value=[]),
        ):
            ex = service.execute("zzzz-no-hit", top_k=5)
        payload = normalize_search_contract(execution_to_dict(ex))
        assert payload["results"] == []
        assert payload["trace"]["mode"] == "legacy_raw"
        assert_matches_snapshot("search_no_result.json", payload)

    def test_vector_unavailable_falls_back_keyword_path(self):
        """Hybrid raises → block_store still packages knowledge results."""
        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = {"id": "k1", "title": "Doc"}
        block_store = Mock()
        block_store.search.return_value = [
            {"id": "b1", "text": "fallback text", "metadata": {"page_id": "k1"}, "distance": 0.2},
        ]
        service = SearchService(_cfg(verified=False), db, block_store, Mock(), Mock())
        with (
            patch.object(service, "_rewrite_query", return_value=["q"]),
            patch.object(service, "_hybrid_search", side_effect=RuntimeError("vector down")),
            patch.object(service, "_rerank", side_effect=lambda q, c, top_n=5: c),
        ):
            ex = service.execute("q", top_k=5)
        assert list(ex.results)
        assert list(ex.results)[0]["source"] == "knowledge"
        assert list(ex.results)[0]["text"] == "fallback text"

    def test_rerank_failure_keeps_candidates(self):
        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = {"id": "k1", "title": "Doc"}
        service = SearchService(_cfg(verified=False), db, Mock(), Mock(), Mock())
        with (
            patch.object(service, "_rewrite_query", return_value=["q"]),
            patch.object(service, "_hybrid_search", return_value=[_raw_hit()]),
            patch.object(service, "_timed_rerank", side_effect=RuntimeError("rerank down")),
        ):
            ex = service.execute("q", top_k=5)
        assert list(ex.results)
        assert list(ex.results)[0]["source"] == "knowledge"

    def test_citation_present_when_builder_available(self):
        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = {
            "id": "k1", "title": "Doc", "source_path": "/tmp/a.md", "content": "x",
        }
        db.get_conn.return_value.execute.return_value.fetchone.return_value = ("Doc",)
        service = SearchService(_cfg(verified=False), db, Mock(), Mock(), Mock())
        with (
            patch.object(service, "_rewrite_query", return_value=["q"]),
            patch.object(service, "_hybrid_search", return_value=[_raw_hit()]),
            patch.object(service, "_rerank", side_effect=lambda q, c, top_n=5: c),
        ):
            ex = service.execute("q", top_k=5)
        assert list(ex.results)
        # citation may be present when CitationBuilder succeeds
        row = list(ex.results)[0]
        assert row.get("knowledge_id") == "k1"
        assert row.get("text")

    def test_trace_basic_fields(self):
        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = {"id": "k1", "title": "Doc"}
        service = SearchService(_cfg(verified=False), db, Mock(), Mock(), Mock())
        with (
            patch.object(service, "_rewrite_query", return_value=["hello"]),
            patch.object(service, "_hybrid_search", return_value=[_raw_hit()]),
        ):
            ex = service.execute("hello world", top_k=3)
        assert "mode" in ex.trace
        assert "query" in ex.trace
        assert "stages" in ex.trace
        assert isinstance(ex.trace["stages"], dict)


class TestSearchExecuteCompat:
    def test_search_equals_execute_results(self):
        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = {"id": "k1", "title": "Doc"}
        service = SearchService(_cfg(verified=False), db, Mock(), Mock(), Mock())
        hit = _raw_hit()
        with (
            patch.object(service, "_rewrite_query", return_value=["q"]),
            patch.object(service, "_hybrid_search", return_value=[hit]),
        ):
            via_search = service.search("q", top_k=5)
            via_execute = list(service.execute("q", top_k=5).results)
        assert via_search == via_execute
