"""EvidenceOnly / Verified policy tests."""
from unittest.mock import Mock, patch

from src.models.search_execution import SearchExecution
from src.retrieval.policies.evidence_only import EvidenceOnlyPolicy
from src.retrieval.policies.verified import VerifiedPolicy
from src.services.search_service import SearchService


def _service(verified: bool = False) -> SearchService:
    config = Mock()

    def _get(key, default=None):
        mapping = {
            "rag.enable_query_rewriting": False,
            "rag.enable_rerank": False,
            "rag.verified_knowledge.enabled": verified,
        }
        return mapping.get(key, default)

    config.get.side_effect = _get
    db = Mock()
    db.search_wiki_fts.return_value = []
    db.get_knowledge.return_value = {"title": "T"}
    wiki_repo = Mock() if verified else None
    return SearchService(
        config, db, Mock(), Mock(), Mock(),
        wiki_repository=wiki_repo,
        wiki_serving_gate=Mock(),
    )


def test_evidence_only_policy_returns_search_execution():
    svc = _service(verified=False)
    with patch.object(svc, "_should_use_verified_hybrid", return_value=False), \
         patch.object(svc, "_rewrite_query", return_value=["q"]), \
         patch.object(
             svc,
             "_hybrid_search",
             return_value=[{
                 "id": "b1",
                 "text": "t",
                 "metadata": {"page_id": "k1"},
                 "rrf_score": 0.5,
             }],
         ), \
         patch.object(
             svc,
             "_rerank",
             side_effect=lambda q, c, top_k: c,
         ):
        ex = EvidenceOnlyPolicy(svc).execute("q", top_k=3)
    assert isinstance(ex, SearchExecution)
    assert len(ex.results) >= 1


def test_verified_policy_empty_wiki_still_returns_raw():
    svc = _service(verified=True)
    raw_hit = {
        "id": "b1",
        "text": "raw hit",
        "metadata": {"page_id": "k1", "title": "T"},
        "rrf_score": 0.8,
    }
    with patch.object(svc, "_should_use_verified_hybrid", return_value=True), \
         patch.object(svc, "_rewrite_query", return_value=["q"]), \
         patch.object(svc, "_timed_hybrid_search", return_value=[raw_hit]), \
         patch.object(svc, "_timed_rerank", side_effect=lambda q, c, top_k: c), \
         patch.object(svc, "_safe_verified_claim_retrieve", return_value=[]):
        ex = VerifiedPolicy(svc).execute("q", top_k=3)

    assert isinstance(ex, SearchExecution)
    assert len(ex.results) >= 1
    # empty wiki should record fallback
    reasons = [f.get("reason") for f in ex.fallbacks if isinstance(f, dict)]
    stage_fb = (ex.trace.get("stages") or {}).get("fallback")
    assert "empty_wiki_to_raw" in reasons or stage_fb == "empty_wiki_to_raw"


def test_verified_policy_claim_error_does_not_raise():
    svc = _service(verified=True)
    raw_hit = {
        "id": "b1",
        "text": "raw hit",
        "metadata": {"page_id": "k1", "title": "T"},
        "rrf_score": 0.8,
    }

    def _retrieve(query, limit, state=None):
        if state is not None:
            state.claim_error = "wiki_repo_error"
        return []

    with patch.object(svc, "_should_use_verified_hybrid", return_value=True), \
         patch.object(svc, "_rewrite_query", return_value=["q"]), \
         patch.object(svc, "_timed_hybrid_search", return_value=[raw_hit]), \
         patch.object(svc, "_timed_rerank", side_effect=lambda q, c, top_k: c), \
         patch.object(svc, "_safe_verified_claim_retrieve", side_effect=_retrieve):
        ex = VerifiedPolicy(svc).execute("q", top_k=3)

    assert isinstance(ex, SearchExecution)
    assert len(ex.results) >= 1
