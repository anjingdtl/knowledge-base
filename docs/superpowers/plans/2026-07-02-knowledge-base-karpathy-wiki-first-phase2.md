# Knowledge-Base Karpathy Wiki-First 第二阶段实施计划（检索执行层）

> **For agentic workers:** 本 plan 为第二阶段**规划层**文档，按 W1-W4 给出文件、接口、验收与实现要点。进入任一 W 的实现时，REQUIRED SUB-SKILL：先用 `superpowers:writing-plans` 把对应 W 展开为 bite-sized TDD 步骤（参照第一阶段 W1 plan 的 Step 粒度），再用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 执行。步骤用 checkbox（`- [ ]`）跟踪。

**Goal:** 补齐第一阶段 §263 预告的 Karpathy 检索原则——规模自适应路由 + parent-child for wiki + 中文 lexical 强化，使检索执行层与已建成的 wiki 累积层匹配。

**Architecture:** 在现有 `route_engine`（5 路由）/ `hybrid_search`（向量+lexical RRF）/ `parent_child_retrieval`（block parent）之上，前置 `SizeAwareRouter`、新增 `wiki_parent_retrieval`、强化 `lexical_zh`；仅 `mode=wiki_first` 启用，`legacy` 零影响。

**Tech Stack:** Python 3.12 / SQLite-vec + FTS5 / jieba / FastMCP / 现有 RAG 管线

## Global Constraints

- **不破坏现有测试**：基线约 950+ passed，每周末全量 pytest 不回归
- **mode=legacy 零影响**：三段新配置缺省 `enabled=false`，SizeAwareRouter 仅 `wiki_first` 介入（S6）
- **零运行时 LLM**：SizeAwareRouter 规则层先行，`llm_fallback` 默认 false；lexical 强纯文本字典
- **BGE-M3 / 1024 维不变**：不做向量维度/模型/向量库迁移（§2.2）
- **每个涉及现有符号的改动，实现前先 `gitnexus impact`**（spec §11）
- **可复现性**：阈值/计数可配置，不在路由器内取系统时间

## File Structure

| 文件 | 职责 | 本阶段动作 |
|---|---|---|
| `src/services/size_aware_router.py` | 规模判定 + 三档分流 | **新增** |
| `src/services/wiki_parent_retrieval.py` | wiki 候选 → source 页 parent 上下文 | **新增** |
| `src/services/lexical_zh.py` | 词典/同义词加载 + query 扩展 | **新增** |
| `src/services/hybrid_search.py` | keyword 分词/同义词 + RRF 语种权重 | **改**：`_keyword_search` / RRF 段 |
| `src/services/rag_pipeline.py` | `WikiReadStage` + wiki parent 挂载 + size_aware 分流 | **改** |
| `src/services/route_engine.py` | 前置 SizeAwareRouter 调用点 | **改**（最小） |
| `src/utils/config.py` / `core/container.py` | 三段配置 + 注入 | **改** |
| `src/services/project_setup.py` | init 生成字典/同义词空模板 | **改** |
| `data/lexical_zh_dict.txt` / `lexical_zh_synonyms.txt` | 词典/同义词 | **新增**（init 生成空模板） |
| `evals/run_retrieval_eval.py` | size_aware 路由准确率 + retrieval_zh | **扩展** |

---

## W1 — 规模自适应路由

### Task 1.1: SizeAwareRouter 规则层

**Files:**
- Create: `src/services/size_aware_router.py`
- Test: `tests/test_size_aware_router.py`

**Interfaces:**
- Consumes: config `rag.size_aware.small_query_max_tokens` / `small_wiki_page_threshold` / `intent_words_large`
- Produces: `class SizeAwareRouter` with `def route(self, question: str, wiki_index_hits: int) -> dict` → `{"scale": "wiki_read"|"full_search"|"blend", "reason": str}`

- [ ] 写失败测试：小查询（token≤12 且 wiki_hits≤3）→ `wiki_read`；含意图词"哪些"→ `full_search`；中间（token>12 但 hits≤3）→ `blend`
- [ ] 实现 `SizeAwareRouter`：token 计数（jieba 分词）、意图词匹配、三档分类；阈值从 config 读
- [ ] 测试通过 + commit `feat(knowledge-base): add SizeAwareRouter rule layer`

**验收:** 三类 fixture 断言档位正确（S1）

### Task 1.2: wiki_read 档执行器（WikiReadStage）

**Files:**
- Modify: `src/services/rag_pipeline.py`
- Test: `tests/test_wiki_read_stage.py`

**Interfaces:**
- Consumes: `SizeAwareRouter.route`（1.1）的 `scale=wiki_read`
- Produces: `WikiReadStage` 输出候选，标记 `match_channels=["wiki_read"]`

- [ ] 写失败测试：小查询经 `WikiReadStage` 后 `vector_search` 调用计数=0，wiki 页读取≥1
- [ ] 实现 `WikiReadStage`：读 `index.md` → FTS5 定位 wiki 页 → 返回候选，跳过 vector_search 阶段
- [ ] 在 `rag_pipeline` 按 `scale` 分流：`wiki_read`→仅 `WikiReadStage`；`full_search`→现有阶段；`blend`→两路
- [ ] 测试通过 + commit

**验收:** 小查询零向量调用（S2）

### Task 1.3: blend 档 RRF 融合

**Files:**
- Modify: `src/services/rag_pipeline.py`, `src/services/hybrid_search.py`
- Test: `tests/test_blend_fusion.py`

- [ ] 写失败测试：blend 档候选含 `wiki_read` + `full_search` 两路来源，RRF 融合分数正确
- [ ] 实现两路候选合并（复用 RRF 融合函数；`wiki_read` 候选给 channel 权重）
- [ ] 测试通过 + commit

**验收:** blend 候选含两路，分数正确

### Task 1.4: 配置 + container + legacy 兜底

**Files:**
- Modify: `src/utils/config.py`, `src/core/container.py`, `src/services/route_engine.py`

- [ ] 加载 `rag.size_aware` 段；container 注入 `SizeAwareRouter`
- [ ] `route_engine` 前置调用 `SizeAwareRouter`（仅 `mode=wiki_first` 且 `enabled`）
- [ ] `legacy` / `enabled=false` 时 SizeAwareRouter 不介入（直接 `full_search`）
- [ ] 回归测试：legacy 配置检索行为不变 + commit

**验收:** S6（legacy 零变化）

---

## W2 — parent-child for wiki

### Task 2.1: WikiParentRetriever

**Files:**
- Create: `src/services/wiki_parent_retrieval.py`
- Test: `tests/test_wiki_parent_retrieval.py`

**Interfaces:**
- Consumes: wiki 候选 frontmatter `source_ids` / `key_entities`（第一阶段 `wiki_source_compiler`/`wiki_entity_updater` 写入）
- Produces: `def enrich_with_wiki_parent(candidates: list, db) -> list`，每候选附加 `parent_context`（source 页摘要，≤`wiki_parent_context_max_length`）

- [ ] 写失败测试：wiki 候选（entities/concepts 页，frontmatter 含 `source_ids`）→ `parent_context` 非空且指向 source 页
- [ ] 实现：解析 frontmatter `source_ids` → 拉对应 source 页摘要（复用 `wiki_repo`）→ 截断
- [ ] `parent_context` 为空时（wiki 页无 source 引用）静默跳过
- [ ] 测试通过 + commit `feat(knowledge-base): add wiki parent-child retrieval`

**验收:** S3

### Task 2.2: 挂载到 wiki 检索 post-rerank

**Files:**
- Modify: `src/services/rag_pipeline.py`

- [ ] 写失败测试：wiki_retrieval 阶段后候选含 `parent_context` 字段
- [ ] 在 wiki_retrieval 阶段后挂 `enrich_with_wiki_parent`（与 block parent-child 对称位置）
- [ ] 复用 `CitationBuilder` 渲染 `parent_context`（不新增字段语义）
- [ ] 测试通过 + commit

### Task 2.3: blend 档 block + wiki parent 共存

**Files:**
- Modify: `src/services/hybrid_search.py`（验证，可能不改）

- [ ] 写测试：blend 档 block 候选与 wiki 候选各自 `parent_context` 不互相覆盖
- [ ] 确认 `setdefault+extend` 写 warnings（已有 `hybrid_search.py:261` 模式）
- [ ] 测试通过 + commit

**验收:** 两路 parent 共存

---

## W3 — 中文 lexical 强化

### Task 3.1: 自定义词典加载

**Files:**
- Create: `src/services/lexical_zh.py`（加载器）
- Modify: `src/services/hybrid_search.py:_keyword_search`
- Test: `tests/test_lexical_zh_dict.py`

- [ ] 写失败测试：加载 `lexical_zh_dict.txt` → jieba 注入 → 专名查询分词命中
- [ ] 实现 `LexicalZh` 加载器（文件不存在/空 → 空字典，warning 不阻塞）
- [ ] `_keyword_search` 分词前注入 jieba 词典
- [ ] 测试通过 + commit

### Task 3.2: 同义词扩展

**Files:**
- Modify: `src/services/lexical_zh.py`, `src/services/hybrid_search.py`
- Test: `tests/test_lexical_zh_synonym.py`

- [ ] 写失败测试：query 含"KB" → 同义词"知识库"命中 FTS5
- [ ] 实现：加载同义词表 → query 扩展同义查询集 → 并集进 FTS5（`_keyword_search` 接收 queries list 已支持）
- [ ] 测试通过 + commit

### Task 3.3: RRF 权重按语种

**Files:**
- Modify: `src/services/hybrid_search.py` RRF 段（`hybrid_search.py:167` 附近）
- Test: `tests/test_rrf_lang_weight.py`

- [ ] 写失败测试：中文查询用 `weight_zh`（0.7），英文用 `weight_en`（0.5）
- [ ] 实现语种判定（中文字符占比）→ 选权重 → RRF 融合
- [ ] 英文数据集回归（确保不降）
- [ ] 测试通过 + commit

### Task 3.4: init 生成字典/同义词模板 + 配置注入

**Files:**
- Modify: `src/services/project_setup.py`
- Test: `tests/test_project_setup.py`（扩展）

- [ ] 写失败测试：init 后 `data/lexical_zh_dict.txt` / `synonyms.txt` 存在（空模板带注释）
- [ ] 实现 `write_lexical_zh_templates` + 配置段注入
- [ ] 测试通过 + commit

**验收:** `retrieval_zh` Recall@5 ≥ 0.7（S4）

---

## W4 — 收口与 eval 扩展

### Task 4.1: size_aware 路由准确率指标

**Files:**
- Modify: `evals/run_retrieval_eval.py`

- [ ] 新增小/大查询路由 fixture + 准确率统计
- [ ] `run_retrieval_eval.py --all` 输出 size_aware accuracy
- [ ] commit

### Task 4.2: retrieval_zh 达标验证

**Files:**
- `evals/` 中文数据集

- [ ] 跑 `retrieval_zh` → Recall@5 ≥ 0.7
- [ ] 未达标则回 W3 调词典/同义词/权重
- [ ] commit 达标基线

**验收:** S4

### Task 4.3: 文档

**Files:**
- Modify: `docs/advanced-features.md`, `tests/test_docs_consistency.py`

- [ ] 新增「规模自适应路由 / wiki parent-child / 中文 lexical」三章
- [ ] 文档-配置一致性测试通过
- [ ] commit

### Task 4.4: 版本 → v1.5.0 + 全量回归

**Files:**
- Modify: `src/version.py`

- [ ] `VERSION` → `1.5.0`
- [ ] 全量 pytest + CI 五项（backend test/lint/frontend/docker/eval）绿
- [ ] 更新 `PROGRESS.md` 第二阶段状态
- [ ] commit

**验收:** S5 + 全量绿

---

## Self-Review

**1. Spec coverage:**
- S1 SizeAwareRouter 三档 → Task 1.1 ✓
- S2 小查询零向量 → Task 1.2 ✓
- S3 wiki parent_context → Task 2.1/2.2 ✓
- S4 Recall@5 ≥ 0.7 → Task 3.1-3.4 + 4.2 ✓
- S5 全量不回归 → 每 W 末 + 4.4 ✓
- S6 legacy 零变化 → Task 1.4 ✓
- §4.1/4.2/4.3 架构 → W1/W2/W3 ✓
- §5 配置 → Task 1.4/3.4 ✓

**2. Placeholder scan:** 无 TBD/TODO；每 Task 给出文件、接口签名、步骤、验收。

**3. Type consistency:** `SizeAwareRouter.route` → `WikiReadStage` 消费 `scale`；`enrich_with_wiki_parent` 与现有 `enrich_with_parent_context` 对称；`parent_context` 字段 block/wiki 一致。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-02-knowledge-base-karpathy-wiki-first-phase2.md`。进入实现时：

1. 选定起始 W（建议 W1）
2. 用 `superpowers:writing-plans` 把该 W 展开为 bite-sized TDD 步骤
3. 用 `subagent-driven-development`（推荐）或 `executing-plans` 执行
