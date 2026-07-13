"""Block-First Indexer 集成测试"""
import json
from datetime import datetime

import pytest

from src.models.knowledge import KnowledgeItem
from src.services.block_store import BlockStore
from src.services.db import Database
from src.services.indexer import (
    get_vector_coverage,
    index_knowledge_item,
    reindex_all,
    reindex_knowledge_item,
    repair_missing_block_vectors,
)
from src.utils.config import Config


def _insert_coverage_item(item_id: str) -> None:
    Database.insert_knowledge(KnowledgeItem(
        id=item_id,
        title=item_id,
        content="coverage test item",
        source_type="manual",
        file_type="txt",
    ).to_row())


def _insert_coverage_block(
    block_id: str,
    content: str,
    order_idx: int,
    page_id: str = "coverage-page",
) -> None:
    now = datetime.now().isoformat()
    Database.insert_blocks([{
        "id": block_id,
        "parent_id": None,
        "page_id": page_id,
        "content": content,
        "block_type": "text",
        "properties": "{}",
        "order_idx": order_idx,
        "created_at": now,
        "updated_at": now,
    }])


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


class TestVectorCoverageRepair:
    def test_repair_missing_block_vectors_embeds_only_missing_blocks(self, monkeypatch):
        Config.set("embedding.model", "test-embedding")
        Config.set("embedding.api_key", "test-key")
        _insert_coverage_item("coverage-page")
        _insert_coverage_block("already-vectorized", "already covered", 0)
        _insert_coverage_block("missing-vector", "must be embedded", 1)
        BlockStore().add_block_embedding("already-vectorized", [0.1] * 1024)
        seen: list[str] = []

        class FakeEmbeddingService:
            def build_embedding_text(self, block):
                return f"prepared:{block['content']}"

            def embed_batch_with_cache(self, texts, batch_size=20):
                seen.extend(texts)
                return [[0.2] * 1024 for _ in texts]

        monkeypatch.setattr("src.services.embedding.EmbeddingService", FakeEmbeddingService)

        result = repair_missing_block_vectors(batch_size=1)

        assert seen == ["prepared:must be embedded"]
        assert result == {
            "total_blocks": 2,
            "missing_before": 1,
            "repaired": 1,
            "failed": 0,
            "coverage_before": 0.5,
            "coverage_after": 1.0,
            "errors": [],
        }
        assert get_vector_coverage()["covered_blocks"] == 2

    def test_repair_missing_block_vectors_continues_after_a_failed_batch(self, monkeypatch):
        Config.set("embedding.model", "test-embedding")
        Config.set("embedding.api_key", "test-key")
        _insert_coverage_item("coverage-page")
        _insert_coverage_block("failing-vector", "provider failure", 0)
        _insert_coverage_block("successful-vector", "provider success", 1)

        class FakeEmbeddingService:
            def build_embedding_text(self, block):
                return block["content"]

            def embed_batch_with_cache(self, texts, batch_size=20):
                if texts == ["provider failure"]:
                    raise RuntimeError("embedding unavailable")
                return [[0.3] * 1024 for _ in texts]

        monkeypatch.setattr("src.services.embedding.EmbeddingService", FakeEmbeddingService)

        result = repair_missing_block_vectors(batch_size=1)

        assert result["repaired"] == 1
        assert result["failed"] == 1
        assert result["coverage_after"] == 0.5
        assert result["errors"] == [{
            "block_ids": ["failing-vector"],
            "error": "embedding unavailable",
        }]

    def test_repair_missing_block_vectors_reports_vectors_rejected_by_store(self, monkeypatch):
        Config.set("embedding.model", "test-embedding")
        Config.set("embedding.api_key", "test-key")
        _insert_coverage_item("coverage-page")
        _insert_coverage_block("wrong-dimension-vector", "bad dimension", 0)

        class FakeEmbeddingService:
            def build_embedding_text(self, block):
                return block["content"]

            def embed_batch_with_cache(self, texts, batch_size=20):
                return [[0.3] * 512 for _ in texts]

        monkeypatch.setattr("src.services.embedding.EmbeddingService", FakeEmbeddingService)

        result = repair_missing_block_vectors()

        assert result["repaired"] == 0
        assert result["failed"] == 1
        assert result["coverage_after"] == 0.0

    def test_repair_missing_block_vectors_requires_embedding_configuration(self):
        Config.set("embedding.model", "")
        Config.set("embedding.api_key", "")
        Config.set("llm.api_key", "")

        with pytest.raises(ValueError, match="Embedding 模型未配置"):
            repair_missing_block_vectors()

    def test_repair_missing_block_vectors_excludes_soft_deleted_knowledge(self, monkeypatch):
        Config.set("embedding.model", "test-embedding")
        Config.set("embedding.api_key", "test-key")
        _insert_coverage_item("active-coverage-page")
        _insert_coverage_item("deleted-coverage-page")
        Database.get_conn().execute(
            "UPDATE knowledge_items SET deleted_at = ? WHERE id = ?",
            (datetime.now().isoformat(), "deleted-coverage-page"),
        )
        Database.get_conn().commit()
        _insert_coverage_block("active-missing-vector", "active content", 0, "active-coverage-page")
        _insert_coverage_block("deleted-missing-vector", "deleted content", 0, "deleted-coverage-page")
        seen: list[str] = []

        class FakeEmbeddingService:
            def build_embedding_text(self, block):
                return block["content"]

            def embed_batch_with_cache(self, texts, batch_size=20):
                seen.extend(texts)
                return [[0.4] * 1024 for _ in texts]

        monkeypatch.setattr("src.services.embedding.EmbeddingService", FakeEmbeddingService)

        result = repair_missing_block_vectors()

        assert seen == ["active content"]
        assert result["total_blocks"] == 1
        assert result["missing_before"] == 1
        assert result["coverage_after"] == 1.0
