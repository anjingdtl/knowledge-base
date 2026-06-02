# Block-First 向量存储重写 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将向量存储从 chunk 模型彻底重写为 Block 模型，使每个 Block 成为最小检索单元，同时修复 6 个管线 Bug 并接入 EmbeddingCache。

**Architecture:** Block-First 重写 — 新建 `BlockStore` 类替代 `VectorStore`，`vec_blocks` 虚拟表关联 `blocks` 表，`block_fts` 替代 `chunk_fts` 作为主全文索引。旧表（`knowledge_chunks`、`vec_chunks`、`chunk_fts`）保留为兼容层，写入时同步写但搜索/RAG 不再读取。

**Tech Stack:** Python 3.12+, sqlite-vec, SQLite FTS5, jieba, OpenAI-compatible embedding API

---

## File Structure

### 新建文件

| 文件 | 职责 |
|------|------|
| `src/services/block_store.py` | Block 级向量存储，替代 VectorStore |
| `scripts/migrate_to_block_store.py` | 数据迁移脚本 |
| `tests/test_block_store.py` | BlockStore 单元测试 |
| `tests/test_indexer.py` | Block-First indexer 集成测试 |
| `tests/test_embedding_cache.py` | EmbeddingCache 集成测试 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `src/services/db.py` | 新增 `insert_blocks`, `insert_blocks_fts`, `search_blocks_fts`, `delete_blocks_by_page` 等方法 |
| `src/services/embedding.py` | 新增 `embed_batch_with_cache()` |
| `src/services/indexer.py` | 重写 `index_knowledge_item()` 为 Block-First 管线 |
| `src/services/hybrid_search.py` | 改用 BlockStore + block_fts |
| `src/services/rag_pipeline.py` | 修复 Bug #1, #2, 适配 block 级 metadata |
| `src/core/container.py` | 修复 Bug #4, #5，引入 BlockStore |
| `src/utils/config.py` | 删除 `get_chroma_dir()` |
| `src/utils/paths.py` | 删除 `chroma_dir` 默认值 |
| `tests/conftest.py` | 重置 BlockStore 单例 |
| `tests/test_search.py` | 改造为 block 级测试 |

---

### Task 1: BlockStore 类

**Files:**
- Create: `src/services/block_store.py`
- Test: `tests/test_block_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_block_store.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_block_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.block_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/block_store.py
"""Block 级向量存储 — 基于 sqlite-vec，rowid 关联 blocks 表"""
import json
import logging
import struct
import threading

import sqlite_vec

from src.utils.config import Config

_lock = threading.Lock()


class BlockStore:
    """Block 级向量存储服务

    支持两种模式:
    1. DI 注入模式: BlockStore.__new__ + 手动设置 _db
    2. 单例模式（兼容）: BlockStore() 自动获取 Database 单例
    """
    _instance = None
    _initialized = False

    def __new__(cls, db=None):
        if db is not None:
            inst = super().__new__(cls)
            inst._initialized = False
            inst._db = db
            return inst
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
            cls._instance._db = None
        return cls._instance

    def _check_db_changed(self):
        if self._db is not None:
            return
        from src.services.db import Database
        current_db = Database._instance if hasattr(Database, '_instance') else None
        if current_db is not None and current_db is not getattr(self, '_last_db_instance', None):
            self._last_db_instance = current_db
            self._initialized = False

    def _get_conn(self):
        if self._db is not None:
            return self._db.get_conn()
        from src.services.db import Database
        return Database.get_conn()

    def _get_dimension(self) -> int:
        return Config.get("embedding.dimension", 1024)

    def _ensure_table(self):
        self._check_db_changed()
        if self._initialized:
            return
        with _lock:
            if self._initialized:
                return
            conn = self._get_conn()
            try:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
            finally:
                conn.enable_load_extension(False)
            dim = self._get_dimension()
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_blocks USING vec0("
                f"embedding float[{dim}] distance_metric=cosine)"
            )
            conn.commit()
            self._initialized = True

    def _pack_embedding(self, embedding: list[float]) -> bytes:
        return struct.pack(f"{len(embedding)}f", *embedding)

    def add_block_embedding(self, block_id: str, embedding: list[float]):
        self._ensure_table()
        conn = self._get_conn()
        row = conn.execute(
            "SELECT rowid FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        if not row:
            logging.warning(f"Block {block_id} not found, skip vec insert")
            return
        rowid = row[0]
        conn.execute(
            "INSERT OR REPLACE INTO vec_blocks(rowid, embedding) VALUES (?, ?)",
            (rowid, self._pack_embedding(embedding)),
        )
        conn.commit()

    def add_blocks_batch(self, blocks: list[dict]):
        for b in blocks:
            emb = b.get("embedding")
            if emb:
                self.add_block_embedding(b["id"], emb)

    def search(self, query: str, top_k: int = 5, tags: list[str] | None = None,
               query_embedding: list[float] | None = None) -> list[dict]:
        self._ensure_table()
        if query_embedding is None:
            from src.services.embedding import EmbeddingService
            query_embedding = EmbeddingService().embed(query)

        conn = self._get_conn()
        packed = self._pack_embedding(query_embedding)
        rows = conn.execute(
            """SELECT b.id, b.page_id, b.content, b.block_type, b.properties,
                      vc.distance
               FROM vec_blocks vc
               JOIN blocks b ON b.rowid = vc.rowid
               WHERE vc.embedding MATCH ? AND k = ?
               ORDER BY vc.distance""",
            (packed, top_k),
        ).fetchall()

        results = []
        for r in rows:
            try:
                properties = json.loads(r[4]) if r[4] else {}
            except (json.JSONDecodeError, TypeError):
                properties = {}
            results.append({
                "id": r[0],
                "text": r[2],
                "metadata": {
                    "page_id": r[1],
                    "block_type": r[3],
                    "properties": properties,
                },
                "distance": r[5],
            })
        return results

    def delete_by_page(self, page_id: str):
        self._ensure_table()
        conn = self._get_conn()
        conn.execute("BEGIN")
        try:
            rows = conn.execute(
                "SELECT rowid FROM blocks WHERE page_id = ?",
                (page_id,),
            ).fetchall()
            if rows:
                placeholders = ",".join("?" for _ in rows)
                conn.execute(
                    f"DELETE FROM vec_blocks WHERE rowid IN ({placeholders})",
                    [r[0] for r in rows],
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def delete_by_block(self, block_id: str):
        self._ensure_table()
        conn = self._get_conn()
        row = conn.execute(
            "SELECT rowid FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        if row:
            conn.execute(
                "DELETE FROM vec_blocks WHERE rowid = ?", (row[0],)
            )
            conn.commit()

    def count(self) -> int:
        self._ensure_table()
        conn = self._get_conn()
        row = conn.execute("SELECT count(*) FROM vec_blocks").fetchone()
        return row[0] if row else 0

    def count_by_page(self, page_id: str) -> int:
        self._ensure_table()
        conn = self._get_conn()
        row = conn.execute(
            """SELECT COUNT(*)
               FROM vec_blocks vc
               JOIN blocks b ON b.rowid = vc.rowid
               WHERE b.page_id = ?""",
            (page_id,),
        ).fetchone()
        return row[0] if row else 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_block_store.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/services/block_store.py tests/test_block_store.py
git commit -m "feat: add BlockStore — block-level vector storage replacing VectorStore"
```

---

### Task 2: Database 新增 Block 级方法

**Files:**
- Modify: `src/services/db.py:726-839` (在现有 chunk 方法后新增 block 方法)
- Test: `tests/test_block_store.py` (扩展)

- [ ] **Step 1: Write the failing test**

在 `tests/test_block_store.py` 末尾追加：

```python
class TestDatabaseBlockMethods:
    def test_insert_blocks_and_search_fts(self):
        """insert_blocks + insert_blocks_fts + search_blocks_fts 端到端"""
        blocks = [
            {
                "id": "b-fts-1",
                "parent_id": None,
                "page_id": "p-fts-1",
                "content": "这是一段关于机器学习的文本",
                "block_type": "text",
                "properties": '{"knowledge_id": "p-fts-1", "chunk_index": 0}',
                "order_idx": 0,
                "created_at": "2026-01-01",
                "updated_at": "2026-01-01",
            },
            {
                "id": "b-fts-2",
                "parent_id": None,
                "page_id": "p-fts-1",
                "content": "深度学习是机器学习的子领域",
                "block_type": "text",
                "properties": '{"knowledge_id": "p-fts-1", "chunk_index": 1}',
                "order_idx": 1,
                "created_at": "2026-01-01",
                "updated_at": "2026-01-01",
            },
        ]
        Database.insert_blocks(blocks)
        Database.insert_blocks_fts(blocks)

        results = Database.search_blocks_fts("机器学习", limit=5)
        assert len(results) >= 1
        assert any(r["id"] == "b-fts-1" for r in results)

    def test_delete_blocks_by_page(self):
        """delete_blocks_by_page 清理 blocks + block_fts + block_property_index"""
        blocks = [
            {
                "id": "b-del-1",
                "parent_id": None,
                "page_id": "p-del",
                "content": "待删除内容",
                "block_type": "text",
                "properties": '{"knowledge_id": "p-del", "chunk_index": 0}',
                "order_idx": 0,
                "created_at": "2026-01-01",
                "updated_at": "2026-01-01",
            },
        ]
        Database.insert_blocks(blocks)
        Database.insert_blocks_fts(blocks)

        Database.delete_blocks_by_page("p-del")

        conn = Database.get_conn()
        row = conn.execute("SELECT count(*) FROM blocks WHERE page_id = ?", ("p-del",)).fetchone()
        assert row[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_block_store.py::TestDatabaseBlockMethods -v`
Expected: FAIL with `AttributeError: type object 'Database' has no attribute 'insert_blocks'`

- [ ] **Step 3: Write minimal implementation**

在 `src/services/db.py` 的 `# ---- Chunk FTS (jieba 分词) ----` 注释之前（约第 816 行），插入以下方法：

```python
    # ---- Block-level methods (Block-First architecture) ----

    @classmethod
    def insert_blocks(cls, blocks: list[dict]):
        """写入 blocks 表 + block_property_index（原子事务）"""
        with cls._write_lock:
            conn = cls.get_conn()
            conn.executemany(
                """INSERT OR REPLACE INTO blocks
                   (id, parent_id, page_id, content, block_type, properties, order_idx, created_at, updated_at)
                   VALUES (:id, :parent_id, :page_id, :content, :block_type, :properties, :order_idx, :created_at, :updated_at)""",
                blocks,
            )
            prop_rows = []
            for block in blocks:
                try:
                    props = json.loads(block.get("properties", "{}"))
                except (json.JSONDecodeError, TypeError):
                    props = {}
                for key, value in props.items():
                    prop_rows.append({
                        "block_id": block["id"],
                        "prop_key": key,
                        "prop_value": str(value),
                        "value_type": "ref" if key == "knowledge_id" else "str",
                    })
            if prop_rows:
                conn.executemany(
                    """INSERT OR REPLACE INTO block_property_index
                       (block_id, prop_key, prop_value, value_type)
                       VALUES (:block_id, :prop_key, :prop_value, :value_type)""",
                    prop_rows,
                )
            conn.commit()

    @classmethod
    def insert_blocks_fts(cls, blocks: list[dict]):
        """将 block 文本用 jieba 全模式分词后写入 block_fts"""
        from src.utils.chinese_tokenizer import tokenize_chinese_full
        conn = cls.get_conn()
        for b in blocks:
            segmented = tokenize_chinese_full(b.get("content", ""))
            conn.execute(
                "INSERT INTO block_fts(fts_segmented, page_id, block_id) VALUES (?, ?, ?)",
                (segmented, b["page_id"], b["id"]),
            )
        conn.commit()

    @classmethod
    def search_blocks_fts(cls, query: str, limit: int = 10) -> list[dict]:
        """Block 级 FTS 搜索"""
        from src.utils.chinese_tokenizer import tokenize_chinese_full
        sanitized = tokenize_chinese_full(query)
        if not sanitized.strip():
            return []
        rows = cls.get_conn().execute(
            """SELECT b.id, b.page_id, b.content, b.block_type, b.properties,
                      bf.rank
               FROM block_fts bf
               JOIN blocks b ON b.id = bf.block_id
               WHERE block_fts MATCH ?
               ORDER BY bf.rank
               LIMIT ?""",
            (sanitized, limit),
        ).fetchall()
        results = []
        for r in rows:
            try:
                properties = json.loads(r[4]) if r[4] else {}
            except (json.JSONDecodeError, TypeError):
                properties = {}
            results.append({
                "id": r[0],
                "page_id": r[1],
                "content": r[2],
                "block_type": r[3],
                "properties": properties,
                "fts_rank": r[5],
            })
        return results

    @classmethod
    def delete_blocks_fts(cls, page_id: str):
        """删除指定 page 的 block FTS 记录"""
        with cls._write_lock:
            cls.get_conn().execute(
                "DELETE FROM block_fts WHERE page_id = ?", (page_id,)
            )
            cls.get_conn().commit()

    @classmethod
    def delete_blocks_by_page(cls, page_id: str):
        """删除指定 page 的所有 block 数据（blocks + block_fts + block_property_index + block_refs）"""
        with cls._write_lock:
            conn = cls.get_conn()
            conn.execute(
                "DELETE FROM block_property_index WHERE block_id IN (SELECT id FROM blocks WHERE page_id = ?)",
                (page_id,),
            )
            conn.execute(
                "DELETE FROM block_refs WHERE source_id IN (SELECT id FROM blocks WHERE page_id = ?) OR target_id IN (SELECT id FROM blocks WHERE page_id = ?)",
                (page_id, page_id),
            )
            conn.execute("DELETE FROM block_fts WHERE page_id = ?", (page_id,))
            conn.execute("DELETE FROM blocks WHERE page_id = ?", (page_id,))
            conn.commit()
```

同时，在 `db.py` 的 `_SCHEMA` 字符串中（约第 98 行 `chunk_fts` 定义之后），追加 `block_fts` 表定义：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS block_fts USING fts5(
    fts_segmented,
    page_id UNINDEXED,
    block_id UNINDEXED,
    tokenize='unicode61'
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_block_store.py::TestDatabaseBlockMethods -v`
Expected: PASS (all 2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/services/db.py tests/test_block_store.py
git commit -m "feat: add block-level Database methods (insert_blocks, block_fts, search_blocks_fts)"
```

---

### Task 3: EmbeddingService 缓存接入

**Files:**
- Modify: `src/services/embedding.py:37-68` (新增 `embed_batch_with_cache`)
- Test: `tests/test_embedding_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_embedding_cache.py
"""EmbeddingCache 集成测试 — 验证缓存接入 embedding 管线"""
from unittest.mock import MagicMock, patch
from src.services.embedding import EmbeddingService
from src.core.embedding_cache import EmbeddingCache
from src.services.db import Database


class TestEmbeddingCacheIntegration:
    def test_cache_hit_returns_cached_embedding(self):
        """缓存命中返回缓存值，不调用 API"""
        svc = EmbeddingService()
        cache = EmbeddingCache()

        import hashlib
        text = "缓存测试文本"
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        model = svc._cfg("embedding.model", "")
        cached_vec = [0.5] * 1024
        cache.put(content_hash, model, cached_vec)

        with patch.object(svc, '_get_client') as mock_client:
            result = svc.embed_batch_with_cache([text])
            mock_client.assert_not_called()

        assert len(result) == 1
        assert result[0] == cached_vec

    def test_cache_miss_calls_api_and_caches(self):
        """缓存未命中调用 API 并写入缓存"""
        svc = EmbeddingService()

        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.7] * 1024)]

        with patch.object(svc, '_get_client') as mock_client:
            mock_client.return_value.embeddings.create.return_value = mock_response
            result = svc.embed_batch_with_cache(["未缓存的文本XYZ"])

        assert len(result) == 1
        assert result[0] == [0.7] * 1024

        cache = EmbeddingCache()
        import hashlib
        content_hash = hashlib.sha256("未缓存的文本XYZ".encode()).hexdigest()
        model = svc._cfg("embedding.model", "")
        cached = cache.get(content_hash, model)
        assert cached == [0.7] * 1024

    def test_cache_invalidation_by_model(self):
        """切换模型时缓存失效"""
        cache = EmbeddingCache()
        import hashlib
        text = "模型切换测试"
        content_hash = hashlib.sha256(text.encode()).hexdigest()

        cache.put(content_hash, "model-a", [0.1] * 1024)
        cache.put(content_hash, "model-b", [0.2] * 1024)

        assert cache.get(content_hash, "model-a") == [0.1] * 1024
        assert cache.get(content_hash, "model-b") == [0.2] * 1024

        deleted = cache.invalidate_model("model-a")
        assert deleted == 1
        assert cache.get(content_hash, "model-a") is None
        assert cache.get(content_hash, "model-b") == [0.2] * 1024
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_embedding_cache.py -v`
Expected: FAIL with `AttributeError: 'EmbeddingService' object has no attribute 'embed_batch_with_cache'`

- [ ] **Step 3: Write minimal implementation**

在 `src/services/embedding.py` 的 `embed_batch` 方法之后（第 68 行后），追加：

```python
    def embed_batch_with_cache(self, texts: list[str], batch_size: int = 20) -> list[list[float]]:
        """批量生成 embedding，带 SQLite 缓存"""
        import hashlib
        from src.core.embedding_cache import EmbeddingCache

        cache = EmbeddingCache()
        model = self._cfg("embedding.model", "")

        results = [None] * len(texts)
        to_embed = []

        for i, text in enumerate(texts):
            content_hash = hashlib.sha256(text.encode()).hexdigest()
            cached = cache.get(content_hash, model)
            if cached is not None:
                results[i] = cached
            else:
                to_embed.append((i, text))

        if to_embed:
            texts_to_embed = [t for _, t in to_embed]
            embeddings = self.embed_batch(texts_to_embed, batch_size)
            for (i, text), emb in zip(to_embed, embeddings):
                content_hash = hashlib.sha256(text.encode()).hexdigest()
                cache.put(content_hash, model, emb)
                results[i] = emb

        return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_embedding_cache.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/services/embedding.py tests/test_embedding_cache.py
git commit -m "feat: add embed_batch_with_cache — integrate EmbeddingCache into embedding pipeline"
```

---

### Task 4: Indexer 管线改造

**Files:**
- Modify: `src/services/indexer.py:1-118` (重写)
- Test: `tests/test_indexer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_indexer.py
"""Block-First Indexer 集成测试"""
from unittest.mock import MagicMock, patch
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_indexer.py -v`
Expected: FAIL (tests may pass with old indexer since it writes blocks via `_upsert_blocks_from_chunks_unlocked`, but `block_fts` and `BlockStore` won't be populated)

- [ ] **Step 3: Write minimal implementation**

重写 `src/services/indexer.py`：

```python
"""知识条目索引 — Block-First 管线（分块 + 向量化 + 全文索引）"""
import json
import logging
import uuid
from datetime import datetime

from src.utils.config import Config
from src.models.knowledge import KnowledgeItem, KnowledgeChunk
from src.services.db import Database
from src.services.text_splitter import split_text, split_markdown, split_code
from src.services.block_store import BlockStore
from src.services.vectorstore import VectorStore


def index_knowledge_item(item: KnowledgeItem):
    """将知识条目分块并存入 DB + 向量库 + 全文索引（Block-First 管线）"""
    tags_str = ",".join(item.tags)
    chunk_size = Config.get("rag.chunk_size", 500)
    chunk_overlap = Config.get("rag.chunk_overlap", 50)
    base_meta = {"knowledge_id": item.id, "tags": tags_str, "title": item.title,
                 "created_at": item.created_at}

    if item.file_type == "md":
        chunks = split_markdown(item.content, chunk_size=chunk_size,
                                chunk_overlap=chunk_overlap, metadata=base_meta)
    elif item.file_type == "code":
        chunks = split_code(item.content, chunk_size=chunk_size,
                            chunk_overlap=chunk_overlap, metadata=base_meta)
    else:
        chunks = split_text(item.content, chunk_size=chunk_size,
                            chunk_overlap=chunk_overlap, metadata=base_meta)
    if not chunks:
        return

    now = datetime.now().isoformat()

    block_rows = []
    chunk_rows = []
    for c in chunks:
        block_id = str(uuid.uuid4())
        block_rows.append({
            "id": block_id,
            "parent_id": None,
            "page_id": item.id,
            "content": c.text,
            "block_type": "text",
            "properties": json.dumps({
                "knowledge_id": item.id,
                "chunk_index": c.index,
            }, ensure_ascii=False),
            "order_idx": c.index,
            "created_at": now,
            "updated_at": now,
        })
        chunk = KnowledgeChunk(
            knowledge_id=item.id, chunk_index=c.index, chunk_text=c.text
        )
        row = chunk.to_row()
        row["id"] = block_id
        chunk_rows.append(row)

    Database.insert_blocks(block_rows)

    Database.insert_chunks(chunk_rows)

    texts = [b["content"] for b in block_rows]
    embeddings = [None] * len(texts)
    try:
        from src.services.embedding import EmbeddingService
        embeddings = EmbeddingService().embed_batch_with_cache(texts)
        if len(embeddings) != len(texts):
            logging.warning(
                "Embedding count mismatch for %s: expected %d, got %d. "
                "Missing embeddings will be unsearchable via vector search.",
                item.id, len(texts), len(embeddings),
            )
    except Exception as e:
        logging.error(f"Embedding failed for {item.id}: {e}")

    for i, block in enumerate(block_rows):
        emb = embeddings[i] if i < len(embeddings) else None
        if emb:
            try:
                BlockStore().add_block_embedding(block["id"], emb)
            except Exception as e:
                logging.error(f"Vec insert failed for block {block['id']}: {e}")
            try:
                VectorStore().add_chunk_embedding(chunk_rows[i]["id"], item.id, emb)
            except Exception as e:
                logging.error(f"Legacy vec insert failed for chunk {chunk_rows[i]['id']}: {e}")

    try:
        Database.insert_blocks_fts(block_rows)
    except Exception as e:
        logging.error(f"Block FTS insert failed for {item.id}: {e}")

    chunk_dicts = [{"id": chunk_rows[i]["id"], "knowledge_id": item.id,
                    "chunk_text": c.text} for i, c in enumerate(chunks)]
    try:
        Database.insert_chunks_fts(chunk_dicts)
    except Exception as e:
        logging.error(f"Legacy chunk FTS insert failed for {item.id}: {e}")


def reindex_knowledge_item(item_id: str, item: KnowledgeItem):
    """删除旧索引，重新分块索引"""
    BlockStore().delete_by_page(item_id)
    Database.delete_blocks_by_page(item_id)
    VectorStore().delete_by_knowledge(item_id)
    Database.delete_chunks_fts(item_id)
    Database.delete_chunks(item_id)
    index_knowledge_item(item)


def reindex_all(progress_callback: callable = None) -> dict:
    """重建所有知识条目的索引（向量 + FTS）"""
    items = Database.list_knowledge(limit=100000)
    total = len(items)
    success = 0
    failed = 0
    errors = []

    for i, row in enumerate(items):
        try:
            tags_raw = row.get("tags", "[]")
            if isinstance(tags_raw, str):
                import json as _json
                try:
                    tags = _json.loads(tags_raw)
                except (json.JSONDecodeError, TypeError):
                    tags = []
            else:
                tags = tags_raw if isinstance(tags_raw, list) else []

            item = KnowledgeItem(
                id=row["id"],
                title=row["title"],
                content=row.get("content", ""),
                tags=tags,
                source_type=row.get("source_type", "manual"),
                source_path=row.get("source_path", ""),
                file_type=row.get("file_type", "txt"),
            )
            reindex_knowledge_item(row["id"], item)
            success += 1
            if progress_callback:
                progress_callback(i + 1, total, f"Reindexing {i+1}/{total}")
            elif (i + 1) % 10 == 0:
                logging.info(f"Reindex progress: {i + 1}/{total}")
        except Exception as e:
            failed += 1
            errors.append({"id": row["id"], "title": row["title"], "error": str(e)})
            logging.error(f"Reindex failed for {row.get('title', '')}: {e}")

    return {"total": total, "success": success, "failed": failed, "errors": errors[:10]}
```

注意：`insert_chunks` 内部已调用 `_upsert_blocks_from_chunks_unlocked`，但由于我们在 `insert_blocks` 中已经用新的 UUID 写入了 blocks，`insert_chunks` 会用相同的 UUID 再次写入（`INSERT OR REPLACE`），不会冲突。`chunk_rows` 的 `id` 与 `block_rows` 的 `id` 保持一致。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_indexer.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Run all existing tests to verify no regressions**

Run: `pytest tests/ -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add src/services/indexer.py tests/test_indexer.py
git commit -m "feat: rewrite indexer to Block-First pipeline with dual-write compatibility"
```

---

### Task 5: HybridSearcher 改造

**Files:**
- Modify: `src/services/hybrid_search.py:1-162` (改用 BlockStore + block_fts)
- Modify: `tests/test_search.py` (改造为 block 级测试)

- [ ] **Step 1: Write the failing test**

在 `tests/test_search.py` 的 `TestHybridSearch` 类中追加：

```python
    def test_blend_search_returns_block_level_results(self, monkeypatch):
        """混合搜索返回 block 级结果，metadata 包含 page_id"""
        searcher = HybridSearcher()

        monkeypatch.setattr(
            searcher,
            "_vector_search",
            lambda queries, top_k: [
                {
                    "text": "向量搜索结果",
                    "metadata": {"page_id": "p-vec", "block_type": "text", "properties": {"chunk_index": 0}},
                    "distance": 0.3,
                }
            ],
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_search.py::TestHybridSearch::test_blend_search_returns_block_level_results -v`
Expected: FAIL (current `_candidate_id` uses `knowledge_id:chunk_index`, not `page_id:block_id`)

- [ ] **Step 3: Write minimal implementation**

重写 `src/services/hybrid_search.py`：

```python
"""混合检索模块 — Block-First 架构（embedding/keywords/blend）+ 加分融合"""
import hashlib
import logging

from src.utils.config import Config
from src.services.block_store import BlockStore
from src.services.db import Database


class HybridSearcher:
    def search(self, queries: list[str], top_k: int = 5) -> list[dict]:
        mode = Config.get("rag.search_mode", "blend")
        if mode == "embedding":
            return self._vector_search(queries, top_k)
        elif mode == "keywords":
            return self._keyword_search(queries, top_k)
        else:
            return self._blend_search(queries, top_k)

    def _vector_search(self, queries: list[str], top_k: int) -> list[dict]:
        results = []
        seen = set()
        for query in queries:
            try:
                vec_results = BlockStore().search(query, top_k=top_k * 2)
                for r in vec_results:
                    cid = r["id"]
                    if cid not in seen:
                        seen.add(cid)
                        results.append({
                            "text": r["text"],
                            "metadata": r.get("metadata", {}),
                            "distance": r.get("distance", 0),
                        })
            except Exception as e:
                logging.warning(f"Vector search failed: {e}")
        results.sort(key=lambda x: (1 - x["distance"] / 2, -len(x.get("text", ""))), reverse=True)
        return results[:top_k * 2]

    def _keyword_search(self, queries: list[str], top_k: int) -> list[dict]:
        results = []
        seen = set()
        for query in queries:
            try:
                fts_results = Database.search_blocks_fts(query, limit=top_k * 2)
                for r in fts_results:
                    cid = r["id"]
                    if cid not in seen:
                        seen.add(cid)
                        results.append({
                            "text": r.get("content", ""),
                            "metadata": {
                                "page_id": r.get("page_id", ""),
                                "block_type": r.get("block_type", ""),
                                "properties": r.get("properties", {}),
                            },
                            "distance": 0,
                            "fts_rank": r.get("fts_rank", 0),
                        })
            except Exception as e:
                logging.warning(f"Keyword search failed: {e}")
        return results[:top_k * 2]

    def _blend_search(self, queries: list[str], top_k: int) -> list[dict]:
        w_v = Config.get("rag.hybrid_search.vector_weight", 0.7)
        w_k = Config.get("rag.hybrid_search.keyword_weight", 0.3)

        vec_results = self._vector_search(queries, top_k * 3)
        fts_results = self._keyword_search(queries, top_k * 3)

        merged = {}
        for r in vec_results:
            cid = self._candidate_id(r)
            merged[cid] = {
                "text": r["text"],
                "metadata": r.get("metadata", {}),
                "distance": r.get("distance", 0),
                "vec_score": w_v * max(0, 1 - r.get("distance", 0) / 2),
                "fts_score": 0,
            }
        for r in fts_results:
            cid = self._candidate_id(r)
            raw_rank = r.get("fts_rank", 0)
            normalized = self._normalize_fts_rank(raw_rank)
            if cid in merged:
                merged[cid]["fts_score"] = w_k * min(normalized, 1.0)
                merged[cid]["fts_rank"] = raw_rank
            else:
                merged[cid] = {
                    "text": r["text"],
                    "metadata": r.get("metadata", {}),
                    "distance": r.get("distance", 0),
                    "vec_score": 0,
                    "fts_score": w_k * min(normalized, 1.0),
                    "fts_rank": raw_rank,
                }

        for item in merged.values():
            item["rrf_score"] = item["vec_score"] + item["fts_score"]

        sorted_items = sorted(merged.values(), key=lambda x: x["rrf_score"], reverse=True)
        return self._preserve_keyword_hits(sorted_items, top_k)

    @staticmethod
    def _normalize_fts_rank(raw_rank: float) -> float:
        try:
            rank = float(raw_rank)
        except (TypeError, ValueError):
            return 0
        if rank < 0:
            strength = abs(rank)
            return strength / (strength + 10)
        return min(rank / 10, 1.0)

    @staticmethod
    def _candidate_id(item: dict) -> str:
        page_id = item.get("metadata", {}).get("page_id", "")
        block_id = item.get("id", "")
        if page_id and block_id:
            return page_id + ":" + block_id
        kid = item.get("metadata", {}).get("knowledge_id", "")
        cidx = str(item.get("metadata", {}).get("chunk_index", 0))
        if kid:
            return kid + ":" + cidx
        text = item.get("text", "")
        text_hash = hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()[:12]
        return "text_" + text_hash

    def _preserve_keyword_hits(self, sorted_items: list[dict], top_k: int) -> list[dict]:
        limit = top_k * 2
        selected = list(sorted_items[:limit])
        keyword_items = [
            item for item in sorted_items
            if item.get("fts_score", 0) > 0
        ]
        keep_count = min(3, top_k, len(keyword_items))
        if keep_count == 0:
            return selected

        selected_ids = {self._candidate_id(item) for item in selected}
        for keyword_item in keyword_items[:keep_count]:
            keyword_id = self._candidate_id(keyword_item)
            if keyword_id in selected_ids:
                continue
            if len(selected) < limit:
                selected.append(keyword_item)
            else:
                replace_idx = None
                for i in range(len(selected) - 1, -1, -1):
                    if selected[i].get("fts_score", 0) <= 0:
                        replace_idx = i
                        break
                if replace_idx is None:
                    replace_idx = len(selected) - 1
                selected_ids.discard(self._candidate_id(selected[replace_idx]))
                selected[replace_idx] = keyword_item
            selected_ids.add(keyword_id)

        selected.sort(key=lambda x: x["rrf_score"], reverse=True)
        return selected[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_search.py -v`
Expected: PASS (all tests including new block-level test)

- [ ] **Step 5: Commit**

```bash
git add src/services/hybrid_search.py tests/test_search.py
git commit -m "feat: rewrite HybridSearcher to Block-First architecture (BlockStore + block_fts)"
```

---

### Task 6: RAG 管线 Bug 修复

**Files:**
- Modify: `src/services/rag_pipeline.py:137-165` (Bug #1, #2)
- Modify: `src/services/rag_pipeline.py:245-272` (metadata 适配)

- [ ] **Step 1: Fix Bug #1 — searcher.search() 参数不匹配**

在 `src/services/rag_pipeline.py` 第 147-161 行，将 `VectorSearchStage.execute` 方法替换为：

```python
    async def execute(self, ctx, config):
        if not self.is_enabled(config):
            return ctx
        top_k = config.get("top_k", 10)
        try:
            searcher = HybridSearcher()
            all_results = []
            for query in ctx.rewritten_queries:
                results = searcher.search([query], top_k=top_k)
                all_results.extend(results)
            seen = set()
            unique = []
            for r in all_results:
                rid = r.get("id", r.get("metadata", {}).get("page_id", ""))
                if rid and rid not in seen:
                    seen.add(rid)
                    unique.append(r)
            unique.sort(key=lambda x: x.get("rrf_score", x.get("vec_score", 0)), reverse=True)
            ctx.candidates = unique[:top_k]
        except Exception as e:
            logger.warning("Vector search failed: %s", e)
            ctx.candidates = []
        return ctx
```

- [ ] **Step 2: Fix Bug #2 — 排序字段不存在**

已在 Step 1 中修复：`unique.sort(key=lambda x: x.get("rrf_score", x.get("vec_score", 0)), reverse=True)`

- [ ] **Step 3: Fix metadata 适配 — GenerateStage._build_context_from_filtered**

在 `src/services/rag_pipeline.py` 第 245-272 行，将 `_build_context_from_filtered` 方法替换为：

```python
    def _build_context_from_filtered(self, filtered):
        """批量查询标题，组装上下文和来源列表"""
        kid_map = {}
        for r in filtered:
            meta = r.get("metadata", {}) if isinstance(r.get("metadata"), dict) else {}
            kid = meta.get("page_id", meta.get("knowledge_id", ""))
            if not kid:
                kid = r.get("knowledge_id", "")
            if kid:
                kid_map[kid] = True
        items = Database.get_knowledge_batch(list(kid_map.keys())) if kid_map else {}
        context_parts = []
        sources = []
        for i, result in enumerate(filtered):
            text = result.get("text", result.get("chunk_text", ""))
            context_parts.append(f"[来源{i+1}]\n{text}")
            meta = result.get("metadata", {}) if isinstance(result.get("metadata"), dict) else {}
            kid = meta.get("page_id", meta.get("knowledge_id", ""))
            if not kid:
                kid = result.get("knowledge_id", "")
            item = items.get(kid)
            title = meta.get("title") if isinstance(meta, dict) else result.get("title")
            if not title and item:
                title = item.get("title", "未知")
            if not title:
                title = "未知"
            sources.append({
                "chunk_id": result.get("id", result.get("chunk_id", "")),
                "knowledge_id": kid,
                "title": title,
                "text_preview": text[:200],
                "score": result.get("rerank_score", result.get("rrf_score", result.get("score", result.get("distance", 0)))),
            })
        return context_parts, sources
```

- [ ] **Step 4: Run all tests to verify no regressions**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/rag_pipeline.py
git commit -m "fix: RAG pipeline bugs — searcher params, sort field, block metadata adaptation"
```

---

### Task 7: Container Bug 修复

**Files:**
- Modify: `src/core/container.py:66-127` (Bug #4, #5)

- [ ] **Step 1: Fix Bug #4 — IndexerService 类不存在**

在 `src/services/indexer.py` 末尾追加：

```python
class IndexerService:
    """Indexer 服务类 — 封装 indexer 函数，供 Container DI 使用"""

    def __init__(self, db, vectorstore, embedding, config):
        self._db = db
        self._vectorstore = vectorstore
        self._embedding = embedding
        self._config = config

    def index(self, item: KnowledgeItem):
        index_knowledge_item(item)

    def reindex(self, item_id: str, item: KnowledgeItem):
        reindex_knowledge_item(item_id, item)

    def reindex_all(self, progress_callback=None):
        return reindex_all(progress_callback)
```

- [ ] **Step 2: Fix Bug #5 — LibrarianService 构造函数不匹配**

在 `src/core/container.py` 第 122-127 行，将 `librarian` 属性替换为：

```python
    @property
    def librarian(self):
        if self._librarian is None:
            from src.services.librarian import LibrarianService
            self._librarian = LibrarianService()
        return self._librarian
```

- [ ] **Step 3: Add BlockStore to container**

在 `src/core/container.py` 第 39 行后追加：

```python
    block_store: "BlockStore" = field(default=None)  # noqa: F821
```

在 `create_container()` 函数中（第 152-154 行后），追加：

```python
    from src.services.block_store import BlockStore
    block_store = BlockStore(db=Database)
    logger.info("BlockStore ready")
```

在 `AppContainer(...)` 构造中追加 `block_store=block_store`。

- [ ] **Step 4: Run all tests**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/indexer.py src/core/container.py
git commit -m "fix: container bugs — add IndexerService class, fix LibrarianService constructor, add BlockStore"
```

---

### Task 8: 配置清理

**Files:**
- Modify: `src/utils/config.py:229-233` (删除 `get_chroma_dir`)
- Modify: `src/utils/paths.py` (删除 `chroma_dir`)

- [ ] **Step 1: Delete get_chroma_dir from config.py**

删除 `src/utils/config.py` 第 229-233 行：

```python
    @_dualmethod
    def get_chroma_dir(self) -> Path:
        path = self.get_data_dir() / self.get("storage.chroma_dir", "chroma")
        path.mkdir(parents=True, exist_ok=True)
        return path
```

- [ ] **Step 2: Delete chroma_dir from paths.py**

在 `src/utils/paths.py` 中删除包含 `"chroma_dir": "chroma"` 的行。

- [ ] **Step 3: Run all tests**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/utils/config.py src/utils/paths.py
git commit -m "chore: remove legacy chroma_dir config — no longer used after Block-First migration"
```

---

### Task 9: 迁移脚本

**Files:**
- Create: `scripts/migrate_to_block_store.py`

- [ ] **Step 1: Write the migration script**

```python
# scripts/migrate_to_block_store.py
"""Block-First 向量存储迁移脚本

将现有 vec_chunks + chunk_fts 数据迁移到 vec_blocks + block_fts。

用法:
    python scripts/migrate_to_block_store.py <db_path>              # dry-run（默认）
    python scripts/migrate_to_block_store.py <db_path> --apply      # 执行迁移
    python scripts/migrate_to_block_store.py <db_path> --apply --backfill-vectors  # 迁移+向量回填
"""
import argparse
import json
import logging
import shutil
import sqlite3
import struct
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _load_sqlite_vec(conn: sqlite3.Connection):
    import sqlite_vec
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def _pack_embedding(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


def _analyze(conn: sqlite3.Connection) -> dict:
    stats = {}
    for table in ["knowledge_chunks", "blocks", "vec_chunks", "chunk_fts"]:
        try:
            row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            stats[table] = row[0] if row else 0
        except Exception:
            stats[table] = -1
    for table in ["vec_blocks", "block_fts"]:
        try:
            row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            stats[table] = row[0] if row else 0
        except Exception:
            stats[table] = -1
    return stats


def _backup_database(db_path: str) -> str:
    backup_dir = Path(db_path).parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"kb_{timestamp}.db"
    shutil.copy2(db_path, backup_path)
    logger.info(f"Backup created: {backup_path}")
    return str(backup_path)


def _create_schema(conn: sqlite3.Connection, dimension: int = 1024):
    _load_sqlite_vec(conn)
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_blocks USING vec0("
        f"embedding float[{dimension}] distance_metric=cosine)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS block_fts USING fts5("
        "fts_segmented, page_id UNINDEXED, block_id UNINDEXED, tokenize='unicode61')"
    )
    conn.commit()
    logger.info("Schema created: vec_blocks, block_fts")


def _migrate_blocks(conn: sqlite3.Connection):
    rows = conn.execute(
        """SELECT kc.id, kc.knowledge_id, kc.chunk_index, kc.chunk_text, kc.created_at
           FROM knowledge_chunks kc
           WHERE kc.id NOT IN (SELECT id FROM blocks)"""
    ).fetchall()
    if not rows:
        logger.info("No missing blocks to migrate")
        return 0

    now = datetime.now().isoformat()
    count = 0
    for row in rows:
        chunk_id, knowledge_id, chunk_index, chunk_text, created_at = row
        created_at = created_at or now
        conn.execute(
            """INSERT OR IGNORE INTO blocks
               (id, parent_id, page_id, content, block_type, properties, order_idx, created_at, updated_at)
               VALUES (?, NULL, ?, ?, 'text', ?, ?, ?, ?)""",
            (chunk_id, knowledge_id, chunk_text,
             json.dumps({"knowledge_id": knowledge_id, "chunk_index": chunk_index or 0}, ensure_ascii=False),
             chunk_index or 0, created_at, created_at),
        )
        conn.execute(
            """INSERT OR IGNORE INTO block_property_index
               (block_id, prop_key, prop_value, value_type)
               VALUES (?, 'knowledge_id', ?, 'ref')""",
            (chunk_id, knowledge_id),
        )
        count += 1
    conn.commit()
    logger.info(f"Migrated {count} blocks")
    return count


def _migrate_vectors(conn: sqlite3.Connection):
    _load_sqlite_vec(conn)
    rows = conn.execute(
        """SELECT kc.id, vc.embedding
           FROM vec_chunks vc
           JOIN knowledge_chunks kc ON kc.rowid = vc.rowid
           WHERE kc.id NOT IN (
               SELECT b.id FROM blocks b
               JOIN vec_blocks vb ON vb.rowid = b.rowid
           )"""
    ).fetchall()
    if not rows:
        logger.info("No missing vectors to migrate")
        return 0

    count = 0
    for chunk_id, embedding_blob in rows:
        block_row = conn.execute(
            "SELECT rowid FROM blocks WHERE id = ?", (chunk_id,)
        ).fetchone()
        if block_row:
            conn.execute(
                "INSERT OR REPLACE INTO vec_blocks(rowid, embedding) VALUES (?, ?)",
                (block_row[0], embedding_blob),
            )
            count += 1
    conn.commit()
    logger.info(f"Migrated {count} vectors")
    return count


def _migrate_fts(conn: sqlite3.Connection):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.utils.chinese_tokenizer import tokenize_chinese_full

    rows = conn.execute(
        """SELECT b.id, b.page_id, b.content FROM blocks b
           WHERE b.id NOT IN (SELECT block_id FROM block_fts)"""
    ).fetchall()
    if not rows:
        logger.info("No missing FTS entries to migrate")
        return 0

    count = 0
    for block_id, page_id, content in rows:
        segmented = tokenize_chinese_full(content or "")
        conn.execute(
            "INSERT INTO block_fts(fts_segmented, page_id, block_id) VALUES (?, ?, ?)",
            (segmented, page_id, block_id),
        )
        count += 1
    conn.commit()
    logger.info(f"Migrated {count} FTS entries")
    return count


def _backfill_vectors(conn: sqlite3.Connection):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.services.embedding import EmbeddingService

    rows = conn.execute(
        """SELECT b.id, b.content FROM blocks b
           WHERE b.rowid NOT IN (SELECT rowid FROM vec_blocks)"""
    ).fetchall()
    if not rows:
        logger.info("No missing vectors to backfill")
        return 0

    _load_sqlite_vec(conn)
    svc = EmbeddingService()
    count = 0
    batch_size = 20
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        texts = [r[1] for r in batch]
        try:
            embeddings = svc.embed_batch(texts)
            for (block_id, _), emb in zip(batch, embeddings):
                block_row = conn.execute(
                    "SELECT rowid FROM blocks WHERE id = ?", (block_id,)
                ).fetchone()
                if block_row:
                    conn.execute(
                        "INSERT OR REPLACE INTO vec_blocks(rowid, embedding) VALUES (?, ?)",
                        (block_row[0], _pack_embedding(emb)),
                    )
                    count += 1
        except Exception as e:
            logger.error(f"Backfill failed for batch {i}: {e}")
    conn.commit()
    logger.info(f"Backfilled {count} vectors via API")
    return count


def _verify(conn: sqlite3.Connection) -> bool:
    stats = _analyze(conn)
    ok = True
    blocks = stats.get("blocks", 0)
    vec_blocks = stats.get("vec_blocks", 0)
    block_fts = stats.get("block_fts", 0)

    if blocks > 0 and vec_blocks == 0:
        logger.warning(f"MISMATCH: {blocks} blocks but 0 vec_blocks")
        ok = False
    elif blocks > 0 and abs(blocks - vec_blocks) > blocks * 0.1:
        logger.warning(f"MISMATCH: {blocks} blocks vs {vec_blocks} vec_blocks (>10% diff)")
        ok = False
    else:
        logger.info(f"OK: {blocks} blocks, {vec_blocks} vec_blocks, {block_fts} block_fts")

    return ok


def main():
    parser = argparse.ArgumentParser(description="Block-First vector store migration")
    parser.add_argument("db_path", help="Path to SQLite database file")
    parser.add_argument("--apply", action="store_true", help="Execute migration (default: dry-run)")
    parser.add_argument("--backfill-vectors", action="store_true", help="Backfill missing vectors via API")
    parser.add_argument("--no-backup", action="store_true", help="Skip database backup")
    parser.add_argument("--dimension", type=int, default=1024, help="Vector dimension (default: 1024)")
    args = parser.parse_args()

    db_path = args.db_path
    if not Path(db_path).exists():
        logger.error(f"Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    stats = _analyze(conn)
    logger.info("Current state:")
    for table, count in stats.items():
        logger.info(f"  {table}: {count}")

    if not args.apply:
        logger.info("Dry-run complete. Use --apply to execute migration.")
        conn.close()
        return

    if not args.no_backup:
        _backup_database(db_path)

    _create_schema(conn, args.dimension)
    _migrate_blocks(conn)
    _migrate_vectors(conn)
    _migrate_fts(conn)

    if args.backfill_vectors:
        _backfill_vectors(conn)

    ok = _verify(conn)
    conn.close()

    if ok:
        logger.info("Migration completed successfully!")
    else:
        logger.warning("Migration completed with warnings. Check the output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test the migration script (dry-run)**

Run: `python scripts/migrate_to_block_store.py data/kb.db`
Expected: Prints current state and "Dry-run complete"

- [ ] **Step 3: Commit**

```bash
git add scripts/migrate_to_block_store.py
git commit -m "feat: add Block-First vector store migration script"
```

---

### Task 10: conftest.py 更新 + 全量测试

**Files:**
- Modify: `tests/conftest.py:26-34` (重置 BlockStore 单例)

- [ ] **Step 1: Update conftest.py**

在 `tests/conftest.py` 的 `setup_db` fixture 中，将 VectorStore 重置部分替换为：

```python
    # Reset VectorStore and BlockStore singletons so each test gets fresh state
    from src.services.vectorstore import VectorStore
    from src.services.block_store import BlockStore
    VectorStore._instance = None
    VectorStore._initialized = False
    BlockStore._instance = None
    BlockStore._initialized = False
    yield
    Database.close()
    Database._instance = None
    VectorStore._instance = None
    VectorStore._initialized = False
    BlockStore._instance = None
    BlockStore._initialized = False
```

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -v`
Expected: PASS (all tests)

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: update conftest to reset BlockStore singleton"
```

---

## Self-Review

**1. Spec coverage:**
- [x] BlockStore 类 → Task 1
- [x] Database 新增方法 → Task 2
- [x] EmbeddingCache 接入 → Task 3
- [x] Indexer 管线改造 → Task 4
- [x] HybridSearcher 改造 → Task 5
- [x] RAG 管线 Bug 修复 → Task 6
- [x] Container Bug 修复 → Task 7
- [x] 配置清理 → Task 8
- [x] 迁移脚本 → Task 9
- [x] 测试 → Task 10

**2. Placeholder scan:** No TBD/TODO found. All code blocks are complete.

**3. Type consistency:**
- `BlockStore.add_block_embedding(block_id, embedding)` — consistent across Tasks 1, 4
- `BlockStore.delete_by_page(page_id)` — consistent across Tasks 1, 4
- `Database.insert_blocks(blocks)` — consistent across Tasks 2, 4
- `Database.search_blocks_fts(query, limit)` — consistent across Tasks 2, 5
- `Database.delete_blocks_by_page(page_id)` — consistent across Tasks 2, 4
- `EmbeddingService.embed_batch_with_cache(texts)` — consistent across Tasks 3, 4
- Metadata keys: `page_id`, `block_type`, `properties` — consistent across Tasks 1, 5, 6

All checks pass.
