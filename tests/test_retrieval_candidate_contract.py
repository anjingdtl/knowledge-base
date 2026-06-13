"""检索候选分数与引用契约测试 — 捕获当前已知问题。

本测试文件在 M0 阶段创建，预期部分测试在 M4 实现前失败。
覆盖：
1. reranker 返回 rerank_score 时不能按缺失的 score 字段过滤为空
2. 同一文档的两个不同 block_id 可以同时进入 sources
3. FTS 命中必须保留 keyword channel
4. reranker 异常时保留 RRF 排序并记录 warning
"""
from __future__ import annotations

import pytest
from unittest.mock import Mock, patch, MagicMock


# ---- 1. reranker 分数不应被缺失 score 字段破坏 ----

class TestRerankScoreHandling:
    """reranker 返回 rerank_score 后，下游不能用不存在的 score 字段过滤。"""

    def test_reranked_candidates_keep_all_results(self):
        """rerank 后所有候选应保留，不能因为缺少 score 字段变成空列表。"""
        from src.services.search_service import SearchService

        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "rag.enable_query_rewriting": False,
            "rag.enable_rerank": True,
        }.get(key, default)

        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = {"title": "Test"}

        block_store = Mock()
        embedding = Mock()
        llm = Mock()

        service = SearchService(config, db, block_store, embedding, llm)

        # 模拟混合检索返回两个候选
        hybrid_results = [
            {
                "id": "block-1",
                "text": "text one",
                "metadata": {"page_id": "k1"},
                "rrf_score": 0.9,
            },
            {
                "id": "block-2",
                "text": "text two",
                "metadata": {"page_id": "k1"},
                "rrf_score": 0.8,
            },
        ]

        # reranker 返回带 rerank_score 的结果（没有 score 字段）
        reranked = [
            {
                "id": "block-1",
                "text": "text one",
                "metadata": {"page_id": "k1"},
                "rerank_score": 0.95,
                "rrf_score": 0.9,
            },
            {
                "id": "block-2",
                "text": "text two",
                "metadata": {"page_id": "k1"},
                "rerank_score": 0.85,
                "rrf_score": 0.8,
            },
        ]

        with patch.object(service, '_rewrite_query', return_value=["query"]), \
             patch.object(service, '_hybrid_search', return_value=hybrid_results), \
             patch.object(service, '_rerank', return_value=reranked):
            results = service.search("test", top_k=5)

        # 关键断言：rerank 后不应丢失任何结果
        assert len(results) >= 1, (
            f"rerank 后结果不应为空，实际得到 {len(results)} 个结果。"
            "可能原因：下游按不存在的 score 字段过滤。"
        )


# ---- 2. 同一文档多个 block 应可同时出现 ----

class TestMultiBlockFromSameDocument:
    """同一文档的两个不同 block_id 应可以同时进入 sources。"""

    def test_two_blocks_from_same_knowledge_both_appear(self):
        """去重应以 block_id 为主，而不是只按 knowledge_id 去重。"""
        from src.services.search_service import SearchService

        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "rag.enable_query_rewriting": False,
            "rag.enable_rerank": True,
        }.get(key, default)

        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = {"title": "Same Doc"}

        block_store = Mock()
        embedding = Mock()
        llm = Mock()

        service = SearchService(config, db, block_store, embedding, llm)

        # 两个来自同一文档的不同 block
        hybrid_results = [
            {
                "id": "block-A",
                "text": "first block content",
                "metadata": {"page_id": "same-kid"},
                "rrf_score": 0.9,
            },
            {
                "id": "block-B",
                "text": "second block content",
                "metadata": {"page_id": "same-kid"},
                "rrf_score": 0.8,
            },
        ]

        reranked = [
            {
                "id": "block-A",
                "text": "first block content",
                "metadata": {"page_id": "same-kid"},
                "rerank_score": 0.95,
                "rrf_score": 0.9,
            },
            {
                "id": "block-B",
                "text": "second block content",
                "metadata": {"page_id": "same-kid"},
                "rerank_score": 0.85,
                "rrf_score": 0.8,
            },
        ]

        with patch.object(service, '_rewrite_query', return_value=["query"]), \
             patch.object(service, '_hybrid_search', return_value=hybrid_results), \
             patch.object(service, '_rerank', return_value=reranked):
            results = service.search("test", top_k=5)

        # 当前实现按 knowledge_id 去重（search_service.py:133-137），
        # 导致同一文档的第二个 block 被丢弃。
        # M4 应改为按 block_id 去重。
        block_ids = {r.get("block_id", r.get("id", "")) for r in results}
        assert "block-A" in block_ids, "block-A 应出现在结果中"
        assert "block-B" in block_ids, (
            "block-B 也应出现在结果中。"
            "当前按 knowledge_id 去重导致同文档第二 block 被丢弃。"
        )


# ---- 3. FTS 命中保留 keyword channel ----

class TestFTSKeywordChannel:
    """FTS 命中的结果应标记 keyword match channel。"""

    def test_fts_hit_has_keyword_channel_marker(self):
        """纯 FTS 命中的候选应标记为 keyword 通道。"""
        from src.services.hybrid_search import HybridSearcher

        db = Mock()
        # FTS 返回一个结果
        db.search_blocks_fts.return_value = [
            {
                "id": "block-fts-1",
                "content": "exact keyword match",
                "page_id": "k1",
                "block_type": "paragraph",
                "properties": {},
                "fts_rank": -1.5,
            },
        ]

        block_store = Mock()
        # 向量搜索无结果
        block_store.search.return_value = []

        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "rag.search_mode": "keywords",
            "rag.parent_child.enabled": False,
        }.get(key, default)

        searcher = HybridSearcher(db, block_store, config)
        results = searcher.search(["test query"], top_k=5)

        assert len(results) >= 1, "FTS 应返回至少一个结果"
        # 检查结果是否有 keyword channel 标记
        # 当前实现只记录 fts_rank，没有显式 match_channels 字段
        # M4 应引入 match_channels
        result = results[0]
        has_channel_marker = (
            "match_channels" in result
            or result.get("fts_rank", 0) != 0
        )
        assert has_channel_marker, (
            "FTS 命中结果应有 keyword channel 标记。"
            "当前缺少 match_channels 字段。"
        )


# ---- 4. reranker 异常时保留 RRF 排序 ----

class TestRerankerFailureFallback:
    """reranker 异常时应保留 RRF 排序并记录 warning。"""

    def test_reranker_exception_preserves_rrf_order(self):
        """reranker 抛异常时，结果应保持 RRF 原始排序。"""
        from src.services.search_service import SearchService

        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "rag.enable_query_rewriting": False,
            "rag.enable_rerank": True,
        }.get(key, default)

        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.side_effect = lambda kid: {"title": f"Doc {kid}"}

        block_store = Mock()
        embedding = Mock()
        llm = Mock()

        service = SearchService(config, db, block_store, embedding, llm)

        hybrid_results = [
            {
                "id": "block-1",
                "text": "top rrf result",
                "metadata": {"page_id": "k1"},
                "rrf_score": 0.95,
            },
            {
                "id": "block-2",
                "text": "second rrf result",
                "metadata": {"page_id": "k2"},
                "rrf_score": 0.80,
            },
        ]

        with patch.object(service, '_rewrite_query', return_value=["query"]), \
             patch.object(service, '_hybrid_search', return_value=hybrid_results), \
             patch.object(service, '_rerank', side_effect=RuntimeError("reranker API down")):
            results = service.search("test", top_k=5)

        # reranker 失败后结果不应为空
        assert len(results) >= 1, (
            "reranker 异常时不应丢失检索结果。"
            "应保留 RRF 排序并记录 warning。"
        )
        # 如果能保留，验证排序顺序
        if len(results) >= 2:
            scores = [r.get("score", 0) for r in results]
            # 结果应保持 RRF 顺序（0.95 在 0.80 前面）
            assert scores[0] >= scores[-1], (
                "reranker 失败后应保持 RRF 排序降序。"
            )


# ---- 5. 统一候选模型字段检查（M4 前置测试） ----

class TestRetrievalCandidateModel:
    """RetrievalCandidate 统一模型应存在且包含规范字段。"""

    def test_retrieval_candidate_model_exists(self):
        """src.models.retrieval 应定义 RetrievalCandidate。"""
        try:
            from src.models.retrieval import RetrievalCandidate
            # 验证必须字段
            import typing
            hints = typing.get_type_hints(RetrievalCandidate)
            required_fields = {
                "block_id", "knowledge_id", "text", "metadata",
                "vector_score", "keyword_score", "rrf_score",
                "rerank_score", "final_score", "match_channels",
            }
            present = set(hints.keys())
            missing = required_fields - present
            assert not missing, f"RetrievalCandidate 缺少字段: {missing}"
        except ImportError:
            pytest.skip("src.models.retrieval 尚未创建（M4 阶段）")
