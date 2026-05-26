# 搜索架构全面重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将知识库搜索架构从 ChromaDB + SQLite FTS5(unicode61) 重构为 sqlite-vec + jieba 预分词 + 加分融合混合搜索，参考 MaxKB 的成熟架构。

**Architecture:** 用 sqlite-vec 替代 ChromaDB 做向量存储，jieba 全模式分词替代 unicode61 做中文全文索引，三种搜索模式（embedding/keywords/blend）通过配置切换，混合搜索使用加分融合公式。

**Tech Stack:** sqlite-vec 0.1.9 (pip), jieba (已有), SQLite FTS5, Python 3.14

---

## 文件结构

| 文件 | 责任 |
|------|------|
| `src/services/vectorstore.py` | 全面重写：sqlite-vec 向量存储 |
| `src/services/db.py` | 大改：schema 迁移、chunk_fts 重建、FTS 查询清洗 |
| `src/services/indexer.py` | 大改：原子同步写入、错误处理、reindex_all |
| `src/services/hybrid_search.py` | 重写：搜索模式策略 + 加分融合 |
| `src/mcp_server.py` | 中改：search 逻辑调整、新增 reindex_all 工具 |
| `src/utils/chinese_tokenizer.py` | 小改：新增全模式分词、FTS 查询清洗 |
| `src/services/rag.py` | 小改：适配新 HybridSearcher |
| `config.yaml` | 小改：新增 search_mode |
| `pyproject.toml` | 小改：chromadb → sqlite-vec |
| `tests/test_vectorstore.py` | 新建：向量存储测试 |
| `tests/test_search.py` | 新建：搜索集成测试 |
| `tests/conftest.py` | 小改：适配新 VectorStore |

---

### Task 1: 依赖变更与 FTS 查询清洗工具

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/utils/chinese_tokenizer.py`
- Create: `tests/test_tokenizer.py`

- [ ] **Step 1: 更新 pyproject.toml 依赖**

将 `chromadb>=0.4.22` 替换为 `sqlite-vec>=0.1.6`：

```toml
dependencies = [
    "fastmcp>=2.0",
    "sqlite-vec>=0.1.6",
    "openai>=1.0",
    "langchain-text-splitters>=0.0.1",
    "PyYAML>=6.0",
    "httpx>=0.27",
    "jieba>=0.42.1",
]
```

- [ ] **Step 2: 在 chinese_tokenizer.py 新增两个函数**

在现有 `tokenize_chinese` 函数后追加：

```python
def tokenize_chinese_full(text: str) -> str:
    """jieba 全模式分词（MaxKB 风格），返回空格分隔词组。
    全模式会产出所有可能的词组组合，适合 FTS 索引。"""
    import jieba
    words = jieba.lcut(text, cut_all=True)
    return " ".join(w.strip() for w in words if w.strip())


def sanitize_fts_query(query: str, is_tokenized: bool = False) -> str:
    """清洗 FTS5 MATCH 查询字符串，避免特殊字符导致语法错误。

    Args:
        query: 原始查询或 jieba 分词后的空格分隔文本
        is_tokenized: True 表示输入已是分词结果（每个词独立引用后 OR 连接）
    """
    if not query or not query.strip():
        return ""
    if is_tokenized:
        tokens = query.strip().split()
        if not tokens:
            return ""
        parts = []
        for t in tokens:
            clean = t.replace('"', "")
            if clean:
                parts.append(f'"{clean}"')
        return " OR ".join(parts)
    else:
        clean = query.strip().replace('"', "")
        if not clean:
            return ""
        return f'"{clean}"'
```

- [ ] **Step 3: 写 tokenizer 测试**

Create `tests/test_tokenizer.py`:

```python
"""分词和 FTS 查询清洗测试"""
from src.utils.chinese_tokenizer import (
    tokenize_chinese,
    tokenize_chinese_full,
    sanitize_fts_query,
)


class TestTokenizeChineseFull:
    def test_chinese(self):
        result = tokenize_chinese_full("企微集约运营情况汇总表")
        tokens = result.split()
        assert "企微" in tokens
        assert "集约" in tokens
        assert "运营" in tokens

    def test_mixed(self):
        result = tokenize_chinese_full("2025-2026年企微运营")
        tokens = result.split()
        assert len(tokens) > 0
        assert any("2025" in t for t in tokens)

    def test_empty(self):
        assert tokenize_chinese_full("") == ""
        assert tokenize_chinese_full("   ") == ""


class TestSanitizeFtsQuery:
    def test_plain_query(self):
        assert sanitize_fts_query("企微运营") == '"企微运营"'

    def test_hyphen(self):
        assert sanitize_fts_query("2025-2026") == '"2025-2026"'

    def test_parentheses(self):
        result = sanitize_fts_query("订正版(修正)")
        assert result == '"订正版(修正)"'

    def test_tokenized_input(self):
        result = sanitize_fts_query("企微 集约 运营", is_tokenized=True)
        assert result == '"企微" OR "集约" OR "运营"'

    def test_empty(self):
        assert sanitize_fts_query("") == ""
        assert sanitize_fts_query("   ") == ""

    def test_quotes_in_input(self):
        result = sanitize_fts_query('含"引号"内容')
        assert '"' not in result[1:-1] or result.count('"') == 2
```

- [ ] **Step 4: 运行测试**

Run: `cd D:\ClaudeCodeWorkSpace\projects\knowledge-base && python -m pytest tests/test_tokenizer.py -v`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml src/utils/chinese_tokenizer.py tests/test_tokenizer.py
git commit -m "feat: 添加 sqlite-vec 依赖、jieba 全模式分词、FTS 查询清洗"
```

---

### Task 2: VectorStore 全面重写（ChromaDB → sqlite-vec）

**Files:**
- Rewrite: `src/services/vectorstore.py`
- Create: `tests/test_vectorstore.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: 重写 vectorstore.py**

完全替换 `src/services/vectorstore.py` 内容：

```python
"""向量存储 — 基于 sqlite-vec，与 SQLite 数据库共享连接"""
import json
import logging
import struct
import threading

import sqlite_vec

from src.utils.config import Config

_lock = threading.Lock()
_VEC_DIM = 1024  # bge-m3 输出维度


class VectorStore:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def _ensure_table(self):
        if self._initialized:
            return
        with _lock:
            if self._initialized:
                return
            from src.services.db import Database
            conn = Database.get_conn()
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0("
                f"embedding float[{_VEC_DIM}] distance_metric=cosine)"
            )
            conn.commit()
            self._initialized = True

    def _pack_embedding(self, embedding: list[float]) -> bytes:
        return struct.pack(f"{len(embedding)}f", *embedding)

    def add_chunk_embedding(self, chunk_id: str, knowledge_id: str,
                            embedding: list[float], metadata: dict | None = None):
        self._ensure_table()
        from src.services.db import Database
        conn = Database.get_conn()
        # sqlite-vec 使用 integer rowid，用 knowledge_chunks 的 rowid
        row = conn.execute(
            "SELECT rowid FROM knowledge_chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
        if not row:
            logging.warning(f"Chunk {chunk_id} not found, skip vec insert")
            return
        rowid = row[0]
        conn.execute(
            "INSERT OR REPLACE INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
            (rowid, self._pack_embedding(embedding)),
        )
        conn.commit()

    def add_chunks(self, chunks: list[dict]):
        """兼容旧接口：批量写入（由 indexer 调用）。
        chunks 中每个元素需包含 id, knowledge_id, embedding"""
        for c in chunks:
            emb = c.get("embedding")
            if emb:
                self.add_chunk_embedding(
                    c["id"], c.get("knowledge_id", ""), emb, c.get("metadata")
                )

    def search(self, query: str, top_k: int = 5, tags: list[str] | None = None,
               query_embedding: list[float] | None = None) -> list[dict]:
        self._ensure_table()
        if query_embedding is None:
            from src.services.embedding import EmbeddingService
            query_embedding = EmbeddingService().embed(query)

        from src.services.db import Database
        conn = Database.get_conn()
        packed = self._pack_embedding(query_embedding)
        rows = conn.execute(
            """SELECT kc.id, kc.knowledge_id, kc.chunk_text,
                      vc.distance
               FROM vec_chunks vc
               JOIN knowledge_chunks kc ON kc.rowid = vc.rowid
               WHERE vc.embedding MATCH ? AND k = ?
               ORDER BY vc.distance""",
            (packed, top_k),
        ).fetchall()

        results = []
        for r in rows:
            meta = {"knowledge_id": r[1], "chunk_index": 0}
            results.append({
                "id": r[0],
                "text": r[2],
                "metadata": meta,
                "distance": r[3],
            })
        return results

    def delete_by_knowledge(self, knowledge_id: str):
        self._ensure_table()
        from src.services.db import Database
        conn = Database.get_conn()
        rows = conn.execute(
            "SELECT rowid FROM knowledge_chunks WHERE knowledge_id = ?",
            (knowledge_id,),
        ).fetchall()
        if rows:
            rowids = ",".join(str(r[0]) for r in rows)
            conn.execute(
                f"DELETE FROM vec_chunks WHERE rowid IN ({rowids})"
            )
            conn.commit()

    def count(self) -> int:
        self._ensure_table()
        from src.services.db import Database
        conn = Database.get_conn()
        row = conn.execute("SELECT count(*) FROM vec_chunks").fetchone()
        return row[0] if row else 0
```

- [ ] **Step 2: 创建 test_vectorstore.py**

Create `tests/test_vectorstore.py`:

```python
"""sqlite-vec 向量存储测试"""
import struct
from src.services.vectorstore import VectorStore
from src.services.db import Database
from src.models.knowledge import KnowledgeItem


def _embed(size=4):
    return [0.1] * size


class TestVectorStore:
    def test_add_and_search(self, sample_item, monkeypatch):
        # 插入知识条目和 chunk
        Database.insert_knowledge(sample_item.to_row())
        chunk_id = "chunk-test-001"
        Database.insert_chunks([{
            "id": chunk_id, "knowledge_id": sample_item.id,
            "chunk_index": 0, "chunk_text": "测试文本",
            "created_at": "2026-01-01T00:00:00",
        }])

        vs = VectorStore()
        # mock embedding 为固定 1024 维
        fake_emb = [0.1] * 1024
        vs.add_chunk_embedding(chunk_id, sample_item.id, fake_emb)
        assert vs.count() >= 1

    def test_delete_by_knowledge(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        chunk_id = "chunk-del-001"
        Database.insert_chunks([{
            "id": chunk_id, "knowledge_id": sample_item.id,
            "chunk_index": 0, "chunk_text": "删除测试",
            "created_at": "2026-01-01T00:00:00",
        }])

        vs = VectorStore()
        fake_emb = [0.2] * 1024
        vs.add_chunk_embedding(chunk_id, sample_item.id, fake_emb)
        count_before = vs.count()

        vs.delete_by_knowledge(sample_item.id)
        assert vs.count() < count_before
```

- [ ] **Step 3: 更新 conftest.py 适配新 VectorStore**

修改 `tests/conftest.py` 中 `setup_db` fixture，将 VectorStore 重置改为：

```python
@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    db_path = tmp_path / "test.db"
    Config.load()
    Config.set("storage.data_dir", str(tmp_path))
    Config.set("storage.db_name", "test.db")
    Database._conn = None
    Database._instance = None
    Database.connect(str(db_path))
    import src.api.auth as auth_mod
    auth_mod._users_db.clear()
    from src.services.vectorstore import VectorStore
    VectorStore._instance = None
    VectorStore._initialized = False
    yield
    Database.close()
    Database._instance = None
    VectorStore._instance = None
    VectorStore._initialized = False
```

- [ ] **Step 4: 运行测试**

Run: `cd D:\ClaudeCodeWorkSpace\projects\knowledge-base && python -m pytest tests/test_vectorstore.py tests/test_db.py -v`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/vectorstore.py tests/test_vectorstore.py tests/conftest.py
git commit -m "feat: 重写 VectorStore 使用 sqlite-vec 替代 ChromaDB"
```

---

### Task 3: 数据库 Schema 迁移 — 修复 chunk_fts + 新增列

**Files:**
- Modify: `src/services/db.py`

- [ ] **Step 1: 修改 _SCHEMA 中的 chunk_fts 定义**

在 `db.py` 的 `_SCHEMA` 字符串中，替换旧 chunk_fts 定义：

```sql
-- 旧:
-- CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
--     chunk_text_segmented,
--     knowledge_id,
--     content=knowledge_chunks,
--     content_rowid=rowid,
--     tokenize='unicode61'
-- );

-- 新:
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    fts_segmented,
    knowledge_id UNINDEXED,
    tokenize='unicode61'
);
```

注意：`content=` 绑定已移除，变为独立 FTS 表。列名从 `chunk_text_segmented` 改为 `fts_segmented`。`knowledge_id` 标记 `UNINDEXED` 用于精确删除。

- [ ] **Step 2: 在 _migrate() 中添加迁移逻辑**

在 `_migrate()` 方法末尾追加：

```python
# chunk_fts 重建：检测旧 schema（含 content=knowledge_chunks）
chunk_fts_sql = cls._conn.execute(
    "SELECT sql FROM sqlite_master WHERE name='chunk_fts'"
).fetchone()
if chunk_fts_sql and 'content=knowledge_chunks' in (chunk_fts_sql[0] or ''):
    cls._conn.execute("DROP TABLE IF EXISTS chunk_fts")
    cls._conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5("
        "fts_segmented, knowledge_id UNINDEXED, tokenize='unicode61')"
    )
    cls._conn.commit()
    logging.getLogger(__name__).info("chunk_fts schema migrated, reindex needed")
```

文件顶部添加 `import logging`（如果没有的话）。

- [ ] **Step 3: 修改 insert_chunks_fts 方法**

替换现有 `insert_chunks_fts` 方法：

```python
@classmethod
def insert_chunks_fts(cls, chunks: list[dict]):
    """将 chunk 文本用 jieba 分词后写入 chunk_fts（独立表，无 content= 绑定）"""
    from src.utils.chinese_tokenizer import tokenize_chinese_full
    conn = cls.get_conn()
    for c in chunks:
        segmented = tokenize_chinese_full(c["chunk_text"])
        conn.execute(
            "INSERT INTO chunk_fts(fts_segmented, knowledge_id) VALUES (?, ?)",
            (segmented, c["knowledge_id"]),
        )
    conn.commit()
```

注意：`tokenize_chinese` 改为 `tokenize_chinese_full`（全模式分词），列名改为 `fts_segmented`，不再需要 rowid 子查询。

- [ ] **Step 4: 修改 delete_chunks_fts 方法**

替换现有 `delete_chunks_fts` 方法：

```python
@classmethod
def delete_chunks_fts(cls, knowledge_id: str):
    """删除指定知识的 chunk FTS 记录"""
    cls.get_conn().execute(
        "DELETE FROM chunk_fts WHERE knowledge_id = ?", (knowledge_id,)
    )
    cls.get_conn().commit()
```

注意：`UNINDEXED` 列支持 WHERE 精确匹配，无需旧的 `'delete'` 命令。

- [ ] **Step 5: 修改 search_chunks_fts — 使用查询清洗**

替换现有 `search_chunks_fts` 方法：

```python
@classmethod
def search_chunks_fts(cls, query: str, limit: int = 20) -> list[dict]:
    """使用 jieba 分词后的 chunk 级 FTS 搜索"""
    from src.utils.chinese_tokenizer import tokenize_chinese, sanitize_fts_query
    tokenized_query = tokenize_chinese(query)
    if not tokenized_query.strip():
        return []
    safe_query = sanitize_fts_query(tokenized_query, is_tokenized=True)
    if not safe_query:
        return []
    conn = cls.get_conn()
    try:
        rows = conn.execute(
            """SELECT kc.id, kc.knowledge_id, kc.chunk_index, kc.chunk_text, rank as fts_rank
               FROM chunk_fts cf
               JOIN knowledge_chunks kc ON kc.rowid = cf.rowid
               WHERE chunk_fts MATCH ?
               ORDER BY fts_rank LIMIT ?""",
            (safe_query, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
```

- [ ] **Step 6: 修改 search_knowledge — 使用查询清洗**

替换现有 `search_knowledge` 方法：

```python
@classmethod
def search_knowledge(cls, query: str, limit: int = 20, offset: int = 0) -> list[dict]:
    from src.utils.chinese_tokenizer import sanitize_fts_query
    conn = cls.get_conn()
    try:
        safe_query = sanitize_fts_query(query)
        if safe_query:
            fts_rows = conn.execute(
                """SELECT ki.*, rank as fts_rank FROM knowledge_fts kf
                   JOIN knowledge_items ki ON ki.rowid = kf.rowid
                   WHERE knowledge_fts MATCH ? ORDER BY fts_rank LIMIT ? OFFSET ?""",
                (safe_query, limit, offset),
            ).fetchall()
            if fts_rows:
                return [dict(r) for r in fts_rows]
    except Exception:
        pass
    rows = conn.execute(
        "SELECT * FROM knowledge_items WHERE title LIKE ? OR content LIKE ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
        (f"%{query}%", f"%{query}%", limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 7: 修改 search_wiki_fts — 使用查询清洗**

替换现有 `search_wiki_fts` 方法：

```python
@classmethod
def search_wiki_fts(cls, query: str, limit: int = 10) -> list[dict]:
    from src.utils.chinese_tokenizer import sanitize_fts_query
    try:
        safe_query = sanitize_fts_query(query)
        if not safe_query:
            return []
        rows = cls.get_conn().execute(
            """SELECT wp.*, rank as fts_rank FROM wiki_fts wf
               JOIN wiki_pages wp ON wp.rowid = wf.rowid
               WHERE wiki_fts MATCH ? AND wp.status = 'active'
               ORDER BY fts_rank LIMIT ?""",
            (safe_query, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
```

- [ ] **Step 8: 运行现有测试确保不破坏**

Run: `cd D:\ClaudeCodeWorkSpace\projects\knowledge-base && python -m pytest tests/test_db.py -v`
Expected: ALL PASS

- [ ] **Step 9: 提交**

```bash
git add src/services/db.py
git commit -m "fix: 重建 chunk_fts schema、FTS 查询清洗、jieba 全模式分词"
```

---

### Task 4: Indexer 重写 — 原子同步写入 + reindex_all

**Files:**
- Rewrite: `src/services/indexer.py`

- [ ] **Step 1: 重写 indexer.py**

完全替换 `src/services/indexer.py` 内容：

```python
"""知识条目索引 — 分块 + 向量化 + 全文索引（原子同步写入）"""
import logging
from concurrent.futures import ThreadPoolExecutor

from src.utils.config import Config
from src.models.knowledge import KnowledgeItem, KnowledgeChunk
from src.services.db import Database
from src.services.text_splitter import split_text, split_markdown, split_code
from src.services.vectorstore import VectorStore


def index_knowledge_item(item: KnowledgeItem):
    """将知识条目分块并存入 DB + 向量库 + 全文索引（同步写入）"""
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

    chunk_rows = []
    for c in chunks:
        chunk = KnowledgeChunk(knowledge_id=item.id, chunk_index=c.index, chunk_text=c.text)
        chunk_rows.append(chunk.to_row())

    Database.insert_chunks(chunk_rows)

    # 计算所有 chunk 的 embedding（批量调用 API）
    texts = [c.text for c in chunks]
    try:
        from src.services.embedding import EmbeddingService
        embeddings = EmbeddingService().embed_batch(texts)
    except Exception as e:
        logging.error(f"Embedding failed for {item.id}: {e}")
        embeddings = [None] * len(texts)

    # 写入 chunk_fts + vec_chunks（同步执行）
    for i, c in enumerate(chunks):
        chunk_id = chunk_rows[i]["id"]
        emb = embeddings[i] if i < len(embeddings) else None
        if emb:
            try:
                VectorStore().add_chunk_embedding(chunk_id, item.id, emb)
            except Exception as e:
                logging.error(f"Vec insert failed for chunk {chunk_id}: {e}")

    # FTS 写入（在所有 chunk 准备好后一次性写入）
    chunk_dicts = [{"id": chunk_rows[i]["id"], "knowledge_id": item.id,
                    "chunk_text": c.text} for i, c in enumerate(chunks)]
    try:
        Database.insert_chunks_fts(chunk_dicts)
    except Exception as e:
        logging.error(f"FTS insert failed for {item.id}: {e}")


def reindex_knowledge_item(item_id: str, item: KnowledgeItem):
    """删除旧索引，重新分块索引"""
    VectorStore().delete_by_knowledge(item_id)
    Database.delete_chunks_fts(item_id)
    conn = Database.get_conn()
    conn.execute("DELETE FROM knowledge_chunks WHERE knowledge_id = ?", (item_id,))
    conn.commit()
    index_knowledge_item(item)


def reindex_all() -> dict:
    """重建所有知识条目的索引（向量 + FTS）"""
    items = Database.list_knowledge(limit=100000)
    total = len(items)
    success = 0
    failed = 0
    errors = []

    for i, row in enumerate(items):
        try:
            item = KnowledgeItem(
                id=row["id"],
                title=row["title"],
                content=row.get("content", ""),
                tags=row.get("tags", "[]") if isinstance(row.get("tags"), str) else row.get("tags", []),
                source_type=row.get("source_type", "manual"),
                source_path=row.get("source_path", ""),
                file_type=row.get("file_type", "txt"),
            )
            reindex_knowledge_item(row["id"], item)
            success += 1
            if (i + 1) % 10 == 0:
                logging.info(f"Reindex progress: {i + 1}/{total}")
        except Exception as e:
            failed += 1
            errors.append({"id": row["id"], "title": row["title"], "error": str(e)})
            logging.error(f"Reindex failed for {row.get('title', '')}: {e}")

    return {"total": total, "success": success, "failed": failed, "errors": errors[:10]}
```

关键改动：
1. 移除 ThreadPoolExecutor 并发写入，改为同步顺序写入确保原子性
2. 批量调用 embedding API 后逐个写入 vec_chunks
3. 新增 `reindex_all()` 函数
4. 所有异常记录 logging 而非静默吞掉

- [ ] **Step 2: 运行现有测试**

Run: `cd D:\ClaudeCodeWorkSpace\projects\knowledge-base && python -m pytest tests/ -v --ignore=tests/test_api.py`
Expected: ALL PASS

- [ ] **Step 3: 提交**

```bash
git add src/services/indexer.py
git commit -m "refactor: 重写 indexer 原子同步写入，新增 reindex_all"
```

---

### Task 5: HybridSearch 重写 — 搜索模式策略 + 加分融合

**Files:**
- Rewrite: `src/services/hybrid_search.py`
- Modify: `config.yaml`

- [ ] **Step 1: 更新 config.yaml 搜索配置**

在 `rag:` 节下添加 `search_mode`：

```yaml
rag:
  search_mode: blend  # embedding | keywords | blend
  chunk_overlap: 150
  chunk_size: 1000
  enable_query_rewriting: true
  enable_rerank: true
  hybrid_search:
    candidate_multiplier: 3
    enabled: true
    keyword_weight: 0.3
    vector_weight: 0.7
  rerank:
    min_score: 0.3
    top_n: 5
  score_threshold: 0.35
  top_k: 5
```

- [ ] **Step 2: 重写 hybrid_search.py**

完全替换 `src/services/hybrid_search.py` 内容：

```python
"""混合检索模块 — 三种搜索模式（embedding/keywords/blend）+ 加分融合"""
import logging

from src.utils.config import Config
from src.services.vectorstore import VectorStore
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
                vec_results = VectorStore().search(query, top_k=top_k * 2)
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
        results.sort(key=lambda x: x["distance"])
        return results[:top_k * 2]

    def _keyword_search(self, queries: list[str], top_k: int) -> list[dict]:
        results = []
        seen = set()
        for query in queries:
            try:
                fts_results = Database.search_chunks_fts(query, limit=top_k * 2)
                for r in fts_results:
                    cid = r["id"]
                    if cid not in seen:
                        seen.add(cid)
                        results.append({
                            "text": r.get("chunk_text", ""),
                            "metadata": {
                                "knowledge_id": r.get("knowledge_id", ""),
                                "chunk_index": r.get("chunk_index", 0),
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
            cid = r.get("metadata", {}).get("knowledge_id", "") + str(r.get("metadata", {}).get("chunk_index", ""))
            if not cid:
                cid = r.get("text", "")[:50]
            merged[cid] = {
                "text": r["text"],
                "metadata": r.get("metadata", {}),
                "distance": r.get("distance", 0),
                "vec_score": w_v * max(0, 1 - r.get("distance", 0) / 2),
                "fts_score": 0,
            }
        for r in fts_results:
            cid = r.get("metadata", {}).get("knowledge_id", "") + str(r.get("metadata", {}).get("chunk_index", ""))
            if not cid:
                cid = r.get("text", "")[:50]
            # 归一化 FTS rank（rank 通常为负数，越小越好）
            raw_rank = r.get("fts_rank", 0)
            normalized = max(0, 1 + raw_rank / 10) if raw_rank < 0 else max(0, raw_rank / 10)
            if cid in merged:
                merged[cid]["fts_score"] = w_k * min(normalized, 1.0)
            else:
                merged[cid] = {
                    "text": r["text"],
                    "metadata": r.get("metadata", {}),
                    "distance": r.get("distance", 0),
                    "vec_score": 0,
                    "fts_score": w_k * min(normalized, 1.0),
                }

        for item in merged.values():
            item["rrf_score"] = item["vec_score"] + item["fts_score"]

        sorted_items = sorted(merged.values(), key=lambda x: x["rrf_score"], reverse=True)
        return sorted_items[:top_k * 2]
```

关键设计：
- `_vector_search`: 调用 sqlite-vec 向量搜索，cosine distance 范围 0-2
- `_keyword_search`: 调用 `search_chunks_fts`（已用 jieba 分词 + 清洗）
- `_blend_search`: 加分融合 `vec_score + fts_score`，参考 MaxKB 的 `(1-d) + ts_rank` 思路
- cosine distance 归一化：`max(0, 1 - distance/2)` 映射到 0-1

- [ ] **Step 3: 运行测试**

Run: `cd D:\ClaudeCodeWorkSpace\projects\knowledge-base && python -m pytest tests/ -v --ignore=tests/test_api.py`
Expected: ALL PASS

- [ ] **Step 4: 提交**

```bash
git add src/services/hybrid_search.py config.yaml
git commit -m "feat: 重写 HybridSearch 三模式搜索 + 加分融合"
```

---

### Task 6: MCP Server 适配 + reindex_all 工具

**Files:**
- Modify: `src/mcp_server.py`

- [ ] **Step 1: 修改 _do_search 函数 — 向量空结果时增加回退**

替换 `mcp_server.py` 中的 `_do_search` 函数：

```python
def _do_search(query: str, top_k: int) -> list[dict]:
    """同步执行的搜索逻辑"""
    output = []

    # Wiki 结构化知识优先
    try:
        wiki_results = Database.search_wiki_fts(query, limit=3)
        for wr in wiki_results:
            summary = wr.get("concept_summary", "")
            content_preview = (wr.get("content", "") or "")[:300]
            output.append({
                "source": "wiki",
                "title": wr["title"],
                "summary": summary,
                "text": f"[Wiki] {wr['title']}: {summary}\n{content_preview}",
                "score": wr.get("fts_rank", 0),
            })
    except Exception:
        pass

    # 向量搜索
    vec_results = []
    vec_error = False
    try:
        vec_results = VectorStore().search(query, top_k=top_k)
    except Exception:
        vec_error = True

    if vec_results:
        for r in vec_results:
            kid = (r.get("metadata") or {}).get("knowledge_id", "")
            item = Database.get_knowledge(kid) if kid else None
            output.append({
                "source": "knowledge",
                "chunk_id": r["id"],
                "knowledge_id": kid,
                "title": item["title"] if item else "未知",
                "text": r["text"],
                "score": r["distance"],
            })
    else:
        # 向量搜索无结果或出错 → FTS 回退
        try:
            fts_results = Database.search_knowledge(query, limit=top_k)
            for item in fts_results:
                output.append({
                    "source": "knowledge_fts",
                    "knowledge_id": item["id"],
                    "title": item["title"],
                    "text": (item.get("content", "") or "")[:500],
                    "score": 0,
                })
        except Exception:
            pass

    return output
```

关键改动：
1. 移除 threading.Thread 超时机制（sqlite-vec 不会死锁）
2. 向量搜索返回空结果时直接走 FTS 回退
3. 代码更简洁

- [ ] **Step 2: 修改 server_lifespan — 移除 ChromaDB 预初始化**

替换 `server_lifespan` 函数：

```python
@asynccontextmanager
async def server_lifespan(server: FastMCP):
    global _heartbeat_task
    Config.load()
    Database.connect()
    beat()
    _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    yield {}
    _heartbeat_task.cancel()
    Database.close()
```

- [ ] **Step 3: 新增 reindex_all MCP 工具**

在 `mcp_server.py` 中 `tags()` 工具之后、`ingest_file` 之前，添加：

```python
@mcp.tool(
    description="重建所有知识条目的索引（向量索引、全文索引、分块索引）。当搜索结果异常时使用。",
)
@_heartbeat
def reindex_all() -> dict:
    """重建全部知识条目的索引。包括分块、向量化和全文索引。"""
    from src.services.indexer import reindex_all as _reindex_all
    return _reindex_all()
```

- [ ] **Step 4: 移除不再需要的 import**

在 `mcp_server.py` 顶部移除 `import threading`（如果不再使用），确认无残留 ChromaDB 引用。

- [ ] **Step 5: 运行测试**

Run: `cd D:\ClaudeCodeWorkSpace\projects\knowledge-base && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 6: 提交**

```bash
git add src/mcp_server.py
git commit -m "feat: MCP search 适配 sqlite-vec、新增 reindex_all 工具"
```

---

### Task 7: RAG 适配 + 全链路集成测试

**Files:**
- Modify: `src/services/rag.py` (微调)
- Create: `tests/test_search.py`

- [ ] **Step 1: 适配 rag.py**

`rag.py` 调用 `HybridSearcher.search(queries, top_k)` 的接口未变，不需要改动。但确认 `_get_wiki_context` 中 `Database.search_wiki_fts` 已在 Task 3 中修复了查询清洗。

无需改动 `rag.py`，跳过此步。

- [ ] **Step 2: 创建搜索集成测试**

Create `tests/test_search.py`:

```python
"""搜索链路集成测试 — 验证 FTS/向量/混合搜索"""
from src.services.db import Database
from src.services.indexer import index_knowledge_item
from src.models.knowledge import KnowledgeItem


class TestFTS5ChineseSearch:
    def test_search_knowledge_chinese(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        results = Database.search_knowledge("测试")
        assert len(results) >= 1
        assert any(r["id"] == sample_item.id for r in results)

    def test_search_knowledge_hyphen(self, sample_item):
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
    def test_chunk_fts_chinese(self, sample_item, monkeypatch):
        """chunk FTS 能搜索到中文内容"""
        monkeypatch.setattr(
            "src.services.indexer.EmbeddingService",
            type("M", (), {"embed_batch": lambda s, t: [[0.1] * 1024] * len(t)}),
        )
        Database.insert_knowledge(sample_item.to_row())
        index_knowledge_item(sample_item)
        results = Database.search_chunks_fts("测试内容")
        assert len(results) >= 1
```

- [ ] **Step 3: 运行全部测试**

Run: `cd D:\ClaudeCodeWorkSpace\projects\knowledge-base && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 4: 提交**

```bash
git add tests/test_search.py
git commit -m "test: 新增搜索链路集成测试"
```

---

### Task 8: 生产数据迁移 — reindex + 验证

**Files:**
- 无文件修改，运行时操作

- [ ] **Step 1: 备份现有数据库**

Run: `cp D:\ClaudeCodeWorkSpace\projects\knowledge-base\data\kb.db D:\ClaudeCodeWorkSpace\projects\knowledge-base\data\kb.db.bak`

- [ ] **Step 2: 启动 MCP 服务并验证 schema 迁移**

Run: `cd D:\ClaudeCodeWorkSpace\projects\knowledge-base && python -c "from src.utils.config import Config; Config.load(); from src.services.db import Database; Database.connect(); print('Schema migration OK')"`

- [ ] **Step 3: 执行 reindex_all 重建全部索引**

通过 MCP 工具或直接 Python 调用 `reindex_all()`：
```python
cd D:\ClaudeCodeWorkSpace\projects\knowledge-base && python -c "
from src.utils.config import Config; Config.load()
from src.services.db import Database; Database.connect()
from src.services.indexer import reindex_all
result = reindex_all()
print(result)
"
```

- [ ] **Step 4: 验证目标文档搜索**

Run:
```bash
cd D:\ClaudeCodeWorkSpace\projects\knowledge-base && python -c "
from src.utils.config import Config; Config.load()
from src.services.db import Database; Database.connect()
kid = '4bfe2ae8-41c3-41c9-830c-13909c3fa7e5'

# FTS 搜索
r1 = Database.search_knowledge('企微集约运营情况汇总表')
print(f'FTS: {len(r1)} results, target={any(r[\"id\"]==kid for r in r1)}')

# FTS 含连字符
r2 = Database.search_knowledge('2025-2026企微集约运营')
print(f'FTS hyphen: {len(r2)} results, target={any(r[\"id\"]==kid for r in r2)}')

# Chunk FTS
r3 = Database.search_chunks_fts('企微集约运营情况汇总')
print(f'ChunkFTS: {len(r3)} results, target={any(r[\"knowledge_id\"]==kid for r in r3)}')

# 向量搜索
from src.services.vectorstore import VectorStore
r4 = VectorStore().search('企微集约运营情况汇总表', top_k=10)
print(f'Vector: {len(r4)} results, target={any(r.get(\"metadata\",{}).get(\"knowledge_id\")==kid for r in r4)}')
"
```

Expected: 四种搜索路径中至少 3 种能找到目标文档。

- [ ] **Step 5: 提交最终状态（如有残留改动）**

```bash
git add -A
git commit -m "chore: 生产数据迁移验证完成"
```

---

## 自审清单

**1. Spec 覆盖度：**
- sqlite-vec 替代 ChromaDB → Task 2
- jieba 全模式分词 → Task 1, Task 3
- chunk_fts 重建 → Task 3
- FTS 查询清洗 → Task 1, Task 3
- 原子同步写入 → Task 4
- 加分融合混合搜索 → Task 5
- 搜索模式配置 → Task 5
- reindex_all → Task 4, Task 6
- MCP search 回退逻辑 → Task 6
- 生产数据迁移 → Task 8

**2. Placeholder 扫描：** 无 TBD/TODO/待定。

**3. 类型一致性：**
- `VectorStore.add_chunk_embedding(chunk_id, knowledge_id, embedding)` 在 Task 2 定义，Task 4 调用 — 一致
- `Database.insert_chunks_fts(chunks)` 参数格式在 Task 3 定义，Task 4 调用 — 一致
- `sanitize_fts_query` 在 Task 1 定义，Task 3 调用 — 一致
- `reindex_all` 在 Task 4 定义，Task 6 暴露 — 一致
