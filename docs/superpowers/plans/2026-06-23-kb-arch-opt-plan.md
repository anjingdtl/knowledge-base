---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '36d695de-bd5c-41e5-9f7b-1ca4b39ab610'
  PropagateID: '36d695de-bd5c-41e5-9f7b-1ca4b39ab610'
  ReservedCode1: '39a7923b-f561-4151-9d30-41a2c16797b5'
  ReservedCode2: '39a7923b-f561-4151-9d30-41a2c16797b5'
---

# KB-Arch-Opt 执行计划

> 基于 spec: `docs/superpowers/specs/2026-06-23-kb-arch-opt-design.md`
> 每个Phase完成后：review → fix → commit → 下一Phase

## Phase 1: data-heal（数据层止血）

### Step 1.1 全量 Reindex 增强
- 文件: `src/services/indexer.py`, `src/services/block_store.py`, `src/services/db.py`
- 内容:
  1. `reindex_all()` 增加批量处理（BATCH_SIZE=64）和断点续传（async_jobs记录游标）
  2. reindex期间切WAL，完成后切回DELETE
  3. reindex前清理vec_blocks孤儿向量
  4. 新增 `BlockStore.add_block_embeddings_batch(ids, vecs)` 批量写入
- 验证: reindex后 vec_blocks count ≈ blocks count

### Step 1.2 标签自动补全
- 文件: `src/services/tag_inference.py`（新建）, `src/services/db.py`
- 内容:
  1. `infer_tags_from_title()` — 正则提取类别词
  2. `infer_tags_from_path()` — 路径推断
  3. `infer_tags_from_tfidf()` — TF-IDF高频词
  4. `infer_tags_from_existing_vocab()` — 已有词表匹配
  5. `infer_tags_by_llm()` — LLM批量推理（异步，结果标inferred=True）
  6. `db.update_knowledge_tags()` / `db.get_tag_vocab()`
- 验证: 标签覆盖率 20% → 80%+

### Step 1.3 入库去重加固
- 文件: `src/services/indexer.py`, `scripts/dedup_cleanup.py`（新建）
- 内容:
  1. `index_knowledge_item()` 入口统一增加 `_check_content_hash()` 拦截
  2. 审计所有 insert_knowledge 调用点确认覆盖
  3. 存量去重脚本: 扫描content相同的记录，保留最早版本
- 验证: search_fulltext 同内容不再返回3+条

### Step 1.4 内容完整性校验
- 文件: `src/services/indexer.py`, `src/services/db.py`, `alembic/`
- 内容:
  1. `_validate_content_quality(blocks)` 质量评分函数
  2. knowledge_items 新增 quality_score 字段 + alembic迁移
  3. 有效block=0时标记warning，不入vec_blocks但保留FTS
  4. 低质量文档搜索降权（rerank_score × 0.5）
- 验证: 空文档 quality_score=0，搜索排序在高质量之后

### Phase 1 验收门禁
- [ ] pytest tests/ 全部通过
- [ ] 向量覆盖率 100%
- [ ] 标签覆盖率 80%+
- [ ] 重复结果 <5%
- [ ] git commit + push

---

## Phase 2: search-optimize（检索+路由优化）

### Step 2.1 存储渐进统一
- 文件: `src/services/indexer.py`, `src/services/hybrid_search.py`, `src/services/vectorstore.py`, `src/services/db.py`, `config.yaml`

### Step 2.2 RRF调优 + 专有名词加权
- 文件: `src/services/hybrid_search.py`, `src/utils/chinese_tokenizer.py`, `src/models/retrieval.py`, `config.yaml`

### Step 2.3 三级行星齿轮路由
- 文件: `src/services/route_engine.py`（新建）, `src/services/agentic_router.py`, `config.yaml`

### Step 2.4 route_query证据增强
- 文件: `src/mcp_server.py`

### Step 2.5 搜索结果多样性过滤
- 文件: `src/services/search_service.py`

### Phase 2 验收门禁
- [ ] pytest tests/ 全部通过
- [ ] 完全准确率 90%+
- [ ] 首条命中率 85%+
- [ ] 路由精准率 70%+
- [ ] 路由延迟 <500ms
- [ ] git commit + push

---

## Phase 3: pipeline-hardening（管线强化）

### Step 3.1 RAG管线并行化
- 文件: `src/services/rag_pipeline.py`, `src/services/llm.py`

### Step 3.2 三级缓存架构
- 文件: `src/services/embedding.py`, `src/services/rag_pipeline.py`, `src/services/route_engine.py`, `src/services/db.py`

### Step 3.3 可观测性建设
- 文件: `src/services/trace.py`（新建）, `src/services/health.py`（新建）, `src/services/rag_pipeline.py`, `src/services/search_service.py`, `src/mcp_server.py`

### Step 3.4 ask_with_query异步化
- 文件: `src/mcp_server.py`, `src/services/rag_pipeline.py`

### Phase 3 验收门禁
- [ ] pytest tests/ 全部通过
- [ ] ask延迟 ≤5s
- [ ] ask_with_query超时率 0%
- [ ] trace链路完整
- [ ] git commit + push