"""搜索链路集成测试 — 验证 FTS/向量/混合搜索"""
import json
from src.services.db import Database
from src.services.indexer import index_knowledge_item
from src.services.hybrid_search import HybridSearcher
from src.models.knowledge import KnowledgeItem


class TestFTS5ChineseSearch:
    def test_search_knowledge_chinese(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        results = Database.search_knowledge("测试")
        assert len(results) >= 1
        assert any(r["id"] == sample_item.id for r in results)

    def test_search_knowledge_hyphen(self):
        """含连字符的标题不应导致 FTS5 语法错误"""
        item = KnowledgeItem(title="2025-2026年报告", content="报告内容")
        Database.insert_knowledge(item.to_row())
        results = Database.search_knowledge("2025-2026")
        assert len(results) >= 1

    def test_search_knowledge_parentheses(self):
        """含括号的标题不应导致 FTS5 语法错误"""
        item = KnowledgeItem(title="汇总表(订正版)", content="表格数据")
        Database.insert_knowledge(item.to_row())
        results = Database.search_knowledge("订正版")
        assert len(results) >= 1

    def test_search_knowledge_like_fallback(self):
        """FTS 匹配失败时 LIKE 回退"""
        item = KnowledgeItem(title="特殊文档XYZ", content="内容ABC")
        Database.insert_knowledge(item.to_row())
        results = Database.search_knowledge("XYZ")
        assert len(results) >= 1


class TestChunkFTS:
    def test_chunk_fts_insert_and_search(self, sample_item, monkeypatch):
        """chunk FTS 能搜索到中文内容"""
        mock_embeddings = [[0.1] * 1024 for _ in range(10)]

        class MockEmbeddingService:
            def embed_batch(self, texts, batch_size=20):
                return mock_embeddings[:len(texts)]

        monkeypatch.setattr(
            "src.services.embedding.EmbeddingService",
            MockEmbeddingService,
        )

        Database.insert_knowledge(sample_item.to_row())
        index_knowledge_item(sample_item)
        results = Database.search_chunks_fts("测试")
        assert len(results) >= 1


class TestHybridSearch:
    def test_blend_search_keeps_strong_keyword_match_above_weak_vector(self, monkeypatch):
        """FTS5 的负 rank 越小越相关，融合时不能被归一化成 0。"""
        searcher = HybridSearcher()

        monkeypatch.setattr(
            searcher,
            "_vector_search",
            lambda queries, top_k: [
                {
                    "text": "这是一个语义上较弱的向量候选",
                    "metadata": {"knowledge_id": "vec", "chunk_index": 0},
                    "distance": 1.9,
                }
            ],
        )
        monkeypatch.setattr(
            searcher,
            "_keyword_search",
            lambda queries, top_k: [
                {
                    "text": "这里包含管理制度、审批流程和制度规范",
                    "metadata": {"knowledge_id": "fts", "chunk_index": 0},
                    "distance": 0,
                    "fts_rank": -15.0,
                }
            ],
        )

        results = searcher._blend_search(["管理制度"], top_k=1)

        assert results[0]["metadata"]["knowledge_id"] == "fts"
        assert results[0]["fts_score"] > 0

    def test_blend_search_preserves_keyword_hits_when_vectors_dominate(self, monkeypatch):
        """精确关键词命中不能被一批向量候选完全挤出 RAG 候选集。"""
        searcher = HybridSearcher()

        monkeypatch.setattr(
            searcher,
            "_vector_search",
            lambda queries, top_k: [
                {
                    "text": f"向量候选 {i}",
                    "metadata": {"knowledge_id": f"vec-{i}", "chunk_index": 0},
                    "distance": 0.5,
                }
                for i in range(20)
            ],
        )
        monkeypatch.setattr(
            searcher,
            "_keyword_search",
            lambda queries, top_k: [
                {
                    "text": "管理制度相关文档",
                    "metadata": {"knowledge_id": "fts", "chunk_index": 0},
                    "distance": 0,
                    "fts_rank": -20.0,
                }
            ],
        )

        results = searcher._blend_search(["管理制度"], top_k=5)

        assert any(r["metadata"]["knowledge_id"] == "fts" for r in results)
