"""Independent RawRetriever is the algorithm authority (WP5)."""
from unittest.mock import Mock, patch

from src.services.search_service import SearchService


def _make_service() -> SearchService:
    config = Mock()
    config.get.return_value = False
    db = Mock()
    db.search_wiki_fts.return_value = []
    db.get_knowledge.return_value = {"title": "Title"}
    return SearchService(config, db, Mock(), Mock(), Mock())


def test_raw_retriever_produces_packaged_candidates():
    service = _make_service()
    hit = {
        "id": "b1",
        "text": "hello world",
        "metadata": {"page_id": "k1"},
        "rrf_score": 0.9,
    }
    raw = service._get_raw_retriever()
    with patch.object(raw, "rewrite_query", return_value=["q"]), \
         patch.object(raw, "hybrid_search", return_value=[hit]), \
         patch.object(
             raw,
             "rerank",
             return_value=[{**hit, "rerank_score": 0.95}],
         ):
        via_raw = raw.retrieve("q", top_k=3)

    assert len(via_raw.candidates) >= 1
    assert "query_rewrite" in via_raw.trace.get("stages", {})
    assert "raw_retrieval" in via_raw.trace.get("stages", {})


def test_evidence_only_policy_uses_raw_retriever_candidates():
    service = _make_service()
    hit = {
        "id": "b2",
        "text": "evidence path",
        "metadata": {"page_id": "k2"},
        "rrf_score": 0.8,
    }
    raw = service._get_raw_retriever()
    with patch.object(raw, "rewrite_query", return_value=["q"]), \
         patch.object(raw, "hybrid_search", return_value=[hit]), \
         patch.object(raw, "rerank", return_value=[{**hit, "rerank_score": 0.9}]):
        exec_result = service.execute_evidence_only("q", top_k=3)

    assert len(exec_result.results) >= 1
    assert exec_result.results[0].get("source") == "knowledge"
