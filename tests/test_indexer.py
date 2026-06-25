"""Block-First Indexer 集成测试"""
import json

from src.models.knowledge import KnowledgeItem
from src.services.block_store import BlockStore
from src.services.db import Database
from src.services.indexer import index_knowledge_item, reindex_all, reindex_knowledge_item


class TestBlockFirstIndexer:
    def test_index_preserves_citation_source_metadata(self, monkeypatch, tmp_path):
        """文件来源路径必须进入 Block 元数据，供 CitationBuilder 使用。"""

        class MockEmbeddingService:
            def embed_batch_with_cache(self, texts, batch_size=20):
                return [[0.1] * 1024 for _ in texts]

        monkeypatch.setattr(
            "src.services.embedding.EmbeddingService",
            MockEmbeddingService,
        )

        source_path = tmp_path / "architecture.md"
        item = KnowledgeItem(
            title="架构设计",
            content="# 架构\n\nSQLite 使用 WAL 模式保存本地索引。",
            source_type="file",
            source_path=str(source_path),
            file_type="md",
        )
        Database.insert_knowledge(item.to_row())
        index_knowledge_item(item)

        row = Database.get_conn().execute(
            "SELECT properties FROM blocks WHERE page_id = ? LIMIT 1",
            (item.id,),
        ).fetchone()
        properties = json.loads(row["properties"])

        assert properties["source_path"] == str(source_path)
        assert properties["title"] == "架构设计"

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


class TestReindexCheckpoint:
    """第6轮 BUG#8 回归：reindex 正常完成后清除断点行，不再残留僵尸任务。"""

    def test_reindex_checkpoint_cleared_on_completion(self, monkeypatch):
        """reindex_all 正常跑完后，'reindex_checkpoint' 行应被删除。"""
        from datetime import datetime

        mock_embeddings = [[0.3] * 1024 for _ in range(10)]

        class MockEmbeddingService:
            def embed_batch_with_cache(self, texts, batch_size=20):
                return mock_embeddings[:len(texts)]

        monkeypatch.setattr(
            "src.services.embedding.EmbeddingService",
            MockEmbeddingService,
        )

        item = KnowledgeItem(
            title="断点清除测试",
            content="用于验证 reindex 断点清理的内容" * 20,
            source_type="manual",
            file_type="txt",
        )
        Database.insert_knowledge(item.to_row())
        index_knowledge_item(item)

        # 预置一个残留的 checkpoint 行（模拟旧实现的僵尸记录）
        conn = Database.get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO async_jobs "
            "(id, job_type, status, params, created_at, started_at) "
            "VALUES ('reindex_checkpoint', 'reindex_all', 'processing', ?, ?, NULL)",
            (json.dumps({"processed_ids": []}), datetime.now().isoformat()),
        )
        conn.commit()

        result = reindex_all(dry_run=False, restart=False)
        assert result["success"] >= 1

        # 核心断言：checkpoint 行应已被清除
        row = conn.execute(
            "SELECT id FROM async_jobs WHERE id = 'reindex_checkpoint'"
        ).fetchone()
        assert row is None, "reindex 完成后 'reindex_checkpoint' 行不应残留"

    def test_reindex_checkpoint_uses_valid_status(self, monkeypatch):
        """_save_reindex_checkpoint 写入的 status 必须是合法枚举 'running'，非 'processing'。"""
        from src.services.indexer import _save_reindex_checkpoint

        _save_reindex_checkpoint(["item-1", "item-2"])
        conn = Database.get_conn()
        row = conn.execute(
            "SELECT status, started_at FROM async_jobs WHERE id = 'reindex_checkpoint'"
        ).fetchone()
        assert row is not None
        assert row["status"] == "running", f"status 应为 'running'，实际 {row['status']!r}"
        assert row["started_at"] is not None, "started_at 不应为 NULL"
