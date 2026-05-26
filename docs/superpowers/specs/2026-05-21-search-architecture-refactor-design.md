# 搜索架构全面重构设计 — 参考 MaxKB

## Context

知识库 MCP 搜索无法找到已知存在的文档，根因有三：
1. ChromaDB 向量库与 SQLite FTS5 是独立存储，索引不同步
2. FTS5 `unicode61` 分词器不支持中文，连续汉字被当作单个 token
3. 向量搜索返回空结果时无回退机制

参考 MaxKB（1panel-dev/MaxKB）的成熟架构，进行全面重构。

## 技术选型

| 维度 | 当前 | 重构后 | 理由 |
|------|------|--------|------|
| 向量存储 | ChromaDB | sqlite-vec | 与 SQLite 天然集成，同一行存储 |
| 全文索引 | FTS5 + unicode61 | jieba 预分词 + FTS5 + unicode61 | jieba 负责中文分词，unicode61 只按空格切分 |
| 混合搜索 | RRF 融合 | 加分融合 `(1-d)*w_v + fts*w_k` | 更简单有效，参考 MaxKB blend_search.sql |
| 搜索模式 | 隐式回退链 | 配置级 embedding/keywords/blend | 用户可控 |
| 数据同步 | 两个存储各自写入 | 同一行原子写入 | 永远同步 |

## 数据模型变更

### 1. knowledge_chunks 表扩展

新增两列，向量嵌入和分词结果与 chunk 元数据存同一行：

```sql
ALTER TABLE knowledge_chunks ADD COLUMN vec_embedding BLOB DEFAULT NULL;
ALTER TABLE knowledge_chunks ADD COLUMN fts_segmented TEXT DEFAULT '';
```

- `vec_embedding`: sqlite-vec 的 float32 向量，序列化为 bytes
- `fts_segmented`: jieba 全模式分词后的空格分隔文本

### 2. chunk_fts 表重建

废弃旧 `chunk_fts`（有 schema 损坏），重建为独立 FTS 表：

```sql
DROP TABLE IF EXISTS chunk_fts;
CREATE VIRTUAL TABLE chunk_fts USING fts5(
    fts_segmented,
    knowledge_id UNINDEXED,
    tokenize='unicode61'
);
```

- `fts_segmented` 存放 jieba 分词后的文本（空格分隔）
- `knowledge_id UNINDEXED` 不参与全文索引，仅用于 DELETE 精确匹配
- `unicode61` 只需按空格分词，中文已在 jieba 阶段处理好

### 3. knowledge_fts 保持不变

`knowledge_fts`（索引 knowledge_items 的 title + content + tags）保持现有 schema，但对查询做清洗处理。此表主要用于 `search_fulltext` 工具的标题级搜索。

### 4. 新增 search_mode 配置

```yaml
rag:
  search_mode: blend  # embedding | keywords | blend
```

## 搜索链路重写

### VectorStore 重写（ChromaDB → sqlite-vec）

```python
# src/services/vectorstore.py — 全面重写
class VectorStore:
    """基于 sqlite-vec 的向量存储，向量数据存于 knowledge_chunks.vec_embedding"""

    def _ensure_vec_table(self):
        # CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
        #   vec_embedding float[1024]
        # )

    def add_chunk_embedding(self, chunk_id: str, embedding: list[float]):
        # 单行写入 vec_embedding

    def search(self, query_embedding: list[float], top_k: int) -> list[dict]:
        # SELECT kc.*, distance FROM vec_chunks vc
        # JOIN knowledge_chunks kc ON kc.id = vc.rowid
        # WHERE vec_embedding MATCH ... ORDER BY distance

    def delete_by_knowledge(self, knowledge_id: str):
        # 通过 knowledge_chunks.knowledge_id 找到 chunk_id 列表，逐个删除
```

sqlite-vec 使用 `vec0` 虚拟表存储向量，通过 chunk_id 关联 knowledge_chunks 表。

### Indexer 重写（原子同步写入）

```python
# src/services/indexer.py — 核心改动
def index_knowledge_item(item):
    chunks = split_text(item.content, ...)
    for chunk in chunks:
        # 1. 写入 knowledge_chunks（不含向量）
        Database.insert_chunks([chunk_row])
        # 2. jieba 分词 → 更新 fts_segmented
        segmented = tokenize_chinese_full(chunk.text)
        Database.update_chunk_fts_segmented(chunk.id, segmented)
        # 3. 写入 chunk_fts
        Database.insert_chunk_fts(chunk.id, segmented, item.id)
        # 4. 计算向量 → 写入 vec_chunks + knowledge_chunks.vec_embedding
        embedding = embedding_service.embed(chunk.text)
        VectorStore().add_chunk_embedding(chunk.id, embedding)
```

四步操作在同一个调用中完成，确保同步。失败时记录错误但不静默吞掉。

### HybridSearch 重写（加分融合）

参考 MaxKB 的 blend_search.sql，实现加分融合：

```python
# src/services/hybrid_search.py
class HybridSearcher:
    def search(self, query: str, top_k: int = 5) -> list[dict]:
        mode = Config.get("rag.search_mode", "blend")
        if mode == "embedding":
            return self._vector_search(query, top_k)
        elif mode == "keywords":
            return self._keyword_search(query, top_k)
        else:  # blend
            return self._blend_search(query, top_k)

    def _blend_search(self, query, top_k):
        w_v = Config.get("rag.hybrid_search.vector_weight", 0.7)
        w_k = Config.get("rag.hybrid_search.keyword_weight", 0.3)
        vec_results = self._vector_search(query, top_k * 3)
        fts_results = self._keyword_search(query, top_k * 3)

        # 合并 + 加分融合
        merged = {}
        for rank, r in enumerate(vec_results):
            cid = r["id"]
            merged[cid] = {**r, "vec_score": w_v * (1 - r["distance"]), "fts_score": 0}
        for rank, r in enumerate(fts_results):
            cid = r["id"]
            if cid in merged:
                merged[cid]["fts_score"] = w_k * r.get("fts_rank_normalized", 0)
            else:
                merged[cid] = {**r, "vec_score": 0, "fts_score": w_k * r.get("fts_rank_normalized", 0)}

        for item in merged.values():
            item["score"] = item["vec_score"] + item["fts_score"]

        return sorted(merged.values(), key=lambda x: x["score"], reverse=True)[:top_k]
```

### MCP search 工具调整

`search` 工具使用配置的 search_mode 调用 HybridSearcher。

### FTS5 查询清洗

新增 `sanitize_fts_query()` 函数，用双引号包裹查询字符串，避免 `-`、`()` 等特殊字符导致 FTS5 语法错误。

### reindex_all 机制

新增 MCP 工具 `reindex_all`，重建所有索引：
- 删除所有 vec_chunks、chunk_fts 数据
- 遍历 knowledge_items，对每个条目调用 `reindex_knowledge_item()`
- 支持进度回调

## 修改文件清单

| 文件 | 改动级别 | 说明 |
|------|---------|------|
| `src/services/vectorstore.py` | 全面重写 | ChromaDB → sqlite-vec |
| `src/services/db.py` | 大改 | Schema 变更、迁移、chunk_fts 重建、FTS 查询清洗 |
| `src/services/indexer.py` | 大改 | 原子同步写入、错误处理 |
| `src/services/hybrid_search.py` | 重写 | 搜索模式策略 + 加分融合 |
| `src/mcp_server.py` | 中改 | search 逻辑调整、新增 reindex_all 工具 |
| `src/utils/chinese_tokenizer.py` | 小改 | 新增全模式分词函数、FTS 查询清洗 |
| `config.yaml` | 小改 | 新增 search_mode |
| `pyproject.toml` | 小改 | chromadb → sqlite-vec |
| `src/services/rag.py` | 小改 | 适配新的 HybridSearcher 接口 |

## 验证方案

1. `pytest tests/ -v` — 确保现有测试通过
2. MCP 工具验证：
   - `search("企微集约运营情况汇总表")` 能找到目标文档
   - `search("2025-2026企微集约运营")` 能找到目标文档
   - `ask("2025-2026年企微集约运营情况汇总表的内容")` 能回答
   - `search_fulltext("企微集约运营")` 能找到目标文档
3. `reindex_all` 成功重建所有索引
4. 配置 `search_mode: keywords` 时纯 FTS 搜索正常
5. 配置 `search_mode: embedding` 时纯向量搜索正常
