---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '42d0d9bc-60fb-416e-824f-dce6c865c052'
  PropagateID: '42d0d9bc-60fb-416e-824f-dce6c865c052'
  ReservedCode1: '59a8b911-bd6e-4087-9e88-723cad41e3e6'
  ReservedCode2: '59a8b911-bd6e-4087-9e88-723cad41e3e6'
---

# KB-Arch-Opt: ShineHe Knowledge-Base 底层架构改造设计

> 日期：2026-06-23
> 版本：v1.0
> 状态：Approved
> 策略决策：渐进式存储统一 + 三级行星齿轮路由 + 全Phase完成后上线

## 1. 背景与动机

### 1.1 当前问题

经过多轮MCP调用测试（累计1000+次），ShineHe KB v1.3.1 在 API Key 修复后的核心指标：

| 指标 | 当前值 | 目标值 |
|------|--------|--------|
| 完全准确率 | 73.3% | 90%+ |
| 首条命中率 | 70% | 85%+ |
| route_query精准路由率 | 0% | 70%+ |
| ask 平均延迟 | 8-90s | 2-5s |
| 向量覆盖率 | 0.03% | 100% |
| 重复结果率 | 30%+ | <2% |

### 1.2 根因定位

| 表层问题 | 根因 | 架构层级 |
|----------|------|----------|
| 向量索引覆盖率0.03% | indexer仅对新入库文档生成embedding，历史存量未回填 | 数据层 |
| route_query 100%退化 | tags字段缺失 + 路由强依赖LLM分类 | 路由层 |
| ask_with_query超时 | QuerySpec序列化 + LLM路由叠加延迟 | 管线层 |
| 重复知识条目 | content hash去重逻辑未覆盖所有入库路径 | 数据层 |
| 语义首条命中率70% | RRF融合权重不合理，专有名词被语义噪声淹没 | 检索层 |
| 空内容文档 | 入库无内容完整性校验 | 数据层 |

### 1.3 深层架构问题

- **三表冗余**：同一文档存于 knowledge_items / knowledge_chunks / blocks 三张表，各有独立FTS和向量索引，写入开销3倍
- **FTS分词不一致**：knowledge_fts 用 unicode61（中文差），block_fts 用 jieba 预分词（中文好）
- **嵌入缓存双写**：DB层 embedding_cache 表 + 服务层内存缓存并存，DB层未走查询路径
- **路由LLM强依赖**：AgenticRouter 对非规则匹配查询必须调LLM，认证失败直接退化
- **搜索证据不足**：route_query 的 evidence_preview 仅走 FTS/LIKE，不含向量搜索结果
- **得分归一化混乱**：vector/keyword/rrf/rerank 四种得分计算方式，优先级硬编码

## 2. 总体设计

### 2.1 三阶段架构

| Phase | 代号 | 核心目标 | 改造深度 |
|-------|------|----------|----------|
| P1 | `data-heal` | 数据层止血 | 仅数据修复+新模块，不改核心搜索路径 |
| P2 | `search-optimize` | 检索+路由优化 | 修改搜索路径+新增路由引擎，保留兼容层 |
| P3 | `pipeline-hardening` | 管线强化 | 管线内部重构，API契约不变 |

### 2.2 设计原则

- **向后兼容**：每个Phase完成后现有887个测试必须全通过，不破坏API契约
- **渐进式**：P1只加不改核心路径，P2改路径但保留fallback，P3内部重构API不变
- **可测性**：每个Phase完成后跑一轮30轮MCP召回测试，量化改进效果
- **可回退**：关键改动均有配置开关，可一键回退到旧行为

### 2.3 发布策略

全Phase完成后一次性发版（v1.4.0），发布前完整回归测试。

## 3. Phase 1: `data-heal` — 数据层止血

### 3.1 全量 Reindex

**问题**：152,394 blocks / 43 vectors（覆盖率0.03%），语义搜索不可用。

**方案**：增强 `indexer.reindex_all()`

- 批量处理：BATCH_SIZE=64，每批调用 `EmbeddingService.embed_batch_with_cache()`
- 断点续传：在 `async_jobs` 表记录已处理 block_id 游标，重启后从断点继续
- WAL模式：reindex期间自动切 `PRAGMA journal_mode=WAL`，写不阻塞读
- 孤儿清理：reindex前扫描 `vec_blocks` 中 block_id 已不存在的向量，先删后填
- 批量写入：新增 `BlockStore.add_block_embeddings_batch(ids, vecs)` 批量接口

**涉及文件**：
- `src/services/indexer.py` — reindex_all 增强
- `src/services/block_store.py` — 批量写入接口
- `src/services/db.py` — WAL模式切换 + 孤儿清理SQL

**验证标准**：
- `SELECT COUNT(*) FROM vec_blocks` ≈ `SELECT COUNT(*) FROM blocks`
- reindex期间已有搜索请求不报错（WAL不阻塞读）
- 断点续传：手动中断后重启，从断点继续而非从头开始

### 3.2 标签自动补全

**问题**：38+篇文档大部分tags为空，route_query因找不到标签100%退化。

**方案**：新建 `src/services/tag_inference.py`，两级推理

**Level 1 — 规则推理（零成本，同步）**：

- `infer_tags_from_title(title)` — 正则提取类别词
  - "企微先审后发安全要求" → ["企微", "安全"]
  - "业务外包管理办法审批流程" → ["外包管理"]
- `infer_tags_from_path(source_path)` — 路径推断
  - "渠道运营/" → ["渠道运营"]
- `infer_tags_from_tfidf(content, top_k=3)` — TF-IDF 高频词提取
- `infer_tags_from_existing_vocab(text, vocab)` — 与已有标签词表匹配

**Level 2 — LLM推理（按需，异步）**：

- 对 Level 1 未覆盖的文档（tags仍为空），批量调用 LLM 推断
- 系统提示注入现有标签词表，确保对齐
- 结果写入前标 `inferred=True`，需人工确认后才标 `confirmed=True`
- 批量处理：一次 LLM 调用处理 10 篇文档（一次性推断标签），降低 API 开销

**标签词表管理**：

- 从 `tag_relations` + 已有 tags + 高频词构建 `tag_vocab`
- 存储于 `tag_relations` 表，字段：tag_name, source(inferred/confirmed/manual), count
- 新文档入库时自动从词表匹配标签，不再依赖人工标注

**涉及文件**：
- `src/services/tag_inference.py`（新建）
- `src/services/db.py` — 新增 `update_knowledge_tags()` / `get_tag_vocab()`

**验证标准**：
- 标签覆盖率从 ~20% → 80%+
- Level 1 推理延迟 < 10ms/文档
- Level 2 推理结果含 inferred=True 标记
- 非 confirmed 标签不影响现有 route_query 行为

### 3.3 入库去重加固

**问题**：hash去重逻辑存在于 `create()` 和 `_do_ingest_file()` 中，但未覆盖所有入库路径。

**方案**：

- 审计所有 `insert_knowledge` 调用点，确保每条路径经过 hash 检查
- 在 `index_knowledge_item()` 入口统一增加 `_check_content_hash()` 拦截
- 编写一次性脚本 `scripts/dedup_cleanup.py`，扫描存量重复，保留最早版本，其余标记 `deleted_at`

**涉及文件**：
- `src/services/indexer.py` — 统一hash拦截入口
- `scripts/dedup_cleanup.py`（新建）— 存量去重

**验证标准**：
- search_fulltext 搜索同一内容不再返回3+条相同结果
- 去重脚本执行后 `SELECT COUNT(DISTINCT content) FROM knowledge_items` = `SELECT COUNT(*) FROM knowledge_items WHERE deleted_at IS NULL`
- 新入库重复文档被正确拦截（返回已有文档ID）

### 3.4 内容完整性校验

**问题**：部分文档只有标题无正文，造成无效检索。

**方案**：

- `index_knowledge_item()` 新增 `_validate_content_quality(blocks)` 检查
- 有效 block=0 时标记 warning，不入向量索引但保留 FTS 索引（标题仍可被检索到）
- `knowledge_items` 新增 `quality_score` 字段（0-100，基于 block数量/标题比例/内容密度）
- 低质量文档（score<30）在搜索结果中降权（rerank_score × 0.5）但不隐藏

**计算公式**：

```
quality_score = (
    block_count_score * 0.3      # block数量: 0→0, 1-3→40, 4-10→70, 11+→100
    + text_density_score * 0.4   # 非标题block占比: 越高越好
    + content_length_score * 0.3 # 总内容长度: 0→0, <100字→30, 100-500→60, 500+→100
)
```

**涉及文件**：
- `src/services/indexer.py` — 质量校验
- `src/services/db.py` — 新增字段 + 迁移
- `alembic/` — 数据库迁移脚本

**验证标准**：
- 空内容文档 quality_score=0，不入 vec_blocks
- 有标题无正文文档 quality_score < 30
- 搜索结果中低质量文档排在同查询高质量文档之后

### 3.5 Phase 1 预期收益

| 指标 | 当前 → Phase 1后 |
|------|------------------|
| 向量覆盖率 | 0.03% → 100% |
| 标签覆盖率 | ~20% → 80%+ |
| 重复结果率 | 30%+ → <5% |
| 完全准确率 | 73.3% → 85%+ |

## 4. Phase 2: `search-optimize` — 检索+路由优化

### 4.1 存储渐进统一（blocks为本）

**问题**：三表冗余导致写入3倍、搜索需跨表去重。

**方案**：渐进式收束，不删表、不断接口

**Step 1 — 停止向 vec_chunks 写入**：
- `indexer.index_knowledge_item()` 中通过配置开关禁用 `VectorStore().add_chunk_embedding()` 调用
- 新增配置 `rag.legacy_chunk_vector=False`（默认关闭），可随时回退

**Step 2 — 搜索路径统一走 blocks**：
- `hybrid_search._keyword_search()` 只走 `db.search_blocks_fts()`，不再查 `chunk_fts`
- `hybrid_search._vector_search()` 只走 `BlockStore.search()`，不再查 `vec_chunks`
- 保留 chunk 相关方法但标记 `@deprecated`，legacy 配置开关可回退

**Step 3 — knowledge_fts 分词升级**：
- `knowledge_fts` 从 unicode61 升级为 jieba 预分词（与 block_fts 一致）
- `db.insert_knowledge()` 对 content 做 jieba tokenize 后写入 fts_segmented 列
- 搜索 fallback 链路统一：`block_fts(jieba)` → `knowledge_fts(jieba)` → `LIKE`

**兼容性保证**：
- knowledge_chunks / chunk_fts / vec_chunks 三张表和对应方法全部保留
- `rag.legacy_chunk_vector=True` 可一键回退到旧路径
- 现有测试全部通过

**涉及文件**：
- `src/services/indexer.py` — 禁用 chunk 向量写入（配置开关）
- `src/services/hybrid_search.py` — 搜索路径统一
- `src/services/vectorstore.py` — 标记 @deprecated
- `src/services/db.py` — knowledge_fts jieba 分词
- `config.yaml` — `rag.legacy_chunk_vector` 开关

**验证标准**：
- 默认配置下搜索路径只走 blocks + block_fts + vec_blocks
- `rag.legacy_chunk_vector=True` 回退后行为与改造前一致
- 887 个测试全部通过

### 4.2 RRF 融合调优 + 专有名词加权

**问题**：RRF k=60 未针对中文调优，语义通道权重过高。

**方案**：

**可配置 RRF 参数**：

```yaml
rag:
  rrf_k: 40                    # 从60降到40，提高top结果区分度
  rrf_weight_semantic: 0.4     # 语义通道权重
  rrf_weight_keyword: 0.6     # 关键词通道权重（中文场景关键词更准）
```

**专有名词检测与加权**：

- 基于 jieba 词性标注：nr（人名）/ ns（地名）/ nt（机构名）/ nz（其他专名）
- 基于知识库实体词典：从文档标题和 tags 高频词构建专名词表
- 检测到专有名词时 `rrf_weight_keyword` 自动 ×1.5
- 全小写/全拼音/纯数字查询不触发加权（避免误判）

**得分归一化梳理**：

- 统一得分优先级：`rerank_score > rrf_score > max(vector_score, keyword_score)`
- 每个搜索结果附带 score_breakdown（各通道原始分+融合方式），debug模式可见

**涉及文件**：
- `src/services/hybrid_search.py` — RRF参数化 + 加权逻辑
- `src/utils/chinese_tokenizer.py` — `detect_proper_nouns(query)`
- `src/models/retrieval.py` — score_breakdown 字段
- `config.yaml` — RRF 配置项

**验证标准**：
- 专有名词查询（如"樊俞希工作交接报告"）首条命中率提升
- 非专有名词查询结果不回退（对比改造前后基线）
- RRF 参数可通过 config.yaml 热调整

### 4.3 三级行星齿轮路由

**问题**：AgenticRouter 强依赖 LLM，30-60s延迟，认证失败直接退化。

**方案**：三级路由逐级升级，每级有 timeout 兜底

**第一级 — 规则引擎（0ms）**：

```
输入: question
处理:
  1. 已有正则匹配（NL tag/property/title模式）→ structured
  2. 新增: 标签词表快速匹配 → structured (tag_filter=matched_tag)
  3. 新增: 意图关键词匹配:
     - "列出/所有/统计/哪些/多少" → structured
     - "关系/链接/引用/影响" → graph
     - 其他 → 交给第二级
输出: mode + query_spec 或 交给下一级
```

**第二级 — 嵌入相似度路由（<100ms）**：

```
输入: question（第一级未匹配）
前置: 启动时为每个标签预计算标签向量（标签名的embedding），缓存到内存
处理:
  1. query_embedding 与所有标签向量 cosine 相似度
  2. top-1 标签 score > 0.75 → structured (tag_filter=matched_tag)
  3. score ≤ 0.75 → hybrid
超时: 200ms 内未完成直接跳到第三级
输出: mode + tag_filter 或 hybrid
```

**第三级 — LLM路由（降级为备选，5s超时）**：

```
输入: question（前两级未能确定）
处理: 与现有 AgenticRouter._try_llm() 一致，但:
  - 超时从30s改为 config: rag.route_llm_timeout=5
  - 超时或认证失败 → 直接返回 hybrid（不再阻塞）
  - 仅当1+2级均返回hybrid且查询复杂度评估为"高"时才触发
输出: mode + query_spec 或 hybrid
```

**架构**：

```
src/services/route_engine.py（新建）
  ├── RuleRouter        — 第一级，复用+扩展现有正则
  ├── EmbeddingRouter   — 第二级，标签向量匹配
  └── LLMRouter         — 第三级，现有 _try_llm 改造

AgenticRouter.route() 调用链改为:
  RuleRouter → EmbeddingRouter → LLMRouter → hybrid兜底
```

**涉及文件**：
- `src/services/route_engine.py`（新建）— 三级路由引擎
- `src/services/agentic_router.py` — 重构调用链
- `config.yaml` — `rag.route_llm_timeout=5`

**验证标准**：
- 带标签关键词的查询通过第一级路由（0ms）
- 语义相近但无标签关键词的查询通过第二级路由（<100ms）
- 路由总延迟 < 500ms（第三级超时5s但仅少数触发）
- LLM不可用时路由仍能正常工作（降级到hybrid而非报错）

### 4.4 route_query 证据增强

**问题**：evidence_preview 仅走 FTS/LIKE，语义相似但词汇不同的文档缺失。

**方案**：

- evidence_preview 并行增加向量搜索分支（`BlockStore.search()`，top-3）
- 合并 FTS + Vector 证据，按得分排序取 top-5
- 每条证据标注来源通道（`fts` / `vector` / `like`），帮助 Agent 判断可靠性

**涉及文件**：
- `src/mcp_server.py` — route_query evidence_preview 扩展

**验证标准**：
- 语义相似但词汇不同的查询能出现向量证据
- 证据总数 ≤ 5，按得分排序
- 每条证据包含 source_channel 字段

### 4.5 搜索结果多样性过滤

**问题**：同一文档的相似block占据多个结果位。

**方案**：

- rerank后增加 `_diversity_filter(results, max_per_doc=3, sim_threshold=0.8)`
- 同一 knowledge_id 最多保留3条（已有 max_per_doc）
- 新增：内容 minhash 相似度 > 0.8 的 block 合并为一条，保留得分最高的
- 最终结果保证：同一文档 ≤ 3条 + 任意两条内容相似度 < 0.8

**涉及文件**：
- `src/services/search_service.py` — diversity_filter

**验证标准**：
- 搜索"翼支付"不再返回3条相同内容
- 同一文档最多3条结果
- 任意两条结果内容相似度 < 0.8

### 4.6 Phase 2 预期收益

| 指标 | Phase 1后 → Phase 2后 |
|------|------------------------|
| 完全准确率 | 85%+ → 90%+ |
| 首条命中率 | 75% → 85%+ |
| route_query精准路由率 | 50% → 70%+ |
| 路由平均延迟 | 8-30s → <500ms |
| 重复结果率 | <5% → <2% |

## 5. Phase 3: `pipeline-hardening` — 管线强化

### 5.1 RAG 管线并行化

**问题**：7阶段串行执行，generate 和 postprocess 可并行但当前串行等待。

**方案**：

**Stage 级并行优化**：

```
当前:  wiki ∥ vector → rerank → generate → postprocess
优化后: wiki ∥ vector → rerank → generate ∥ postprocess
```

- generate 和 postprocess 用 ThreadPoolExecutor 并行
- generate 产出部分结果时 postprocess 即开始去重截断
- 两阶段通过 `RagContext` 共享中间状态，postprocess 等待 generate 完成后做最终整合

**Generate 流式输出**：

- `LLMService.chat()` 新增 `stream=True` 模式
- generate stage 逐步产出 answer 片段，写入 `RagContext.answer_chunks`
- MCP ask() 返回时附带完整 answer（对外接口不变），但内部减少总耗时
- 流式仅作管线内部优化，不改变 MCP 协议层

**涉及文件**：
- `src/services/rag_pipeline.py` — generate ∥ postprocess 并行
- `src/services/llm.py` — stream 模式支持

**验证标准**：
- ask() 总延迟降低（对比改造前后P95）
- 返回结果格式不变（answer 字段完整）
- 887 个测试全部通过

### 5.2 三级缓存架构

**问题**：embedding 双写但 DB 层未走查询路径；RAG 结果缓存容量太小。

**方案**：

```
L1 进程内缓存（最快）
  ├── embedding: dict[hash→vector]，max=2048
  ├── rag_result: LRU[query_md5→result]，TTL=600s，max=256（从64扩大）
  └── 标签向量: dict[tag→embedding]，启动时加载永不驱逐

L2 SQLite 缓存（跨重启持久）
  └── embedding_cache 表改造：
      ├── 查询路径激活: embed_batch_with_cache() 先查DB再调API
      ├── TTL索引: created_at + ttl_hours 列，过期自动清理
      └── hash 唯一约束: 避免重复写入

L3 API 结果缓存（可选）
  └── 对同一 query+model+params 的 Embedding API 响应在本地缓存
```

**缓存穿透保护**：

- API 调用失败不写入缓存
- 缓存命中但数据损坏（反序列化失败）时静默删除并穿透
- embedding 和 rag_result 缓存各自独立的 TTL，互不影响

**涉及文件**：
- `src/services/embedding.py` — L1→L2→API 缓存链
- `src/services/rag_pipeline.py` — LRU 扩容
- `src/services/route_engine.py` — 标签向量缓存
- `src/services/db.py` — embedding_cache TTL 索引 + 查询激活

**验证标准**：
- 相同查询第二次响应时间显著降低（L1缓存命中）
- 重启后首次查询走 L2 缓存（无需重调 API）
- 缓存命中率 > 50%（典型工作负载下）

### 5.3 可观测性建设

**问题**：缺少链路追踪和健康度监控。

**方案**：

**Query Trace**：

```python
@dataclass
class QueryTrace:
    trace_id: str           # UUID
    tool: str               # search / ask / route_query
    question: str
    stages: List[StageTrace]
    total_duration_ms: float
    created_at: datetime

@dataclass
class StageTrace:
    name: str               # wiki_retrieval / vector_search / rerank / generate
    duration_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    result_count: int = 0
    warnings: List[str] = field(default_factory=list)
```

- 搜索和 RAG 管线每个阶段自动记录 StageTrace
- QueryTrace 写入 operation_logs 表（已有表结构，扩展字段）
- MCP 返回结果增加 trace_id 字段（仅 debug 模式可见）

**Score Decomposition**：

- 搜索结果增加 score_breakdown 字典：`{"vector": 0.82, "keyword": 0.91, "rrf": 0.73, "rerank": 0.88, "final": 0.88}`
- 仅在 `config.debug=True` 时附带

**健康度指标**：

- `kb_health_check()` 返回：API Key状态 / 向量覆盖率 / 标签覆盖率 / 平均延迟P95 / 缓存命中率
- 定时巡检：每日凌晨3点执行异常检测

**涉及文件**：
- `src/services/trace.py`（新建）— QueryTrace / StageTrace
- `src/services/rag_pipeline.py` — 各 stage 记录 trace
- `src/services/search_service.py` — 搜索 trace
- `src/services/health.py`（新建）— kb_health_check()
- `src/mcp_server.py` — trace_id + debug score_breakdown

**验证标准**：
- 每次搜索/ask请求生成 trace_id
- trace 中包含各阶段耗时和结果数
- debug 模式下搜索结果包含 score_breakdown
- kb_health_check() 返回完整健康指标

### 5.4 ask_with_query 异步化

**问题**：QuerySpec序列化 + LLM路由叠加延迟导致超时。

**方案**：

- 默认同步模式不变（超时提升到120s，内部路由5s超时）
- 新增 `async_mode=True` 参数，返回 `job_id`
- 用户通过 `get_job(job_id)` 轮询获取结果
- 内部超时策略：路由5s → 向量搜索30s → LLM生成60s → 总计120s
- 超时后返回部分结果（已有stages输出）+ timeout 警告

**涉及文件**：
- `src/mcp_server.py` — ask_with_query 异步模式 + 超时策略
- `src/services/rag_pipeline.py` — 部分结果返回机制

**验证标准**：
- 同步模式超时从30s提升到120s，不再有 -32001 超时错误
- 异步模式返回 job_id，可通过 get_job 获取结果
- 超时时返回部分结果 + timeout 警告（而非空错误）

### 5.5 配置汇总

Phase 3 新增/变更的 config.yaml 项：

```yaml
rag:
  legacy_chunk_vector: false        # P2: 禁用chunk向量写入
  rrf_k: 40                         # P2: RRF常数
  rrf_weight_semantic: 0.4          # P2: 语义通道权重
  rrf_weight_keyword: 0.6           # P2: 关键词通道权重
  route_llm_timeout: 5              # P2: LLM路由超时秒数
  pipeline:
    generate_parallel: true         # P3: generate ∥ postprocess
    stream_generate: false          # P3: 流式生成（默认关闭）
  cache:
    l1_embedding_max: 2048          # P3: 进程内embedding缓存
    l1_rag_max: 256                 # P3: RAG结果LRU容量
    l2_enabled: true                # P3: SQLite embedding缓存
    l2_ttl_hours: 168               # P3: DB缓存7天TTL
  observability:
    trace_enabled: true             # P3: 链路追踪
    debug_scores: false             # P3: score_breakdown
    health_check_cron: "0 3 * * *"  # P3: 每日凌晨3点巡检
  ask_with_query:
    total_timeout: 120              # P3: 总超时秒数
    route_timeout: 5                # P3: 路由超时
    async_mode: false               # P3: 默认同步
```

### 5.6 Phase 3 预期收益

| 指标 | Phase 2后 → Phase 3后 |
|------|------------------------|
| ask 平均延迟 | 5-8s → 2-5s |
| 重复查询响应 | 无缓存 → L1/L2缓存命中 |
| 问题定位效率 | 翻日志 → trace_id直达 |
| ask_with_query超时率 | 偶发 → 0% |

## 6. 跨Phase约束

### 6.1 Phase 间依赖

```
P1(data-heal) ← P2(search-optimize) ← P3(pipeline-hardening)
     │                   │                      │
     │                   │                      └─ 标签向量缓存依赖P1标签补全
     │                   └─ 行星齿轮路由依赖P1标签补全
     └─ 无前置依赖
```

- P2 的三级路由依赖 P1 的标签补全结果（标签词表 + 标签向量）
- P3 的标签向量缓存依赖 P2 的 EmbeddingRouter 标签向量
- 各 Phase 可独立测试，但必须按序执行

### 6.2 每个 Phase 完成后的验证门禁

1. `pytest tests/ -v` 全部通过（887 tests）
2. 运行一轮 30 轮 MCP 召回测试，记录核心指标
3. git commit + push 到 master 分支
4. Phase 回顾：对比预期收益与实际收益，调整后续计划

### 6.3 回退策略

| Phase | 回退方式 |
|-------|----------|
| P1 | reindex不可回退（但可重新跑）；标签补全可清空tags字段；去重可恢复deleted_at IS NOT NULL记录 |
| P2 | `rag.legacy_chunk_vector=True` 回退存储；RRF参数恢复默认；路由开关 `rag.use_planetary_router=False` 回退旧路由 |
| P3 | 配置开关可逐项关闭：`generate_parallel=False`、`cache.l2_enabled=False`、`observability.trace_enabled=False` |

## 7. 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| reindex期间API限流 | 中 | reindex耗时翻倍 | 批次间sleep + 限速控制 |
| jieba分词升级导致FTS结果变化 | 低 | 搜索回归 | 保留unicode61 fallback |
| 行星齿轮路由误路由 | 中 | 查询走错通道 | 三级均fallback到hybrid |
| 标签推理引入误标签 | 中 | 路由被误导 | inferred标签需确认、误标签可手动修正 |
| 流式generate边界情况 | 低 | 返回不完整 | 默认关闭，充分测试后启用 |
| 三表统一后旧测试依赖chunk | 中 | 测试失败 | legacy开关回退 |

## 8. 成功标准

Phase 3 完成后，30轮MCP召回测试需达到：

| 指标 | 目标值 | 当前值 |
|------|--------|--------|
| 完全准确率 | ≥ 90% | 73.3% |
| 首条命中率 | ≥ 85% | 70% |
| route_query精准路由率 | ≥ 70% | 0% |
| ask 平均延迟 | ≤ 5s | 8-90s |
| 向量覆盖率 | 100% | 0.03% |
| 重复结果率 | < 2% | 30%+ |
| pytest 全量通过 | 887+ | 887 |

任一指标未达标则针对性修复后再测，直到全部达标后发版 v1.4.0。