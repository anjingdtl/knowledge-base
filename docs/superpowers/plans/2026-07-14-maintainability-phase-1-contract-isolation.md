# Phase 1：行为契约冻结与请求状态隔离 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 冻结 Search / Ask / Wiki Serving 的用户可观察行为契约，并将一次搜索的 results/trace/claims/conflicts/fallbacks 收敛到请求级 `SearchExecution`，消除 `SearchService` 共享 `last_*` 状态导致的并发串线风险。

**Architecture:** 在不改变 MCP 公开返回结构、不改检索算法/排序/RRF/Rerank/Citation、不改 Wiki Gate/Schema 的前提下，先用契约测试钉住现状，再把 `SearchService.search()` 重构为 `execute() -> SearchExecution` + 兼容 `search() -> list[dict]`，最后迁移 `VerifiedAnswerService` 与 eval 调用方并删除共享请求状态。

**Tech Stack:** Python 3.10+、pytest、现有 `SearchService` / `VerifiedAnswerService` / `WikiServingGate`、JSON 行为快照。

**Spec:** `docs/superpowers/specs/01-maintainability-phase-1-contract-isolation.md`  
**基线版本:** `src/version.py` = `1.8.0`（本期目标候选 `v1.8.1`，仅文档/验收标注，不强制改 VERSION 除非验收完成）

---

## 0. 现状勘察（2026-07-14）

| 项 | 现状 | 影响 |
|---|---|---|
| `SearchService.last_search_trace` | 实例字段，每次 `search()` 覆盖 | 并发请求串线 |
| `SearchService.last_disclose_claims` | 实例字段 + `get_disclose_claim_rows()` | Ask 侧信道依赖 |
| `VerifiedAnswerService.ask()` | `search()` 后读 `last_*` | 生产耦合 |
| `evals/run_ask_e2e_eval.py` | 读 `last_search_trace` | eval 耦合（生产外） |
| `tests/test_verified_hybrid_search.py` | 断言 `last_search_trace` | 需改走 `execute().trace` |
| `SearchExecution` | **不存在** | 需新建 |
| 契约快照 `search_*.json` / `ask_*.json` | **不存在** | 需新建 |
| `docs/architecture/wiki-invariants.md` | **不存在** | 需新建 |
| MCP tool contract | `tests/test_mcp_contract.py` + `snapshots/mcp_tools.json` | **不得破坏** |

**生产代码读写 `last_*` 的唯二路径：**

1. `src/services/search_service.py`（写）
2. `src/services/verified_answer.py`（读）

**非生产但需同步：**

- `evals/run_ask_e2e_eval.py`
- `tests/test_verified_hybrid_search.py`

**非目标确认（禁止触碰）：**

- 不统一 Raw/Verified Retrieval、不抽 VerifiedProvider
- 不改 Wiki Serving Gate 逻辑、Claim/Evidence/Canonical
- 不改 RRF/Rerank/Citation 算法、不拆 MCP、不调 Container、不改 DB Schema

---

## 1. 文件地图

| 动作 | 路径 | 职责 |
|---|---|---|
| Create | `src/models/search_execution.py` | 请求级不可变 `SearchExecution` |
| Modify | `src/services/search_service.py` | `execute()` + 请求局部状态；`search()` 兼容壳 |
| Modify | `src/services/verified_answer.py` | 消费 `execute()`，禁止读 `last_*` |
| Modify | `evals/run_ask_e2e_eval.py` | 用 `execute().trace` 替代 `last_search_trace` |
| Modify | `tests/test_verified_hybrid_search.py` | 断言改走 `execute().trace` |
| Create | `tests/test_public_search_contract.py` | Search 行为契约 + 快照 |
| Create | `tests/test_public_ask_contract.py` | Ask 行为契约 + 快照 |
| Create | `tests/test_wiki_serving_contract.py` | WIKI-001..010 不变量 |
| Create | `tests/test_search_request_isolation.py` | 50 并发 × 多轮无串线 |
| Create | `tests/snapshots/search_raw.json` 等 | Search 快照 |
| Create | `tests/snapshots/ask_hybrid_verified.json` 等 | Ask 快照 |
| Create | `docs/architecture/wiki-invariants.md` | 不变量文档 |
| Create | `docs/superpowers/reviews/maintainability-phase1-acceptance.md` | 一期验收报告 |
| Optional | `tests/helpers/contract_normalize.py` | 快照归一化（时间/浮点/ID） |

---

## 2. 目标数据结构（冻结）

```python
# src/models/search_execution.py
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class SearchExecution:
    results: tuple[dict[str, Any], ...]
    trace: dict[str, Any] = field(default_factory=dict)
    disclose_claims: tuple[dict[str, Any], ...] = ()
    conflicts: tuple[dict[str, Any], ...] = ()
    fallbacks: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
```

**语义约定（与现有 trace 对齐，不发明新行为）：**

| 字段 | 来源（现状） |
|---|---|
| `results` | 原 `search()` 返回 list |
| `trace` | 原 `last_search_trace` 内容（含 mode/stages/route/sources/conflicts/elapsed_ms…） |
| `disclose_claims` | 原 `last_disclose_claims` 包装后的 disclose_only 行 |
| `conflicts` | `trace["conflicts"]` 的副本（tuple）；无则 `()` |
| `fallbacks` | 由 `stages.fallback` / wiki error 归一为 `{"from","to","reason"}` 元组；无则 `()` |
| `warnings` | 请求内收集（如 wiki 降级）；无则 `()` |

> 注：`assemble_answer_payload` 已会从 `search_trace.stages.fallback` 再推导 fallbacks。`SearchExecution.fallbacks` 提供显式字段，避免调用方再解析 stages；VerifiedAnswer 迁移时**优先**把 `execution.trace` 原样传入（保持行为），可选附加 `execution.fallbacks` 若 payload 已有则不重复。

---

## Task 1：`SearchExecution` + `SearchService.execute()`（请求局部状态）

**Files:**
- Create: `src/models/search_execution.py`
- Modify: `src/services/search_service.py`
- Modify: `tests/test_verified_hybrid_search.py`（适配 trace 读取）
- Test: 现有 `tests/test_search_service.py`、`tests/test_verified_hybrid_search.py` 必须继续通过

### 实现要点

1. 新增内部可变请求状态（**不**挂在 `self` 上）：

```python
@dataclass
class _SearchRequestState:
    trace: dict[str, Any]
    disclose_claims: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    fallbacks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    claim_error: str | None = None
```

2. `execute()` 入口创建 `state`，所有原 `self.last_search_trace[...]` 改为 `state.trace[...]`；`self.last_disclose_claims = ...` 改为 `state.disclose_claims = ...`；`self._last_claim_error` 改为 `state.claim_error`。

3. `_search_legacy_pipeline` / `_search_verified_hybrid` / `_safe_verified_claim_retrieve` 增加 `state: _SearchRequestState` 参数。

4. 公开 API：

```python
def execute(self, query: str, top_k: int = 5, query_spec=None) -> SearchExecution:
    ...
    return SearchExecution(
        results=tuple(output),
        trace=state.trace,
        disclose_claims=tuple(state.disclose_claims),
        conflicts=tuple(state.conflicts or state.trace.get("conflicts") or ()),
        fallbacks=tuple(state.fallbacks),
        warnings=tuple(state.warnings),
    )

def search(self, query: str, top_k: int = 5, query_spec=None) -> list[dict]:
    return list(self.execute(query=query, top_k=top_k, query_spec=query_spec).results)
```

5. **过渡期：** 可暂时保留 `last_search_trace` / `last_disclose_claims` / `get_disclose_claim_rows` 空壳或从最近一次结果回填——**但 Task 5 必须删除**。推荐本 Task 直接不写 `self.last_*`，测试立刻改用 `execute()`，减少二次迁移。

6. fallback 归一（在 verified 路径设置 `stages.fallback` 时同步 append）：

```python
if stage_fb:
    state.fallbacks.append({
        "from": "verified_wiki",
        "to": "raw_retrieval",
        "reason": str(stage_fb),
    })
```

- [x] **Step 1:** 创建 `src/models/search_execution.py`
- [x] **Step 2:** 重构 `search_service.py`：`execute` + 请求局部 `state`，`search` 变为薄包装
- [x] **Step 3:** 更新 `test_verified_hybrid_search.py` 中 `last_search_trace` 断言为 `service.execute(...).trace`
- [x] **Step 4:** 运行：

```bash
pytest tests/test_search_service.py tests/test_verified_hybrid_search.py -v --tb=short
```

Expected: PASS

---

## Task 2：迁移 `VerifiedAnswerService` 与 eval

**Files:**
- Modify: `src/services/verified_answer.py` (`VerifiedAnswerService.ask`)
- Modify: `evals/run_ask_e2e_eval.py`
- Test: `tests/test_verified_answer.py`

### 目标代码

```python
def ask(self, question: str, *, top_k: int = 5, use_llm: bool = True, llm_answer: str | None = None) -> dict[str, Any]:
    if not hasattr(self._search, "execute"):
        raise TypeError("search_service must implement execute() -> SearchExecution")
    execution = self._search.execute(question, top_k=top_k)
    results = list(execution.results)
    trace = dict(execution.trace or {})
    disclose_rows = list(execution.disclose_claims or ())
    # ... generate_fn 不变 ...
    payload = assemble_answer_payload(
        question,
        results,
        llm_answer=llm_answer,
        search_trace=trace,
        disclose_claims=disclose_rows,
        generate_fn=generate_fn,
    )
    # route / source_graph 等 setdefault 逻辑保持不变
    return payload
```

**Mock 兼容：** 若测试里有只实现 `search()` 的假对象传给 `VerifiedAnswerService`，改为实现：

```python
def execute(self, query, top_k=5, query_spec=None):
    from src.models.search_execution import SearchExecution
    return SearchExecution(results=tuple(self.search(query, top_k=top_k)))
```

或在 `ask` 内对「有 `search` 无 `execute`」做一次性兼容（**仅测试便利**）。**生产路径必须走 `execute`。** 推荐兼容：

```python
if hasattr(self._search, "execute"):
    execution = self._search.execute(question, top_k=top_k)
    results = list(execution.results)
    trace = dict(execution.trace or {})
    disclose_rows = list(execution.disclose_claims or ())
else:
    # deprecated path for legacy test doubles only
    results = list(self._search.search(question, top_k=top_k) or [])
    trace = {}
    disclose_rows = []
```

验收时生产代码路径不得出现 `last_search_trace` / `last_disclose_claims` 读取。

`evals/run_ask_e2e_eval.py` `_run_ask_search_llm`:

```python
ex = container.search_service.execute(question, top_k=5)
# 用 ex.results 构造 sources；search_trace=ex.trace
```

- [x] **Step 1:** 改 `VerifiedAnswerService.ask`
- [x] **Step 2:** 改 eval 脚本
- [x] **Step 3:** 运行：

```bash
pytest tests/test_verified_answer.py tests/test_verified_hybrid_search.py -v --tb=short
```

Expected: PASS

---

## Task 3：Search 契约冻结

**Files:**
- Create: `tests/test_public_search_contract.py`
- Create: `tests/helpers/contract_normalize.py`（可选，也可内联）
- Create snapshots under `tests/snapshots/`:
  - `search_raw.json`
  - `search_verified.json`
  - `search_raw_fallback.json`
  - `search_no_result.json`

### 归一化规则（快照忽略）

- 删除/固定：`elapsed_ms`、绝对时间戳、UUID/随机 ID（若有）
- 浮点：`score` / `rrf_score` / `rerank_score` 保留 4 位小数
- 不比较 LLM 自由文本措辞（Search 契约主要比结构与 source/claim_id/evidence 存在性）
- 环境变量 `UPDATE_CONTRACT_SNAPSHOTS=1` 时允许重写快照

### 覆盖场景（mock 驱动，零外网）

| 场景 | 断言要点 |
|---|---|
| Raw 正常 | `source=knowledge`，有 citation 或 text，`trace.mode=legacy_raw` |
| Verified Claim 增强 | 结果含 `verified_claim`，`evidence` 非空，`trace.mode=hybrid_verified` |
| Wiki 不可用 → Raw fallback | 结果仍有 knowledge；`stages.verified_wiki.error` 或 fallback 存在 |
| Vector 不可用 → 关键词降级 | hybrid 抛错后 block_store/FTS 仍返回；结构稳定 |
| Rerank 不可用 | 保留候选，不抛 |
| 无结果 | `results=[]`，trace 有 mode |
| Citation 完整性 | knowledge 项在有 db 时带 citation 字段（或显式可空） |
| Trace 基本字段 | `mode` / `query` / `stages` 存在 |

**生成首版快照步骤：** 先写测试用 `assert` 结构 + 写文件逻辑，本地跑一次生成 JSON，再提交。

- [x] **Step 1:** 写归一化 helper + 契约测试
- [x] **Step 2:** 生成并固化 4 个 search 快照
- [x] **Step 3:** 运行 `pytest tests/test_public_search_contract.py -v` → PASS

---

## Task 4：Ask 契约冻结

**Files:**
- Create: `tests/test_public_ask_contract.py`
- Create snapshots:
  - `ask_hybrid_verified.json`
  - `ask_raw_only.json`
  - `ask_conflict.json`
  - `ask_no_answer.json`
  - `ask_timeout.json`（可用 generate_fn 超时/失败模拟，或 `use_llm=False` + 空结果路径；timeout 场景断言 warnings 含 generate_failed 或 no_answer 结构稳定）

### 必须冻结的字段键

```text
answer
answer_mode
sources
claims_used
raw_evidence_used
conflicts
fallbacks
warnings
trace_id
route
```

使用 `VerifiedAnswerService.ask(..., use_llm=False)` 或注入固定 `llm_answer`，避免 LLM 非确定性。  
`answer` 对 deterministic 模板路径做全文快照；LLM 路径只比 mode/结构。

- [x] **Step 1:** 写 Ask 契约测试 + 5 快照
- [x] **Step 2:** `pytest tests/test_public_ask_contract.py -v` → PASS

---

## Task 5：Wiki Serving 不变量

**Files:**
- Create: `tests/test_wiki_serving_contract.py`
- Create: `docs/architecture/wiki-invariants.md`

### 不变量映射（实现层定位）

| ID | 不变量 | 验证方式 |
|---|---|---|
| WIKI-001 | Raw Evidence 是最终证据底座 | 无 claim 时仍可 raw_only；claim 必须能落到 evidence |
| WIKI-002 | Claim 必须具有可解析 Evidence | Gate：`MISSING_EVIDENCE` / packaging 丢弃无 evidence claim |
| WIKI-003 | stale Claim 不得进可靠主结论 | Gate REASON_CLAIM_STALE 或 freshness filter |
| WIKI-004 | unsupported 不得进可靠主结论 | Gate REASON_CLAIM_UNSUPPORTED / status |
| WIKI-005 | retracted 不得进可靠主结论 | Gate + ClaimStatus |
| WIKI-006 | conflict 必须披露 | `answer_mode=conflict_disclosure` + conflicts 非空 |
| WIKI-007 | Wiki 故障必须降级 Raw | SearchService wiki boom → knowledge 结果 |
| WIKI-008 | Projection 非 Canonical 权威 | 断言/文档：projection 不作为 serving 入口（引用现有 primary/canonical 路径） |
| WIKI-009 | Serving 与 Authoring 权限分离 | Serving 仅 `list_servable_*` / Gate；写路径不经 search serving |
| WIKI-010 | Auto Publish 默认关闭 | `project_setup` / migrator 默认 `auto_publish=false`；配置缺省策略 |

可复用 `tests/test_wiki_serving_gate.py` 工厂，但本文件用「编号契约」形式固定，防止后续重构漏测。

- [x] **Step 1:** 写 `wiki-invariants.md`
- [x] **Step 2:** 写 `test_wiki_serving_contract.py` 覆盖 001–010
- [x] **Step 3:** `pytest tests/test_wiki_serving_contract.py -v` → PASS

---

## Task 6：并发隔离测试 + 删除 `last_*`

**Files:**
- Create: `tests/test_search_request_isolation.py`
- Modify: `src/services/search_service.py`（删除 last_* 与 get_disclose_claim_rows）
- Grep 全仓库确认无生产读取

### 并发测试设计

```python
# 伪代码
def test_no_cross_request_contamination():
    service = build_service_with_per_query_injection()
    # 对每个 query_i 注入唯一 trace 标记、claim_id、source id
    with ThreadPoolExecutor(max_workers=50) as pool:
        futs = [pool.submit(service.execute, f"q-{i}-{token}", top_k=3) for i in range(50)]
        executions = [f.result() for f in futs]
    # 每轮：execution.trace["query"] 前缀与 results 内 id 必须匹配本请求 token
    # 重复 20–100 轮（CI 可用 20，本地/strict 100）
```

断言：

```text
Trace 串线数 = 0
Claim 串线数 = 0
Conflict 串线数 = 0
Fallback 串线数 = 0
Citation 串线数 = 0
```

实现注入：patch `_timed_hybrid_search` / `_safe_verified_claim_retrieve` 按 query 返回带唯一 ID 的数据，使 results 与 query 可关联。

### 删除前检查清单

```bash
rg "last_search_trace|last_disclose_claims|get_disclose_claim_rows" --glob "*.py"
```

允许残留：`docs/**`、本 plan/spec、验收报告。  
禁止残留：`src/**`、`evals/**`（生产/评测路径）。

- [x] **Step 1:** 写并发隔离测试并通过
- [x] **Step 2:** 删除 `last_*` / `get_disclose_claim_rows`
- [x] **Step 3:** 全仓库 rg 确认 `src/` 与 `evals/` 无读取
- [x] **Step 4:** 相关单测全绿

---

## Task 7：全量验证 + 验收报告

**Commands:**

```bash
# 契约与隔离
pytest tests/test_public_search_contract.py tests/test_public_ask_contract.py \
  tests/test_wiki_serving_contract.py tests/test_search_request_isolation.py \
  tests/test_search_service.py tests/test_verified_hybrid_search.py \
  tests/test_verified_answer.py tests/test_mcp_contract.py -v --tb=short

# 更广回归（时间允许）
pytest tests/ -q --tb=line
# 若过慢：至少 core 子集 + hybrid/search/ask/wiki serving

# 质量评测（有 fixture 时）
python evals/run_retrieval_eval.py   # 或项目惯用入口
python evals/run_hybrid_eval.py --strict
```

**验收报告模板：** `docs/superpowers/reviews/maintainability-phase1-acceptance.md`

必须勾选 Spec §7：

- [ ] Search 契约快照通过
- [ ] Ask 契约快照通过
- [ ] Wiki Serving 契约通过
- [ ] 50 并发无串线
- [ ] 用户可观察 Search/Ask 行为不变（契约证明）
- [ ] Retrieval/Hybrid Eval 不下降（或注明因环境跳过及原因）
- [ ] 生产代码不读 `last_search_trace` / `last_disclose_claims`
- [ ] MCP Tool Contract 不变
- [ ] DB Schema 无变化

- [x] **Step 1:** 跑测试与 eval（相关子集 124 passed；Retrieval/Hybrid Eval 建议合并前补跑）
- [x] **Step 2:** 写验收报告
- [x] **Step 3:** 更新 `PROGRESS.md` 一期状态（若仓库惯例需要）

---

## 3. 推荐提交拆分（用户要求提交时执行）

```text
test(contract): freeze search response behavior
test(contract): freeze ask and wiki serving behavior
feat(search): add request-scoped SearchExecution
refactor(answer): consume request-scoped search output
test(search): prove concurrent request isolation
refactor(search): remove shared last-request state
docs(architecture): record phase-1 contracts and handoff
```

实际执行可合并为 2–4 个逻辑提交，但边界应与上表一致。

---

## 4. 回滚策略（与 Spec 对齐）

1. 保留 `SearchService.search()` 永远返回 `list[dict]`
2. 出问题：VerifiedAnswer 可临时回退（不推荐）；优先修 `execute` 组装
3. 契约测试**不回滚**
4. 无 DB 迁移，无数据恢复

---

## 5. 与第二工期承接

交付物：

```text
SearchExecution
Search/Ask/Wiki 契约
并发隔离测试
wiki-invariants.md
phase1 acceptance report
```

第二工期禁止重新引入 Service 内请求状态、禁止改 `SearchExecution` 已冻结字段语义。

---

## 6. Plan 自审记录

### 6.1 Spec coverage

| Spec 条目 | Plan Task |
|---|---|
| 1.1 Search 契约 | Task 3 |
| 1.2 Ask 契约 | Task 4 |
| 1.3 Wiki Serving 不变量 | Task 5 |
| 1.4 SearchService.execute | Task 1 |
| 1.5 迁移 Verified Answer | Task 2 |
| 1.6 并发隔离测试 | Task 6 |
| 1.7 删除共享状态 | Task 6 |
| 验收 / 回滚 / 二期承接 | Task 7 + §3–5 |

### 6.2 Placeholder scan

无 TBD/TODO/「适当处理」。快照字段、归一化规则、命令、文件路径均已写明。

### 6.3 与代码一致性

- `assemble_answer_payload` 签名保持，仅改变 `ask()` 的数据获取方式 → 用户可观察行为不变
- 现有 `test_verified_hybrid_search` 对 `last_search_trace` 的依赖已纳入迁移
- eval 路径纳入，避免「测试绿、评测仍读 last_*」
- `_last_claim_error` 一并迁入请求状态（Spec 未点名但是同类并发隐患）

### 6.4 风险与减负

| 风险 | 缓解 |
|---|---|
| 全量 pytest 过慢 | 先契约+相关，再全量 |
| 快照过拟合浮点 | 归一化 4 位小数 + 忽略 elapsed |
| Mock 与生产路径偏差 | 契约用真实 SearchService 类 + mock 下游，不 mock 编排本身 |
| 删除 last_* 漏调用方 | rg 门禁 + 并发测试 |

### 6.5 执行策略

用户要求「自审 PLAN 后自主推进」。本会话采用 **Inline Execution**（同会话按 Task 1→7 推进），不拆 worktree（改动集中在 search/answer/tests/docs，冲突面可控）。

---

## 7. 成功标准（可验证）

```bash
pytest tests/test_public_search_contract.py tests/test_public_ask_contract.py \
  tests/test_wiki_serving_contract.py tests/test_search_request_isolation.py -v
# 全部 PASS

rg "last_search_trace|last_disclose_claims|get_disclose_claim_rows" src evals
# 无匹配（或仅注释/字符串文档）
```
