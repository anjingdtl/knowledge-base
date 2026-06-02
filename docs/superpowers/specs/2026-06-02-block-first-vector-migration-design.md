# Spec: Block-First 向量存储重写（迭代 1）

**日期**: 2026-06-02  
**项目**: ShineHeKnowledge v1.1.0 → v1.2.0  
**范围**: 向量存储迁移到 Block 模型 + 管线 Bug 修复 + EmbeddingCache 接入  
**方案**: Block-First 重写（方案 C）

---

## 1. 目标

将知识库系统的向量存储从 chunk 模型（`vec_chunks` → `knowledge_chunks`）彻底重写为 Block 模型（`vec_blocks` → `blocks`），使每个 Block 成为最小检索单元，真正落地 Logseq 式"万物皆块"架构。同时修复 6 个已知管线 Bug，接入 EmbeddingCache。

## 2. 架构总览

### 改造后数据流

```
用户添加文件 → parse_file() → text_splitter → 构建 Block 行
    → Database.insert_blocks() (主表)
    → Database.insert_chunks_compat() (兼容层)
    → EmbeddingService.embed_batch_with_cache() (带缓存)
    → BlockStore.add_block_embedding() (vec_blocks)
    → Database.insert_blocks_fts() (block_fts)
    → Database.insert_chunks_fts_compat() (兼容层)
```

### 核心变化

- **向量存储**: `vec_chunks` → `vec_blocks`，rowid 关联 `blocks` 表
- **VectorStore 类**: 重写为 `BlockStore`，API 改为 block 维度
- **搜索返回**: `{id: block_id, metadata: {page_id, block_type, properties}}`
- **HybridSearcher**: JOIN `blocks` 表替代 `knowledge_chunks`
- **FTS 搜索**: `chunk_fts` → `block_fts`
- **EmbeddingCache**: 从死代码变为活跃缓存
- **维度配置**: 硬编码 1024 → 从 `config.yaml` 读取

### 兼容性策略

- `knowledge_chunks` 表保留但降级：写入时同步写，搜索/RAG 不再读取
- `chunk_fts` 表保留但降级：同上
- `vec_chunks` 表保留但降级：迁移后不再写入，仅作回退备份

## 3. 数据模型变更

### 新增表

**`vec_blocks`** — Block 级向量索引

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS vec_blocks USING vec0(
    embedding float[{dimension}] distance_metric=cosine
)
```

**`block_fts`** — Block 级全文索引

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS block_fts USING fts5(
    fts_segmented,
    page_id UNINDEXED,
    block_id UNINDEXED,
    tokenize='unicode61'
)
```

### 现有表变更

- `blocks` 表：无 schema 变更，`properties` JSON 中新增 `chunk_index`
- `knowledge_chunks` 表：降级为兼容层，schema 不变
- `vec_chunks` 表：降级为备份，schema 不变

### 配置变更

```yaml
# 新增
embedding:
  dimension: 1024

# 删除
storage:
  chroma_dir: chroma
```

删除 `config.py` 中的 `get_chroma_dir()` 方法和 `paths.py` 中的 `chroma_dir` 默认值。

## 4. BlockStore（VectorStore 重写）

### API 设计

```python
class BlockStore:
    def _ensure_table(self):
        # 从 config 读取维度
    
    def add_block_embedding(self, block_id: str, embedding: list[float]):
        # rowid 关联 blocks 表
    
    def add_blocks_batch(self, blocks: list[dict]):
        # 批量写入
    
    def search(self, query: str, top_k: int = 5,
               query_embedding: list[float] | None = None) -> list[dict]:
        # JOIN blocks 表返回 block 级结果
        # 返回: {id, text, metadata: {page_id, block_type, properties}, distance}
    
    def delete_by_page(self, page_id: str):
        # 删除指定 page 的所有 block 向量
    
    def delete_by_block(self, block_id: str):
        # 删除单个 block 向量
    
    def count(self) -> int:
    def count_by_page(self, page_id: str) -> int:
```

### 兼容性

- `VectorStore` 类保留但标记为 `@deprecated`
- 所有内部调用迁移到 `BlockStore`

## 5. Indexer 管线改造

### `index_knowledge_item()` 新流程

1. 文本分块（不变）
2. 构建 Block 行（blocks 成为主表）
3. `Database.insert_blocks()` — 原子写入 blocks + block_property_index
4. `Database.insert_chunks_compat()` — 兼容层写入
5. `EmbeddingService.embed_batch_with_cache()` — 带缓存的 embedding 生成
6. `BlockStore.add_block_embedding()` — 写入 vec_blocks
7. `Database.insert_blocks_fts()` — block_fts 全文索引
8. `Database.insert_chunks_fts_compat()` — 兼容层 FTS

### 新增方法

- `Database.insert_blocks(blocks)` — 写入 blocks 表
- `Database.insert_blocks_fts(blocks)` — 写入 block_fts
- `Database.search_blocks_fts(query, limit)` — block 级 FTS 搜索
- `Database.delete_blocks_by_page(page_id)` — 清理 block 数据
- `EmbeddingService.embed_batch_with_cache(texts)` — 带缓存的批量 embedding

### Bug 修复

| # | 位置 | 问题 | 修复 |
|---|------|------|------|
| 1 | `rag_pipeline.py:151` | `searcher.search(limit=, mode=)` 参数不匹配 | 改为 `searcher.search(queries, top_k=top_k)` |
| 2 | `rag_pipeline.py:160` | 按 `score` 排序但字段不存在 | 改为按 `rrf_score` 排序 |
| 3 | `vectorstore.py:127` | `chunk_index` 硬编码为 0 | BlockStore 从 `blocks.properties` 读取 |
| 4 | `container.py:70` | `IndexerService` 类不存在 | 创建 `IndexerService` 类 |
| 5 | `container.py:126` | `LibrarianService` 构造函数不匹配 | 修复签名 |
| 6 | `embedding.py` | `EmbeddingCache` 未接入 | 新增 `embed_batch_with_cache()` |

## 6. 搜索管线改造

### HybridSearcher 改造

- `_vector_search()`: 使用 `BlockStore().search()`
- `_keyword_search()`: 使用 `Database.search_blocks_fts()`
- `_blend_search()`: 数据源从 `knowledge_chunks` 改为 `blocks`
- `candidate_id`: 从 `knowledge_id:chunk_index` 改为 `page_id:block_id`

### RAG 管线适配

- `VectorSearchStage`: 修复参数调用（Bug #1）
- 排序字段修复（Bug #2）
- `GenerateStage._build_context_from_filtered()`: `knowledge_id` → `page_id`

### 搜索结果结构

```python
{
    "id": "block-uuid",
    "text": "block content...",
    "metadata": {
        "page_id": "ki-uuid",
        "block_type": "text",
        "properties": {"knowledge_id": "ki-uuid", "chunk_index": 3},
    },
    "distance": 0.15,
    "rrf_score": 0.85,
}
```

## 7. 数据迁移

### 迁移脚本: `scripts/migrate_to_block_store.py`

```bash
python scripts/migrate_to_block_store.py <db_path>              # dry-run
python scripts/migrate_to_block_store.py <db_path> --apply      # 执行
python scripts/migrate_to_block_store.py <db_path> --apply --backfill-vectors  # 迁移+回填
```

### 迁移步骤

1. **分析**（dry-run）: 统计各表行数，报告差异
2. **备份**: 自动备份到 `backups/`
3. **Schema**: 创建 `vec_blocks` 和 `block_fts`
4. **Block 对齐**: 补充缺失的 blocks 行
5. **向量迁移**: `vec_chunks` → `vec_blocks`（通过 chunk_id → block_id 映射）
6. **FTS 迁移**: blocks → `block_fts`（jieba 分词）
7. **验证**: 比对行数，抽样校验

### 幂等性

- `INSERT OR IGNORE` + `WHERE NOT EXISTS`
- 可重复执行

### 回滚

- 恢复备份文件
- 旧代码路径保留，回滚后立即可用

## 8. 测试策略

### 新增/改造测试文件

- `tests/test_core.py`: 新增 `TestBlockStore`
- `tests/test_search.py`: 改造 `TestHybridSearch` 使用 blocks
- `tests/test_migration.py`: 新增 block store 迁移测试
- `tests/test_indexer.py`: 新增，测试 Block-First indexer
- `tests/test_embedding_cache.py`: 新增，测试缓存接入

### 端到端验证

1. 创建测试知识库 → 验证 blocks/vec_blocks/block_fts 行数一致
2. 执行搜索（向量+关键词+混合）→ 验证返回 block 级结果
3. 执行 RAG 问答 → 验证 sources 包含 block_id
4. 重新索引 → 验证旧数据清理+新数据重建
5. 删除文档 → 验证全部关联数据清理
6. 运行迁移脚本 → 验证迁移报告正确

## 9. 不在本次范围

以下模块留到后续迭代：

- **迭代 2**: Container DI 集成、MCP/API 搜索能力对齐
- **迭代 3**: Agent 模拟调用验证、GUI Block 视图改造

## 10. 成功标准

- 所有现有测试通过
- 新增测试覆盖 BlockStore、Indexer、搜索管线、迁移脚本、EmbeddingCache
- 迁移脚本 dry-run 和 apply 均通过
- RAG 管线端到端执行无报错
- `knowledge_chunks` 兼容层仍被写入（回退安全）
