# Knowledge-Base Karpathy Wiki-First 第二阶段 W1 实现层 TDD 计划（规模自适应路由）

- **状态**：✅ **已完成**（2026-07-02，5 commit `6c80035`→`2c8b42c`）— 全部 TDD 通过，全量回归 1126 passed 零退化，真实 wiki/ 三档冒烟正确。交接见 `docs/superpowers/handoffs/2026-07-02-w2-handoff.md`。
- **日期**：2026-07-02
- **范围**：第二阶段 W1 —— `SizeAwareRouter` 规模自适应路由（小查询读 wiki 零向量 / 大查询走向量 / 中间 blend）
- **上游规划层**：`docs/superpowers/plans/2026-07-02-knowledge-base-karpathy-wiki-first-phase2.md`（W1 §Task 1.1-1.4）
- **上游 spec**：`docs/superpowers/specs/2026-07-02-knowledge-base-karpathy-wiki-first-phase2-design.md`（§4.1 / §3 S1/S2/S6）
- **前置已完成**：第一阶段迁移落地（`wiki/*.md` 真实产物已存在：11 source 页 + index.md + log.md）

> **For executors:** 本文档是 W1 的 bite-sized TDD 展开计划。每个 Step = 写失败测试 → 实现 → commit。用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 执行。

---

## Context

第一阶段已让真实数据跑通 wiki 编译（`wiki/sources/*.md` 等文件系统产物）。第二阶段 W1 要补 Karpathy「小规模用 index / 大规模用搜索」原则：新增 `SizeAwareRouter`，按查询规模三档分流——小查询只读 `index.md` + wiki 页（**零向量调用**），大查询走现有 hybrid 搜索，中间档 blend 融合两路。

本计划把 phase2 plan 的 W1 Task 1.1-1.4 展开为可直接执行的 TDD 步骤，所有挂载点已通过源码核实（见 §架构决策）。

---

## 架构决策（源码核实的挂载点）

| 决策 | 结论 | 证据 |
|---|---|---|
| SizeAwareRouter 插入点 | `AgenticRouter.route()` 顶部（`use_planetary_router` 分支前）——唯一瓶颈，MCP `route_query` 与管线 `VectorSearchStage` 全汇聚于此 | `agentic_router.py:119`（route 入口）、`:121`（use_planetary_router 分支）；调用方 `mcp_server.py:2328` / `rag_pipeline.py:276-283` |
| 规模分流位置 | `VectorSearchStage.execute` 顶部（AgenticRouter 调用前），按 `scale` 分流——保持编排器不变，与 structured/graph 提前返回一致 | `rag_pipeline.py:268-273`（分流点）、`:276`（AgenticRouter 调用）、`:296-297`（现成提前返回范式） |
| WikiReadStage 注册 | 加 `_builtin_stages` 元组 + `DEFAULT_PIPELINE_CONFIG`（vector_search 前） | `rag_pipeline.py:809-812`、`:876-885`、`:871`（init_builtins） |
| 配置加载 | `rag.size_aware.*` 用 `Config.get` 点号路径直读，**无需改 config.py**；缺段返回默认 | `config.py:261-275`（Config.get）；spec §5 |
| 容器注入 | 照抄 `knowledge_workflow` 懒加载 property 范式，与 `agentic_router` property 并列 | `container.py:344-350`（模板）、`:302-307`（agentic_router property） |
| legacy 门控（S6） | 双保险：① config 层 `mode=legacy` 时强制 `size_aware.enabled=False`；② `agentic_router:119` 兜底检查 mode | 路由/管线目前**完全和 mode 无关**（grep 零命中），mode 只门控采集侧 `knowledge_workflow.py:40,109` |
| wiki 页定位（新增） | **必须新建 `WikiPageLocator`**——`db.search_wiki_fts` 查旧 SQLite `wiki_pages` 表，对文件系统 `wiki/*.md` 无效；无任何现成"按 query 定位 wiki 页"工具 | `db.py:2031`（旧 FTS）；复用 `wiki_slug.py:45` read_frontmatter + `wiki_index_compiler.py:9` PAGE_TYPE_DIRS |
| blend RRF 融合 | **无现成函数可复用**——RRF 逻辑内联在 `_blend_search`；需新写融合 + 统一候选 schema（wiki 候选 vs 检索候选 id 体系不同） | `hybrid_search.py:147-265`（_blend_search）、`:191/207`（RRF 公式）、`:229-254`（候选 schema）、`:278-290`（_candidate_id） |

**RRF 公式（复用 `_blend_search` 既有常数）**：`score = w / (k + rank + 1)`，`k=40`（`hybrid_search.py:165`）。

**候选统一 schema**（blend 融合前置）：`{id, text, metadata, match_channels: list[str], rrf_score: float, final_score: float}`，对齐 `hybrid_search.py:229-254`。

---

## Global Constraints

- **不破坏现有测试**：基线约 950+ passed，每 Task 末跑相关回归
- **mode=legacy 零影响（S6）**：`rag.size_aware.enabled` 缺省 `False`；SizeAwareRouter 仅 `wiki_first` + `enabled=True` 介入
- **零运行时 LLM**：SizeAwareRouter 规则层先行（token/命中数/意图词），`llm_fallback` 默认 `False`
- **小查询零向量（S2）**：`wiki_read` 档不触发 `_vector_search`
- **候选 schema 统一**：wiki 候选与检索候选融合前对齐字段
- **可复现**：阈值/计数可配置，路由器内不取系统时间

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/services/wiki_page_locator.py` | 扫 `wiki/*.md` + 按 query 定位命中页 + 计数 | **新增**（Task 1.0） |
| `src/services/size_aware_router.py` | 规模判定 → 三档分流 | **新增**（Task 1.1） |
| `src/services/rag_pipeline.py` | `WikiReadStage` + `VectorSearchStage` 顶部 scale 分流 + 注册 | **改**（Task 1.2/1.4） |
| `src/services/hybrid_search.py` 或新模块 | blend 档 RRF 融合 + 候选 schema 统一 | **改/新增**（Task 1.3） |
| `src/services/agentic_router.py` | route 顶部挂 SizeAwareRouter 前缀 + legacy 兜底 | **改**（Task 1.4） |
| `src/core/container.py` | 注入 `size_aware_router` + `wiki_page_locator` | **改**（Task 1.4） |
| `config.example.yaml` / `project_setup.py` | `rag.size_aware` 段模板 + init 注入 | **改**（Task 1.4） |
| `tests/test_wiki_page_locator.py` / `test_size_aware_router.py` / `test_wiki_read_stage.py` / `test_blend_fusion.py` | TDD 测试 | **新增** |

---

## Task 1.0 — WikiPageLocator（wiki 页定位器，前置）

> SizeAwareRouter 判定需要 `wiki_index_hits`（query 命中多少 wiki 页），`wiki_read` 档也要拿命中页作候选。两者共享此定位器。

**Files:** Create `src/services/wiki_page_locator.py`, `tests/test_wiki_page_locator.py`

**Interfaces:**
- Consumes: `Config.get("knowledge_workflow.wiki_dir", "wiki")`；复用 `wiki_slug.read_frontmatter`（`wiki_slug.py:45`）、`wiki_index_compiler.PAGE_TYPE_DIRS`（`wiki_index_compiler.py:9`）
- Produces: `class WikiPageLocator` with `def locate(self, query: str, top_n: int = 10) -> tuple[list[dict], int]` → `(命中页候选列表, 命中总数)`；候选 dict 对齐统一 schema（`id/text/metadata/match_channels:["wiki_read"]`）

### Step 1.0.1 — 写失败测试
- [ ] `test_locate_returns_matching_pages`：fixture 建 3 个 `wiki/sources/*.md`（title 含"FTTR"/"营销"/"无关"），query="FTTR" → 命中 FTTR 页，命中数 ≥1
- [ ] `test_locate_no_match_returns_empty`：query 命中 0 页 → 返回 `([], 0)`
- [ ] `test_locate_searches_title_frontmatter_body`：title 不含但 body 含 query → 仍命中
- [ ] `test_locate_candidate_schema`：候选含 `id/text/metadata/match_channels` 字段，`match_channels==["wiki_read"]`
- [ ] `test_locate_missing_wiki_dir_returns_empty`：wiki 目录不存在 → `([], 0)` + warning 不抛

### Step 1.0.2 — 实现 WikiPageLocator
- [ ] `__init__`：读 `wiki_dir`（Config），缓存可选
- [ ] `locate(query, top_n)`：
  1. 遍历 `PAGE_TYPE_DIRS`，对每个 `wiki_dir/ptype/*.md` 调 `read_frontmatter` + 读 body
  2. 按 query token（jieba 分词）匹配 title / frontmatter `key_entities` / body，计命中分
  3. 排序取 top_n，构造候选 dict（对齐统一 schema）
  4. wiki_dir 不存在 → warning + `([], 0)`
- [ ] 测试通过 + commit `feat(knowledge-base): add WikiPageLocator for filesystem wiki lookup`

**验收:** SizeAwareRouter 与 WikiReadStage 的共享基础就绪

---

## Task 1.1 — SizeAwareRouter 规则层（spec S1）

**Files:** Create `src/services/size_aware_router.py`, `tests/test_size_aware_router.py`

**Interfaces:**
- Consumes: config `rag.size_aware.small_query_max_tokens`(12) / `small_wiki_page_threshold`(3) / `intent_words_large`(["哪些","所有","对比","全部","列举"])；`WikiPageLocator.locate` 的命中数
- Produces: `class SizeAwareRouter` with `def route(self, question: str) -> dict` → `{"scale": "wiki_read"|"full_search"|"blend", "reason": str, "wiki_hits": int}`

### Step 1.1.1 — 写失败测试
- [ ] `test_small_query_routes_to_wiki_read`：token≤12 且 wiki_hits≤3 → `scale=="wiki_read"`
- [ ] `test_intent_word_routes_to_full_search`：query 含"哪些" → `scale=="full_search"`（无论 token/hits）
- [ ] `test_no_wiki_hits_routes_to_full_search`：wiki_hits==0 → `scale=="full_search"`
- [ ] `test_medium_routes_to_blend`：token>12 且 wiki_hits≤3 且无意图词 → `scale=="blend"`
- [ ] `test_thresholds_from_config`：改 `small_query_max_tokens` 阈值 → 判定跟随变化
- [ ] `test_route_includes_reason_and_wiki_hits`：返回 dict 含 `reason` 字符串 + `wiki_hits` 整数

### Step 1.1.2 — 实现 SizeAwareRouter
- [ ] `__init__(locator: WikiPageLocator)`：从 Config 读阈值/意图词
- [ ] `route(question)`：
  1. 调 `locator.locate(question)` 拿 `wiki_hits`
  2. token 计数（jieba 分词 `len`）
  3. 意图词匹配（任一命中 → full_search）
  4. 规则：意图词 or wiki_hits==0 → full_search；token≤max 且 hits≤thr → wiki_read；否则 blend
  5. 返回 `{scale, reason, wiki_hits}`
- [ ] 测试通过 + commit `feat(knowledge-base): add SizeAwareRouter rule layer`

**验收:** S1（三档分类，阈值可配置）

---

## Task 1.2 — WikiReadStage（wiki_read 档执行器，spec S2 零向量）

**Files:** Modify `src/services/rag_pipeline.py`, Create `tests/test_wiki_read_stage.py`

**Interfaces:**
- Consumes: `SizeAwareRouter.route` 的 `scale=="wiki_read"` + `WikiPageLocator.locate` 候选
- Produces: `WikiReadStage`（`PipelineStage` 子类），输出候选标 `match_channels=["wiki_read"]`，**不触发 `_vector_search`**

### Step 1.2.1 — 写失败测试
- [ ] `test_wiki_read_stage_zero_vector_calls`：scale=wiki_read 的小查询经管线后，`_vector_search` 调用计数==0，wiki 页候选 ≥1
- [ ] `test_wiki_read_stage_skipped_for_full_search`：scale=full_search → WikiReadStage 不产出（走现有 vector_search）
- [ ] `test_wiki_read_stage_registered`：`StageRegistry` 含 `wiki_read`；`DEFAULT_PIPELINE_CONFIG` 在 vector_search 前有 wiki_read 条目

### Step 1.2.2 — 实现 WikiReadStage + 注册
- [ ] 定义 `WikiReadStage(PipelineStage)`：`name="wiki_read"`；`execute` 调 `locator.locate(ctx.question)` 写入 `ctx.candidates`，标 `match_channels=["wiki_read"]`
- [ ] 加到 `_builtin_stages` 元组（`rag_pipeline.py:809-812`）
- [ ] `DEFAULT_PIPELINE_CONFIG`（`:876-885`）在 `vector_search` 前加 `{"stage": "wiki_read", "enabled": True}`
- [ ] 在 `VectorSearchStage.execute` 顶部（`:268-273`）按 `ctx.metadata["scale"]` 分流：`wiki_read` → 跳过 vector_search（候选已由 WikiReadStage 填充）；`full_search` → 现有逻辑；`blend` → 标记走两路
- [ ] 测试通过 + commit `feat(knowledge-base): add WikiReadStage (zero-vector wiki_read scale)`

**验收:** S2（小查询零向量调用）

---

## Task 1.3 — blend 档 RRF 融合

**Files:** Modify `src/services/rag_pipeline.py`（或新增 `src/services/blend_fusion.py`），Create `tests/test_blend_fusion.py`

> 注意：无现成 RRF 函数（`_blend_search` 内联，且候选 id 体系不同）。本 Task 新写 wiki×检索 两路融合 + 统一候选 schema。

### Step 1.3.1 — 写失败测试
- [ ] `test_blend_fusion_merges_two_channels`：wiki_read 候选 + full_search 候选 → 融合后候选含两路来源，按 rrf_score 排序
- [ ] `test_blend_rrf_formula`：已知 rank/权重 → 断言 `rrf_score == w/(k+rank+1)`（k=40）
- [ ] `test_blend_candidate_schema_unified`：wiki 候选（id=title slug）与检索候选（id=page_id:block_id）融合后字段统一（`id/text/metadata/match_channels/rrf_score/final_score`）
- [ ] `test_blend_no_wiki_candidates_falls_back_to_full_search`：wiki 路无候选 → 融合结果 == full_search 候选

### Step 1.3.2 — 实现融合
- [ ] 定义统一候选 schema 转换：wiki 候选 id 用 `wiki:<slug>`，检索候选保持 `_candidate_id`（`hybrid_search.py:278`）
- [ ] 实现 `blend_fusion(wiki_candidates, search_candidates, w_wiki, w_search, k=40)`：两路各自按 rank 算 `w/(k+rank+1)`，同 id 累加，`match_channels` 并集
- [ ] blend 档在 `VectorSearchStage` 分流后调用融合，结果写回 `ctx.candidates`
- [ ] 测试通过 + commit `feat(knowledge-base): add blend RRF fusion for wiki×search`

**验收:** blend 候选含两路，RRF 分数正确

---

## Task 1.4 — 配置 + container 注入 + 挂载 + legacy 门控（spec S6）

**Files:** Modify `agentic_router.py` / `container.py` / `config.example.yaml` / `project_setup.py`, Create/extend `tests/test_size_aware_legacy.py`

### Step 1.4.1 — 写失败测试
- [ ] `test_legacy_mode_disables_size_aware`：`knowledge_workflow.mode=legacy` → SizeAwareRouter 不介入，`agentic_router.route()` 行为与无 SizeAwareRouter 一致
- [ ] `test_size_aware_enabled_in_wiki_first`：`mode=wiki_first` + `rag.size_aware.enabled=True` → route 先过 SizeAwareRouter，`ctx.metadata["scale"]` 被设置
- [ ] `test_container_injects_size_aware_router`：`container.size_aware_router` 非空，且为 `SizeAwareRouter` 实例
- [ ] `test_init_injects_size_aware_config`：`shinehe init` 后 config 含 `rag.size_aware` 段（enabled/small_query_max_tokens/...）

### Step 1.4.2 — 实现
- [ ] **container.py**：新增 `_size_aware_router` / `_wiki_page_locator` 私有字段（挂 `:134-135` W2 区附近）+ `@property` 懒加载（照抄 `:344-350` knowledge_workflow 范式）；locator 无参或注入 config，router 注入 locator
- [ ] **agentic_router.py:119**：`route()` 顶部（`:121` use_planetary_router 前）插入 SizeAwareRouter 前缀：
  - 读 `Config.get("knowledge_workflow.mode","legacy")` 与 `Config.get("rag.size_aware.enabled",False)`
  - 仅 `wiki_first + enabled` 时调 `container.size_aware_router.route(question)`，把 `scale` 写入返回 dict（如 `result["scale"]`）并透传到管线 `ctx.metadata["scale"]`
  - legacy / disabled → 完全跳过，走原逻辑（S6）
- [ ] **rag_pipeline.py**：`VectorSearchStage` 从 `ctx.metadata["route"]` 读 scale 分流（Task 1.2 已埋点，此处确保 scale 从 route 结果传到 ctx）
- [ ] **config.example.yaml + project_setup.py**：`_wiki_first_defaults`（`project_setup.py:81-99`）注入 `rag.size_aware` 段（enabled/small_query_max_tokens:12/small_wiki_page_threshold:3/intent_words_large/llm_fallback:False）
- [ ] 测试通过 + commit `feat(knowledge-base): wire SizeAwareRouter into router chain with legacy guard`

**验收:** S6（legacy 零变化）+ SizeAwareRouter 仅 wiki_first 介入

---

## 验收对齐（spec §3）

| spec 标准 | 本 plan 落点 |
|---|---|
| S1 SizeAwareRouter 三档 | Task 1.1 |
| S2 小查询零向量 | Task 1.0（locator）+ 1.2（WikiReadStage）+ 1.4（分流） |
| S6 legacy 零变化 | Task 1.4（config 强制 + agentic_router 兜底） |
| （S3 wiki parent / S4 Recall@5 / S5 全量回归 属 W2-W4，不在本 plan） | — |

---

## 验证（每 Task 末 + W1 收尾）

```bash
# 每 Task 末：对应单测
pytest tests/test_wiki_page_locator.py tests/test_size_aware_router.py \
       tests/test_wiki_read_stage.py tests/test_blend_fusion.py -v

# Task 1.4 后：legacy 回归 + 路由契约
pytest tests/test_size_aware_legacy.py tests/test_agentic_router.py -v

# W1 收尾：检索链路无回归
pytest tests/test_rag_sources.py tests/test_mcp_rag_full.py tests/test_mcp_contract.py -v

# 端到端（wiki_first 项目，真实 wiki/ 产物）：
#   小查询（如"FTTR 是什么"）→ 确认 _vector_search 计数=0
#   大查询（如"列出所有营销通知"）→ 走 full_search
```

---

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| WikiPageLocator 扫描全 wiki/*.md 慢 | top_n 截断 + 可选缓存；wiki 页量级小（当前 11） |
| 规模误路由（小查询错走向量） | 规则层阈值可配置 + blend 兜底 + W4 eval 量化准确率 |
| blend 候选 schema 不兼容致融合错乱 | Task 1.3 显式统一 schema（wiki `wiki:<slug>` / 检索保持 `_candidate_id`）+ 契约测试 |
| 改 `agentic_router` / `rag_pipeline` blast radius 大 | 实现前 `gitnexus impact`；legacy 门控双保险保 S6 |
| VectorSearchStage 内联 AgenticRouter 调用耦合 | 分流点设在 `:268-273`（AgenticRouter `:276` 前），不改 AgenticRouter 内部 |

---

## Self-Review

**1. Spec coverage:** S1→1.1 ✓ / S2→1.0+1.2+1.4 ✓ / S6→1.4 ✓；§4.1 SizeAwareRouter→1.1+1.4 ✓；§5 配置→1.4 ✓
**2. Placeholder scan:** 每 Step 给出测试名 + 断言 + 实现要点 + commit + file:line 挂载点，无 TBD
**3. 依赖顺序:** 1.0（locator）→ 1.1（router 用 locator）→ 1.2（stage 用 locator）→ 1.3（融合用两路候选）→ 1.4（装配 + 门控），无环
**4. 隐蔽依赖已暴露:** WikiPageLocator（agent 调研发现，phase2 plan 未显式）作为 Task 1.0 前置

---

## Execution Handoff

Plan complete。进入实现时：
1. 按顺序 Task 1.0 → 1.4，每 Step TDD（失败测试 → 实现 → commit）
2. 推荐用 `superpowers:subagent-driven-development` 逐 Step 驱动
3. 每 Task 末跑对应单测；W1 收尾跑检索回归 + 端到端验证
4. 涉及 `agentic_router` / `rag_pipeline` 改动前先 `gitnexus impact`
