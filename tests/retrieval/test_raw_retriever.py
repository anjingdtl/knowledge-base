"""RawRetriever adapter boundary tests."""
from unittest.mock import Mock, patch

from src.retrieval.raw_retriever import RawRetriever
from src.services.search_service import SearchService


def test_raw_retriever_returns_tuple_candidates():
    config = Mock()
    config.get.return_value = False
    db = Mock()
    db.search_wiki_fts.return_value = []
    db.get_knowledge.return_value = {"title": "T"}
    service = SearchService(config, db, Mock(), Mock(), Mock())

    with patch.object(service, "_rewrite_query", return_value=["q"]), \
         patch.object(
             service,
             "_hybrid_search",
             return_value=[{
                 "id": "b1",
                 "text": "hello",
                 "metadata": {"page_id": "k1"},
                 "rrf_score": 0.9,
             }],
         ), \
         patch.object(
             service,
             "_rerank",
             return_value=[{
                 "id": "b1",
                 "text": "hello",
                 "metadata": {"page_id": "k1"},
                 "rerank_score": 0.95,
             }],
         ):
        result = RawRetriever(service).retrieve("q", top_k=3)

    assert isinstance(result.candidates, tuple)
    assert len(result.candidates) >= 1
    assert result.candidates[0]["source"] == "knowledge"
    assert "stages" in result.trace


def test_raw_retriever_can_strip_legacy_wiki_fts():
    config = Mock()
    config.get.return_value = False
    db = Mock()
    db.search_wiki_fts.return_value = [
        {"title": "Wiki", "concept_summary": "s", "content": "c", "id": "w1"},
    ]
    db.get_knowledge.return_value = {"title": "T"}
    service = SearchService(config, db, Mock(), Mock(), Mock())

    with patch.object(service, "_rewrite_query", return_value=["q"]), \
         patch.object(service, "_hybrid_search", return_value=[]), \
         patch.object(service, "_knowledge_fts_search", return_value=[]):
        full = RawRetriever(service).retrieve("q", include_legacy_wiki_fts=True)
        stripped = RawRetriever(service).retrieve("q", include_legacy_wiki_fts=False)

    assert any(r.get("source") == "wiki" for r in full.candidates)
    assert not any(r.get("source") == "wiki" for r in stripped.candidates)
