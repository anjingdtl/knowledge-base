# Knowledge-Base Karpathy Wiki-First 对齐设计（第二阶段：检索执行层）

- **状态**：Draft（待用户复核 → 进入 writing-plans）
- **日期**：2026-07-02
- **范围**：第二阶段 4 周——补齐 Karpathy 方法论的「检索执行层」（规模自适应路由 + parent-child for wiki + 中文 lexical 强化）
- **上游依据**：`docs/knowledge-base 仓库对照 Karpathy LLM 知识库方法论的深度评估报告.md`
- **承接**：`docs/superpowers/specs/2026-07-02-knowledge-base-karpathy-wiki-first-design.md`（第一阶段 wiki 编译闭环，已完成 W1-W4 核心实现）
- **适用版本**：ShineHeKnowledge v1.4.0+（本阶段完成后建议升 v1.5.0）

## 1. 背景与动机

第一阶段已落地 Karpathy「知识累积层」：raw → wiki 编译闭环、`index.md`/`log.md`、query 回写、lint 4 类、`migrate`、wiki-compilation eval。评估报告中方法论落地分提升，但报告点名的三项 Karpathy 检索原则仍属「未落地」：

| Karpathy 原则 | 当前状态（已核实） | 缺口 |
|---|---|---|
| 小规模用 index / 大规模用搜索 | `route_engine.py` 有 5 路由（`RuleRouter:85` / `EmbeddingRouter:201` / `LLMRouter:402` / `PlanetaryRouter:463` / `AgenticRouter:113`）按语义/结构路由，**无按"规模"路由**：无论查询大小都走向量搜索 | 缺 size-aware 路由层：小查询先读 `index.md`/wiki 页，大查询才走向量+lexical |
| parent-child 上下文 | `parent_child_retrieval.enrich_with_parent_context` 已在 **block 检索**生效（`hybrid_search.py:40`，`rag.parent_child.enabled`） | 未扩展到 **wiki 检索**：wiki 页（entities/concepts）命中时不带回其引用的 source 页上下文 |
| 中文 lexical 通道 | keyword 通道走 FTS5 + jieba，RRF 权重 `w_keyword=0.6` 固定（`hybrid_search.py:167`） | 无专名/同义词扩展、权重不可按语种调优，中文召回弱（`retrieval_zh` Recall@5 长期 0.6） |

第一阶段 spec §263 已明确将这三项预告给第二阶段。本 spec 落地它们。

**最优路径**（延续第一阶段）：不换库换模，在现有 `route_engine` / `hybrid_search` / `parent_child_retrieval` 之上**新增规模自适应层 + wiki parent-child + 中文 lexical 强化**，使检索执行层与已建成的 wiki 累积层匹配。

## 2. 目标与非目标

### 2.1 目标

把检索执行层升级为「规模自适应、wiki 上下文增强、中文友好」的三层管线，补齐第一阶段 §263 预告的 Karpathy 检索原则：

```
query → SizeAwareRouter（规模判定）
        ├─ 小规模 wiki_read  → 读 index.md + 定位 wiki 页（零向量）
        ├─ 大规模 full_search → 向量 + lexical + parent-child
        └─ 中间   blend      → wiki 先行 + 搜索补充（RRF 融合两路）
```

### 2.2 非目标（明确排除，留待第三阶段 spec）

下列属第一阶段 §2.2 中未做的「扩展基础设施」，本阶段仍不做：

- 向量维度配置化迁移、embedding 模型替换（BGE-M3 保持 1024 维）
- 向量库迁移（Qdrant/pgvector，继续 SQLite-vec 路线）
- 缓存治理（query rewrite / result / answer cache）
- 成本面板、Prometheus/OpenTelemetry、SLO
- 真实模型夜测 CI（保留现有 fake-embedding PR 门禁）
- 安全策略细化（脱敏管线 / 源目录 allowlist）

> 这六项是工程基础设施扩展，与本阶段「方法论检索闭环」正交，留待第三阶段独立 spec。

## 3. 成功标准（可验证）

| # | 标准 | 验证方式 |
|---|---|---|
| S1 | 新增 `SizeAwareRouter`：小/大/混合三档路由，阈值可配置 | 单元测试：构造小查询（1 wiki 页可答）→ `scale=wiki_read`；大查询（跨源/列举意图）→ `scale=full_search`；中间 → `blend` |
| S2 | 小规模查询不触发向量搜索，仅读 `index.md` + ≤N 个 wiki 页 | 集成测试：小查询执行期间 `vector_search` 调用计数=0，wiki 页读取≥1 |
| S3 | wiki 检索命中 entities/concepts 页时，带回其引用的 source 页作为 parent 上下文 | 契约测试：wiki 命中 → 候选 `parent_context` 字段非空且指向 source 页 |
| S4 | 中文 lexical 通道支持自定义词典 + 同义词扩展；`retrieval_zh` Recall@5 ≥ 0.7（基线 0.6，+10%） | eval：`retrieval_zh` 数据集 Recall@5 不低于 0.7 |
| S5 | 全量 pytest 无回归（基线约 950+ passed） | CI Test job 绿 |
| S6 | `mode=legacy` 项目检索行为零变化（本阶段能力仅 `wiki_first` 启用） | 回归测试：legacy 配置下 SizeAwareRouter 不介入 |

## 4. 架构设计

### 4.1 规模自适应检索路由（SizeAwareRouter）

新增 `src/services/size_aware_router.py`，挂在现有 `route_engine` 路由链**最前**：

| 规模档 | 判定信号（任一命中即升级） | 路由目标 |
|---|---|---|
| `wiki_read`（小） | 查询 token ≤ `small_query_max_tokens`（默认 12） **且** `index.md` 命中候选 wiki 页 ≤ `small_wiki_page_threshold`（默认 3） | 仅读 `index.md` + 命中 wiki 页（走 `wiki_retrieval` FTS5），**不触发向量搜索** |
| `full_search`（大） | 查询含列举/比较意图词（"哪些/所有/对比/全部/列举"） **或** `index.md` 无命中 **或** 跨源信号 | 现有 hybrid search（向量 + lexical + parent-child） |
| `blend`（中间） | 介于两者之间 | `wiki_read` 结果 + `full_search` 结果 RRF 融合 |

**判定实现**：规则层先行（token / 命中数 / 意图词），可选 LLM 兜底（复用 `LLMRouter` 的 chat，受 `max_llm_calls` 上限，默认关闭）。规则层零 LLM 成本。

**与现有 Router 关系**：SizeAwareRouter 是「规模维度的前置过滤器」，不替代语义路由（Embedding/Agentic）；后者在 `full_search` / `blend` 档内继续决定 structured/hybrid/graph 子模式。

### 4.2 parent-child 扩展到 wiki 检索

现状 `enrich_with_parent_context` 仅作用于 block 候选。新增 `src/services/wiki_parent_retrieval.py`：

- 输入：wiki 检索候选（entities/concepts/comparisons/syntheses 页）
- 解析候选页 frontmatter 的 `source_ids` / `key_entities`（由第一阶段 `wiki_source_compiler` / `wiki_entity_updater` 写入）
- 输出：每个 wiki 候选附加 `parent_context` = 其引用的 source 页摘要（≤ `wiki_parent_context_max_length`，默认 2000）
- 挂载点：wiki 检索管线的 post-rerank 阶段（与 block parent-child 对称）

**与现有 parent-child 关系**：复用同一 `parent_context` 字段语义与 `CitationBuilder` 渲染路径；block 与 wiki 两路 parent-child 在 `blend` 档共存，RRF 不受影响。

### 4.3 中文 lexical 通道强化

扩展现有 `_keyword_search`（`hybrid_search.py:115`）与 RRF 融合层：

| 强化点 | 实现 |
|---|---|
| 专名分词 | 新增 `data/lexical_zh_dict.txt`（自定义词典，可空）；`_keyword_search` 分词前加载，提升专名召回 |
| 同义词扩展 | 新增 `data/lexical_zh_synonyms.txt`（每行 `词 同义词1 同义词2`）；query 改写时扩展同义查询，并集进 FTS5 |
| 权重按语种 | `rag.rrf_weight_keyword` 拆为 `rrf_weight_keyword_zh`（默认 0.7，中文提权）/ `rrf_weight_keyword_en`（默认 0.5）；按查询语种选择 |

**字典格式**（零运行时 LLM）：纯文本，`shinehe init` 生成空模板，用户按域填充；加载失败仅 warning 不阻塞检索（与 wiki hook 同策略）。

### 4.4 与现有架构的关系

- `route_engine.py` / `agentic_router.py`：**前置**新增 SizeAwareRouter 调用点，不改现有 Router 内部逻辑
- `hybrid_search.py`：**扩展** `_keyword_search`（分词/同义词）与 RRF 权重选择，`enrich_with_parent_context` 调用点不变
- `parent_child_retrieval.py`：**新增姊妹模块** `wiki_parent_retrieval.py`，不改动原模块
- `search_service.py` / `rag_pipeline.py`：在 vector_search 阶段前插入 SizeAwareRouter 分流；wiki_retrieval 阶段后挂 wiki parent enrich
- `AppContainer`：注入 `SizeAwareRouter` 与 `WikiParentRetriever`，依赖拓扑在其依赖的 Router/Embedding 之后

## 5. 配置变更

新增 `rag.size_aware` / `rag.wiki_parent_child` / `rag.lexical_zh` 三段：

```yaml
rag:
  size_aware:
    enabled: true                # 仅 mode=wiki_first 生效；legacy 强制 false
    small_query_max_tokens: 12
    small_wiki_page_threshold: 3
    intent_words_large: ["哪些", "所有", "对比", "全部", "列举"]
    llm_fallback: false          # 规则层足够时不开 LLM 兜底
  wiki_parent_child:
    enabled: true
    wiki_parent_context_max_length: 2000
  lexical_zh:
    dict_path: data/lexical_zh_dict.txt
    synonym_path: data/lexical_zh_synonyms.txt
    rrf_weight_keyword_zh: 0.7
    rrf_weight_keyword_en: 0.5
```

> 向后兼容：老配置无这三段时，`enabled` 缺省 `false`（legacy 项目零影响）；`mode=wiki_first` 新 init 项目由 `shinehe init` 注入 `enabled: true`。

## 6. 分阶段任务（W1–W4）

每项涉及现有符号的任务，实现前必须 `gitnexus impact` 评估 blast radius（见 §11）。

### 6.1 W1 — 规模自适应路由

| # | 任务 | 改动位置 | 验收（DoD） |
|---|---|---|---|
| 1.1 | `SizeAwareRouter` 规则层：token / wiki 命中数 / 意图词 → 三档分类 | 新增 `src/services/size_aware_router.py` | 单测：三类查询 fixture → 断言档位 |
| 1.2 | `wiki_read` 档执行器：读 `index.md` + wiki 页，零向量调用 | `rag_pipeline.py` 新增 `WikiReadStage` | 集成测试：小查询 vector_search 计数=0 |
| 1.3 | `blend` 档：`wiki_read` + `full_search` RRF 融合 | `rag_pipeline.py` / `hybrid_search.py` | 候选含两路来源，融合分数正确 |
| 1.4 | 配置加载 + `mode=legacy` 强制关闭 + container 注入 | `config.py` / `container.py` | legacy 配置下 SizeAwareRouter 不介入 |

### 6.2 W2 — parent-child for wiki

| # | 任务 | 改动位置 | 验收（DoD） |
|---|---|---|---|
| 2.1 | `WikiParentRetriever`：解析 wiki 页 frontmatter 的 `source_ids` → 拉 source 页摘要 | 新增 `src/services/wiki_parent_retrieval.py` | 单测：wiki 候选 → `parent_context` 非空 |
| 2.2 | 挂载到 wiki 检索 post-rerank；复用 `parent_context` 字段与 CitationBuilder | `rag_pipeline.py` wiki_retrieval 阶段后 | 契约测试：wiki 命中候选含 `parent_context` |
| 2.3 | 与 block parent-child 在 blend 档共存验证 | `hybrid_search.py` | 两路 parent 不互相覆盖 |

### 6.3 W3 — 中文 lexical 强化

| # | 任务 | 改动位置 | 验收（DoD） |
|---|---|---|---|
| 3.1 | 自定义词典加载 + jieba 注入 | `hybrid_search.py:_keyword_search` | 专名 fixture 召回提升 |
| 3.2 | 同义词扩展：query → 同义查询集并集进 FTS5 | `hybrid_search.py` / 新增 `lexical_zh.py` | 同义词命中计入 keyword 通道 |
| 3.3 | RRF 权重按语种拆分（zh/en） | `hybrid_search.py` RRF 融合段 | 中文查询用 weight_zh，英文用 weight_en |
| 3.4 | `shinehe init` 生成空字典/同义词模板 + 配置注入 | `project_setup.py` | init 后两文件存在，可加载 |

### 6.4 W4 — 收口与 eval 扩展

| # | 任务 | 改动位置 | 验收（DoD） |
|---|---|---|---|
| 4.1 | retrieval eval 扩展：新增 size_aware 路由准确率指标 | `evals/run_retrieval_eval.py` | 小/大查询路由准确率可量化 |
| 4.2 | `retrieval_zh` Recall@5 ≥ 0.7 达标验证 | `evals/` 中文数据集 | 不低于 0.7 |
| 4.3 | 文档：`docs/advanced-features.md` 增规模自适应/wiki parent/lexical 章节 | `docs/` | 文档-配置一致性测试通过 |
| 4.4 | 全量回归 + 版本号 → v1.5.0 | `src/version.py` | 全量 pytest 绿，CI 五项绿 |

## 7. 测试与验收策略

| 层级 | 范围 |
|---|---|
| 单元 | `size_aware_router` / `wiki_parent_retrieval` / `lexical_zh` 各自独立测试 |
| 集成 | 小/大/blend 三档 e2e；wiki parent-child 链路 |
| 回归 | 全量 pytest 不退化（基线约 950+ passed） |
| 契约 | 候选 `parent_context` 字段契约（block 与 wiki 一致） |
| eval | `retrieval_zh` Recall@5 ≥ 0.7；size_aware 路由准确率 |
| 门禁 | PR：fake-embedding 检索门禁 + 全量 pytest |

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 规模判定误路由（小查询错走向量，或大查询漏搜索） | 规则层阈值可配置 + blend 档兜底 + eval 量化准确率 |
| wiki parent 拉取 source 页放大 payload | `wiki_parent_context_max_length` 截断（复用第一阶段 PostProcess 截断策略） |
| 自定义词典/同义词加载失败 | 仅 warning 不阻塞检索（与 wiki hook 同策略） |
| 中文权重调优反而降低英文召回 | zh/en 权重独立配置 + 英文数据集回归 |
| 改动 `hybrid_search` / `rag_pipeline` blast radius | 实现前 `gitnexus impact`，参照第一阶段 §12 |
| `mode=legacy` 兼容 | 三段配置缺省 `enabled=false`；SizeAwareRouter 仅 `wiki_first` 介入 |

## 9. 兼容与迁移

- `mode=legacy` 项目：三段配置缺省关闭，检索行为与 v1.4.0 完全一致
- `mode=wiki_first` 项目：`shinehe init` 注入 `enabled: true`；已有 wiki_first 项目升级时缺省 `false`，需手动开启或重新 init
- 不涉及数据迁移（`data/` schema 不变）；字典/同义词文件 init 生成空模板

## 10. Karpathy 方法论对齐映射（本 spec 落地后）

| Karpathy 原则 | 第一阶段 | 本阶段（第二阶段）落地点 |
|---|---|---|
| 小规模用 index / 大规模用搜索 | 未覆盖 | SizeAwareRouter 三档路由（§4.1） |
| parent-child 上下文 | block 检索已有 | 扩展到 wiki 检索（§4.2） |
| 中文 lexical 友好 | keyword + jieba 基线 | 词典 + 同义词 + 语种权重（§4.3） |

本 spec + 第一阶段 spec 合计覆盖评估报告点名的全部 Karpathy 核心原则。剩余工程基础设施项（向量库迁移/缓存/可观测性/脱敏/夜测）留第三阶段。

## 11. 依赖与前置（实现期）

以下符号在本 spec 实现时会改动，实现前必须 `gitnexus impact` 评估：

- `src/services/route_engine.py` / `agentic_router.py`（W1 前置 SizeAwareRouter）
- `src/services/hybrid_search.py`（W1 blend / W3 lexical）
- `src/services/rag_pipeline.py`（W1 WikiReadStage / W2 wiki parent 挂载）
- `src/services/parent_child_retrieval.py`（W2 新增姊妹模块，不改原模块）
- `src/core/container.py`（W1/W2 注入新服务）
- `src/utils/config.py`（三段配置加载）
- `src/services/project_setup.py`（W3 字典模板生成）

## 12. 阶段交付里程碑

| 里程碑 | 交付 | 后续 |
|---|---|---|
| W1 完成 | 规模自适应路由可用，小查询不再走向量 | 检索成本下降，响应提速 |
| W2 完成 | wiki 检索带 parent 上下文 | wiki 页回答更完整 |
| W3 完成 | 中文 lexical 强化，Recall@5 ≥ 0.7 | 中文召回达标 |
| W4 完成 | eval 扩展 + 文档 + v1.5.0 | 第二阶段收口，可进第三阶段 |

每完成一里程碑跑对应回归与 eval，全量 pytest 不退化方可进入下一阶段。
