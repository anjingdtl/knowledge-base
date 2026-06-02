"""Block-First Indexer 集成测试"""
from src.services.db import Database
from src.services.indexer import index_knowledge_item, reindex_knowledge_item
from src.services.block_store import BlockStore
from src.models.knowledge import KnowledgeItem


class TestBlockFirstIndexer:
    def test_index_creates_blocks_and_vectors(self, monkeypatch):
        """index_knowledge_item → blocks + vec_blocks + block_fts 都有数据"""
        mock_embeddings = [[0.1] * 1024 for _ in range(10)]

        class MockEmbeddingService:
            def embed_batch_with_cache(self, texts, batch_size=20):
                return mock_embeddings[:len(texts)]

        monkeypatch.setattr(
            "src.services.embedding.EmbeddingService",
            MockEmbeddingService,
        )

        item = KnowledgeItem(
            title="索引测试",
            content="这是一段用于测试索引管线的内容。" * 20,
            source_type="manual",
            file_type="txt",
            tags=["测试"],
        )
        Database.insert_knowledge(item.to_row())
        index_knowledge_item(item)

        conn = Database.get_conn()
        block_count = conn.execute(
            "SELECT count(*) FROM blocks WHERE page_id = ?", (item.id,)
        ).fetchone()[0]
        assert block_count >= 1

        store = BlockStore()
        vec_count = store.count_by_page(item.id)
        assert vec_count >= 1

        fts_count = conn.execute(
            "SELECT count(*) FROM block_fts WHERE page_id = ?", (item.id,)
        ).fetchone()[0]
        assert fts_count >= 1

    def test_reindex_cleans_and_rebuilds(self, monkeypatch):
        """reindex → 旧数据清理 + 新数据重建"""
        mock_embeddings = [[0.2] * 1024 for _ in range(10)]

        class MockEmbeddingService:
            def embed_batch_with_cache(self, texts, batch_size=20):
                return mock_embeddings[:len(texts)]

        monkeypatch.setattr(
            "src.services.embedding.EmbeddingService",
            MockEmbeddingService,
        )

        item = KnowledgeItem(
            title="重索引测试",
            content="原始内容" * 20,
            source_type="manual",
            file_type="txt",
            tags=["测试"],
        )
        Database.insert_knowledge(item.to_row())
        index_knowledge_item(item)

        store = BlockStore()
        old_count = store.count_by_page(item.id)
        assert old_count >= 1

        item.content = "更新后的内容" * 20
        reindex_knowledge_item(item.id, item)

        new_count = store.count_by_page(item.id)
        assert new_count >= 1

    def test_knowledge_chunks_compat_still_written(self, monkeypatch):
        """兼容层 knowledge_chunks 仍被写入"""
        mock_embeddings = [[0.3] * 1024 for _ in range(10)]

        class MockEmbeddingService:
            def embed_batch_with_cache(self, texts, batch_size=20):
                return mock_embeddings[:len(texts)]

        monkeypatch.setattr(
            "src.services.embedding.EmbeddingService",
            MockEmbeddingService,
        )

        item = KnowledgeItem(
            title="兼容层测试",
            content="兼容层写入测试内容" * 20,
            source_type="manual",
            file_type="txt",
            tags=["测试"],
        )
        Database.insert_knowledge(item.to_row())
        index_knowledge_item(item)

        conn = Database.get_conn()
        chunk_count = conn.execute(
            "SELECT count(*) FROM knowledge_chunks WHERE knowledge_id = ?", (item.id,)
        ).fetchone()[0]
        assert chunk_count >= 1
