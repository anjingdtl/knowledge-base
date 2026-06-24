"""SearchService 单元测试 — 验证完整搜索管线"""
from unittest.mock import Mock, patch

from src.services.search_service import SearchService


class TestSearchService:
    def test_search_calls_rewrite_hybrid_rerank(self):
        """验证管线各阶段被调用"""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "rag.enable_query_rewriting": True,
            "rag.enable_rerank": True,
        }.get(key, default)

        db = Mock()
        db.get_knowledge.return_value = {"title": "测试标题"}
        db.search_wiki_fts.return_value = []

        block_store = Mock()
        embedding = Mock()
        llm = Mock()

        service = SearchService(config, db, block_store, embedding, llm)

        with patch.object(service, '_rewrite_query', return_value=["query", "rewrite1"]) as mock_rewrite, \
             patch.object(service, '_hybrid_search', return_value=[{"id": "b1", "text": "text", "metadata": {"page_id": "k1"}, "rrf_score": 0.9}]) as mock_hybrid, \
             patch.object(service, '_rerank', return_value=[{"id": "b1", "text": "text", "metadata": {"page_id": "k1"}, "rerank_score": 0.95}]) as mock_rerank:

            results = service.search("test query", top_k=5)

            mock_rewrite.assert_called_once_with("test query")
            mock_hybrid.assert_called_once_with(["query", "rewrite1"], 5)
            mock_rerank.assert_called_once()
            assert len(results) == 1
            assert results[0]["source"] == "knowledge"
            assert results[0]["knowledge_id"] == "k1"

    def test_search_wiki_priority(self):
        """Wiki 结果排在前面"""
        config = Mock()
        config.get.return_value = False

        db = Mock()
        db.search_wiki_fts.return_value = [
            {"title": "Wiki Page", "concept_summary": "summary", "content": "content", "id": "w1"}
        ]
        db.get_knowledge.return_value = {"title": "Knowledge"}

        block_store = Mock()
        embedding = Mock()
        llm = Mock()

        service = SearchService(config, db, block_store, embedding, llm)

        with patch.object(service, '_rewrite_query', return_value=["query"]), \
             patch.object(service, '_hybrid_search', return_value=[{"id": "b1", "text": "text", "metadata": {"page_id": "k1"}, "rrf_score": 0.9}]), \
             patch.object(service, '_rerank', return_value=[{"id": "b1", "text": "text", "metadata": {"page_id": "k1"}, "rerank_score": 0.95}]):

            results = service.search("test", top_k=5)

            assert len(results) == 2
            assert results[0]["source"] == "wiki"
            assert results[1]["source"] == "knowledge"

    def test_search_fallback_to_block_store(self):
        """HybridSearcher 失败时回退 BlockStore"""
        config = Mock()
        config.get.return_value = False

        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = {"title": "Test"}

        block_store = Mock()
        block_store.search.return_value = [
            {"id": "b1", "text": "fallback text", "metadata": {"page_id": "k1"}, "distance": 0.2}
        ]

        embedding = Mock()
        llm = Mock()

        service = SearchService(config, db, block_store, embedding, llm)

        with patch.object(service, '_rewrite_query', return_value=["query"]), \
             patch.object(service, '_hybrid_search', side_effect=Exception("Hybrid failed")), \
             patch.object(service, '_rerank', return_value=[{"id": "b1", "text": "fallback text", "metadata": {"page_id": "k1"}, "distance": 0.2}]):

            results = service.search("test", top_k=5)

            block_store.search.assert_called_once_with("test", top_k=5)
            assert len(results) == 1
            assert results[0]["text"] == "fallback text"

    def test_search_returns_correct_structure(self):
        """返回结构包含 source, score, knowledge_id"""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "rag.enable_rerank": True,
            "rag.title_boost": 0,  # disable title boost for deterministic test scores
        }.get(key, default)

        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = {"title": "Test Title"}

        block_store = Mock()
        embedding = Mock()
        llm = Mock()

        service = SearchService(config, db, block_store, embedding, llm)

        with patch.object(service, '_rewrite_query', return_value=["query"]), \
             patch.object(service, '_hybrid_search', return_value=[{"id": "b1", "text": "text", "metadata": {"page_id": "k1"}, "rrf_score": 0.85}]), \
             patch.object(service, '_rerank', return_value=[{"id": "b1", "text": "text", "metadata": {"page_id": "k1"}, "rerank_score": 0.9}]):

            results = service.search("test", top_k=5)

            assert len(results) == 1
            r = results[0]
            assert "source" in r
            assert "score" in r
            assert "knowledge_id" in r
            assert "title" in r
            assert "text" in r
            assert r["source"] == "knowledge"
            assert r["score"] == 0.9
            assert r["title"] == "Test Title"
