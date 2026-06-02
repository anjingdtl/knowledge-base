"""BlockStore 单元测试 — Block 级向量存储"""
import pytest
from src.services.block_store import BlockStore
from src.services.db import Database


class TestBlockStore:
    def test_add_block_embedding_and_search(self):
        """写入 block embedding → 搜索能命中"""
        conn = Database.get_conn()
        conn.execute(
            "INSERT INTO blocks (id, page_id, content, block_type, properties, order_idx, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("block-001", "page-001", "测试内容", "text", "{}", 0, "2026-01-01", "2026-01-01"),
        )
        conn.commit()

        store = BlockStore()
        store.add_block_embedding("block-001", [0.1] * 1024)

        results = store.search("测试", top_k=5, query_embedding=[0.1] * 1024)
        assert len(results) >= 1
        assert results[0]["id"] == "block-001"
        assert results[0]["metadata"]["page_id"] == "page-001"

    def test_delete_by_page(self):
        """删除 page → 该 page 下所有 block 向量被清理"""
        conn = Database.get_conn()
        for i in range(3):
            conn.execute(
                "INSERT INTO blocks (id, page_id, content, block_type, properties, order_idx, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"block-{i}", "page-del", f"内容{i}", "text", "{}", i, "2026-01-01", "2026-01-01"),
            )
        conn.commit()

        store = BlockStore()
        for i in range(3):
            store.add_block_embedding(f"block-{i}", [0.1] * 1024)
        assert store.count_by_page("page-del") == 3

        store.delete_by_page("page-del")
        assert store.count_by_page("page-del") == 0

    def test_dimension_from_config(self):
        """维度从 config 读取，非硬编码"""
        from src.utils.config import Config
        Config.set("embedding.dimension", 512)

        BlockStore._instance = None
        BlockStore._initialized = False

        store = BlockStore()
        store._ensure_table()

        conn = store._get_conn()
        row = conn.execute("SELECT count(*) FROM vec_blocks").fetchone()
        assert row is not None

        Config.set("embedding.dimension", 1024)

    def test_search_returns_block_metadata(self):
        """搜索结果包含 page_id, block_type, properties"""
        conn = Database.get_conn()
        conn.execute(
            "INSERT INTO blocks (id, page_id, content, block_type, properties, order_idx, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("block-meta", "page-meta", "元数据测试", "code", '{"chunk_index": 5}', 5, "2026-01-01", "2026-01-01"),
        )
        conn.commit()

        store = BlockStore()
        store.add_block_embedding("block-meta", [0.2] * 1024)

        results = store.search("元数据", top_k=5, query_embedding=[0.2] * 1024)
        assert len(results) >= 1
        r = results[0]
        assert r["metadata"]["page_id"] == "page-meta"
        assert r["metadata"]["block_type"] == "code"
        assert "chunk_index" in r["metadata"]["properties"]

    def test_count_and_count_by_page(self):
        """统计功能正确"""
        conn = Database.get_conn()
        conn.execute(
            "INSERT INTO blocks (id, page_id, content, block_type, properties, order_idx, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("block-cnt", "page-cnt", "计数测试", "text", "{}", 0, "2026-01-01", "2026-01-01"),
        )
        conn.commit()

        store = BlockStore()
        store.add_block_embedding("block-cnt", [0.3] * 1024)

        assert store.count() >= 1
        assert store.count_by_page("page-cnt") == 1
