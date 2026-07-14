"""WP1-T2 fusion + packaging unit tests."""
from unittest.mock import Mock

from src.retrieval.fusion import VerifiedFusion
from src.retrieval.models import RawRetrievalResult
from src.retrieval.packaging import (
    SearchRequestState,
    build_evidence_only_execution,
    to_execution,
)
from src.retrieval.raw_retriever import RawRetriever


def test_build_evidence_only_execution_preserves_candidates():
    raw = RawRetrievalResult(
        candidates=({"source": "knowledge", "knowledge_id": "k1"},),
        trace={"mode": "legacy_raw", "stages": {"raw_retrieval": {"count": 1}}},
        warnings=("rerank_timeout",),
    )
    ex = build_evidence_only_execution(raw)
    assert len(ex.results) == 1
    assert ex.trace["mode"] == "legacy_raw"
    assert "rerank_timeout" in ex.warnings


def test_to_execution_includes_conflicts():
    state = SearchRequestState(trace={"mode": "hybrid_verified"})
    state.conflicts = [{"conflict_id": "c1"}]
    ex = to_execution([{"source": "knowledge"}], state)
    assert len(ex.conflicts) == 1


def test_verified_fusion_empty_wiki_records_fallback():
    fusion = VerifiedFusion(
        config=Mock(),
        db=Mock(get_knowledge=Mock(return_value={"title": "T"})),
        block_store=None,
        stage_timeout_fn=lambda s: 5.0,
        verified_cfg_fn=lambda k, d=None: {
            "wiki_weight": 0.4,
            "raw_weight": 0.6,
            "empty_wiki_fallback_to_raw": True,
            "raw_candidate_multiplier": 3,
            "wiki_candidate_multiplier": 2,
        }.get(k, d),
        rewrite_fn=lambda q: [q],
        timed_hybrid_fn=lambda qs, k: [{
            "id": "b1",
            "text": "raw",
            "metadata": {"page_id": "k1", "title": "T"},
            "rrf_score": 0.9,
        }],
        timed_rerank_fn=lambda q, c, k: c,
        diversity_fn=lambda c, threshold=0.8: c,
        knowledge_fts_fn=lambda q, k: [],
        claim_retrieve_fn=lambda q, limit, state: [],
    )
    state = SearchRequestState(trace={"mode": "x", "stages": {}})
    out = fusion.run("q", top_k=3, state=state)
    assert len(out) >= 1
    reasons = [f.get("reason") for f in state.fallbacks if isinstance(f, dict)]
    assert "empty_wiki_to_raw" in reasons or (
        (state.trace.get("stages") or {}).get("fallback") == "empty_wiki_to_raw"
    )


def test_raw_retriever_has_no_search_service_param():
    import inspect

    params = inspect.signature(RawRetriever.__init__).parameters
    assert "search_service" not in params
