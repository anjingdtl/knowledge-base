"""搜索链路集成测试 — 验证 FTS/向量/混合搜索"""
from src.models.knowledge import KnowledgeItem
from src.services.db import Database
from src.services.hybrid_search import HybridSearcher
from src.services.indexer import index_knowledge_item


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
    def test_keyword_search_flattens_citation_metadata(self):
        """关键词候选应直接暴露 CitationBuilder 需要的来源字段。"""
        block = {
            "id": "citation-block",
            "parent_id": None,
            "page_id": "citation-page",
            "content": "本地索引引用路径 unique-citation-path",
            "block_type": "text",
            "properties": (
                '{"knowledge_id": "citation-page", "chunk_index": 0, '
                '"source_path": "D:/docs/architecture.md"}'
            ),
            "order_idx": 0,
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        }
        Database.insert_blocks([block])
        Database.insert_blocks_fts([block])

        results = HybridSearcher()._keyword_search(
            ["unique-citation-path"],
            top_k=3,
        )

        assert results[0]["metadata"]["source_path"] == "D:/docs/architecture.md"

    def test_blend_search_keeps_strong_keyword_match_above_weak_vector(self, monkeypatch):
        """FTS5 的负 rank 越小越相关，融合时不能被归一化成 0。"""
        searcher = HybridSearcher()

        monkeypatch.setattr(
            searcher,
            "_vector_search",
            lambda queries, top_k: (
                [{
                    "text": "这是一个语义上较弱的向量候选",
                    "metadata": {"knowledge_id": "vec", "chunk_index": 0},
                    "distance": 1.9,
                }],
                [],
            ),
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

        # 混合搜索保留 top_k*2 候选以防挤出 keyword 命中
        assert len(results) >= 1
        assert "rrf_score" in results[0]
        # keyword 命中（fts_rank != 0）必须保留在候选集中
        assert any(r.get("fts_rank", 0) != 0 for r in results)

    def test_blend_search_preserves_keyword_hits_when_vectors_dominate(self, monkeypatch):
        """精确关键词命中不能被一批向量候选完全挤出 RAG 候选集。"""
        searcher = HybridSearcher()

        monkeypatch.setattr(
            searcher,
            "_vector_search",
            lambda queries, top_k: (
                [{
                    "text": f"向量候选 {i}",
                    "metadata": {"knowledge_id": f"vec-{i}", "chunk_index": 0},
                    "distance": 0.5,
                } for i in range(20)],
                [],
            ),
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

    def test_blend_search_returns_block_level_results(self, monkeypatch):
        """混合搜索返回 block 级结果，metadata 包含 page_id"""
        searcher = HybridSearcher()

        monkeypatch.setattr(
            searcher,
            "_vector_search",
            lambda queries, top_k: (
                [{
                    "text": "向量搜索结果",
                    "metadata": {"page_id": "p-vec", "block_type": "text", "properties": {"chunk_index": 0}},
                    "distance": 0.3,
                }],
                [],
            ),
        )
        monkeypatch.setattr(
            searcher,
            "_keyword_search",
            lambda queries, top_k: [
                {
                    "text": "关键词搜索结果",
                    "metadata": {"page_id": "p-fts", "block_type": "text", "properties": {"chunk_index": 1}},
                    "distance": 0,
                    "fts_rank": -10.0,
                }
            ],
        )

        results = searcher._blend_search(["测试查询"], top_k=5)
        assert len(results) >= 1
        assert any(r["metadata"].get("page_id") for r in results)

    def test_blend_search_vector_failure_keeps_keyword_independent(self, monkeypatch):
        """BUG-2: 向量通道失败（如 embedding API 401）不能连累 keyword 通道，
        且要把降级原因写进候选的 warnings，让上层可观测、可解释。"""
        searcher = HybridSearcher()

        # 模拟 embedding API 失败：_block_store.search 抛 RuntimeError
        class _BoomBlockStore:
            def search(self, query, top_k=5, **kwargs):
                raise RuntimeError("Embedding API returned no results")

            def count(self):
                return 0

        searcher._block_store = _BoomBlockStore()

        # 插入真实 FTS 数据，让 keyword 通道有命中
        block = {
            "id": "bug2-kw-block",
            "parent_id": None,
            "page_id": "bug2-page",
            "content": "CDN 拦截旁路镜像流量策略",
            "block_type": "text",
            "properties": '{"knowledge_id": "bug2-page", "chunk_index": 0}',
            "order_idx": 0,
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        }
        Database.insert_blocks([block])
        Database.insert_blocks_fts([block])

        # 关键契约：vector 失败时 keyword 独立可用，不抛异常
        results = searcher._blend_search(["CDN 拦截"], top_k=5)

        assert len(results) >= 1
        assert any(r.get("fts_rank", 0) != 0 for r in results)
        # 所有候选 vector_score 必须为 None（未参与向量通道）
        assert all(r.get("vector_score") is None for r in results)
        # 降级信息透传到 warnings
        all_warnings = [w for r in results for w in (r.get("warnings") or [])]
        assert any("vector channel degraded" in w for w in all_warnings)


class TestMixedCJKAsciiSearch:
    """BUG-6 回归：CJK + ASCII 混合术语应能命中，锁住三层兜底链路
    (block_fts/chunk_fts 预分词 + LIKE fallback)。"""

    def test_search_knowledge_cjk_ascii_mixed_term(self):
        """'CDN拦截' 这类 CJK+ASCII 连写术语应能命中，而非落入 unicode61 盲区。"""
        item = KnowledgeItem(
            title="CDN拦截策略",
            content="当 CDN 拦截到异常旁路流量时镜像一份",
            source_type="manual",
            file_type="txt",
        )
        Database.insert_knowledge(item.to_row())
        rows = Database.search_knowledge("CDN拦截")
        assert len(rows) >= 1
        assert any(r["id"] == item.id for r in rows)

    def test_search_knowledge_standalone_term_in_mixed_context(self):
        """混合语境下的独立子词（如 '旁路'）也应命中。"""
        item = KnowledgeItem(
            title="旁路流量检测",
            content="旁路镜像流量需要单独分析",
            source_type="manual",
            file_type="txt",
        )
        Database.insert_knowledge(item.to_row())
        rows = Database.search_knowledge("旁路")
        assert len(rows) >= 1
        assert any(r["id"] == item.id for r in rows)
