"""RawRetriever independent boundary tests (WP1-T1)."""
from unittest.mock import Mock, patch

from src.retrieval.raw_retriever import RawRetriever


def _make_raw(**kwargs) -> RawRetriever:
    config = Mock()
    config.get.return_value = False
    db = Mock()
    db.search_wiki_fts.return_value = []
    db.get_knowledge.return_value = {"title": "T"}
    defaults = {
        "config": config,
        "db": db,
        "block_store": Mock(),
        "llm": Mock(),
    }
    defaults.update(kwargs)
    return RawRetriever(**defaults)


def test_raw_retriever_returns_tuple_candidates():
    raw = _make_raw()
    hit = {
        "id": "b1",
        "text": "hello",
        "metadata": {"page_id": "k1"},
        "rrf_score": 0.9,
    }

    with patch.object(raw, "rewrite_query", return_value=["q"]), \
         patch.object(raw, "hybrid_search", return_value=[hit]), \
         patch.object(
             raw,
             "rerank",
             return_value=[{**hit, "rerank_score": 0.95}],
         ):
        result = raw.retrieve("q", top_k=3)

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
    raw = RawRetriever(config=config, db=db, block_store=Mock(), llm=Mock())

    with patch.object(raw, "rewrite_query", return_value=["q"]), \
         patch.object(raw, "hybrid_search", return_value=[]), \
         patch.object(raw, "knowledge_fts_search", return_value=[]):
        full = raw.retrieve("q", include_legacy_wiki_fts=True)
        stripped = raw.retrieve("q", include_legacy_wiki_fts=False)

    assert any(r.get("source") == "wiki" for r in full.candidates)
    assert not any(r.get("source") == "wiki" for r in stripped.candidates)


def test_raw_retriever_does_not_accept_search_service():
    """Constructor is explicit deps only — no SearchService parameter."""
    import inspect

    sig = inspect.signature(RawRetriever.__init__)
    assert "search_service" not in sig.parameters
