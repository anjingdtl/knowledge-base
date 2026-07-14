# Phase 2：Retrieval 编排统一与 Wiki Serving 隔离 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变检索算法、SearchExecution 语义、MCP/Container/DB 的前提下，将 SearchService 内双主管线拆成 `RawRetriever` + `VerifiedProvider` + Policy + `RetrievalOrchestrator`，以 `legacy → shadow → unified` 安全切换。

**Architecture:** 新增 `src/retrieval/` 包作为内部编排层。首轮全部采用**适配器**包装现有 `SearchService` 私有方法与 `WikiServingGate`，不搬迁算法实现。`SearchService.execute()` 保留公开入口，按 `retrieval.orchestrator` 配置在 legacy / shadow / unified 间切换；默认 `legacy` 保证可回滚。禁止改 AppContainer 构造签名（Orchestrator 在 SearchService 内懒创建）。

**Tech Stack:** Python 3.10+、pytest、现有 SearchService / WikiServingGate / verified_hybrid_fusion / SearchExecution

**Spec:** `docs/superpowers/specs/02-maintainability-phase-2-retrieval-wiki.md`  
**前置：** Phase-1 验收 ✅（`SearchExecution`、无 `last_*`、契约/隔离门禁）  
**基线版本:** `src/version.py` = `1.8.1`（本期目标候选 `v1.8.2`，验收后 bump）

---

## 0. 现状勘察（2026-07-14）

| 项 | 现状 | 二期动作 |
|---|---|---|
| `SearchExecution` | `src/models/search_execution.py` ✅ | 作为 Orchestrator 最终返回契约，**不改语义** |
| `last_*` | 生产路径已删除 ✅ | 保持 |
| 双主管线 | `execute()` → `_search_verified_hybrid` / `_search_legacy_pipeline` | 抽边界 + Policy 选择 |
| Claim 检索 | `_safe_verified_claim_retrieve` + Gate | → `VerifiedProvider` |
| Raw 路径 | `_raw_retrieve` / hybrid / rerank / diversity / package | → `RawRetriever` 适配器 |
| Fusion | `verified_hybrid_fusion.fuse_verified_and_raw` | 留在 VerifiedPolicy，不改算法 |
| `src/retrieval/` | **不存在** | 新建 |
| `retrieval.orchestrator` 配置 | **不存在** | 默认 `legacy` |
| AppContainer | 构造 `SearchService(...)` | **不改**（Spec 非目标） |

**关键文件与职责映射：**

| 现有符号 | 约行 | 映射到 |
|---|---|---|
| `SearchService._safe_verified_claim_retrieve` | ~510 | `VerifiedProvider.serve` |
| `SearchService._raw_retrieve` + rewrite/rerank/diversity/package | ~545+ | `RawRetriever.retrieve`（委托） |
| `SearchService._search_legacy_pipeline` | ~210 | `EvidenceOnlyPolicy`（经适配器复现同等结果） |
| `SearchService._search_verified_hybrid` | ~275 | `VerifiedPolicy` |
| `SearchService.execute` | ~135 | Facade + mode 开关 |
| `WikiServingGate.filter_servable` | gate | VerifiedProvider 唯一 Serving 入口 |

**非目标（禁止触碰）：**

- Ask / LLM Answer 重写、MCP 拆分、Tool Profile、AppContainer 结构、DB Schema
- Canonical Wiki 写入 / Projection / Maintenance / Claim 提取 / Auto Publish
- RRF / Rerank / Citation **算法**（可搬家包装，不可改逻辑）

---

## 1. 文件地图

| 动作 | 路径 | 职责 |
|---|---|---|
| Create | `src/retrieval/__init__.py` | 包导出 |
| Create | `src/retrieval/models.py` | `RawRetrievalResult` / `VerifiedServingResult` |
| Create | `src/retrieval/execution.py` | re-export `SearchExecution` + 组装 helper |
| Create | `src/retrieval/verified_provider.py` | Wiki Serving 适配器 |
| Create | `src/retrieval/raw_retriever.py` | Raw 检索边界适配器 |
| Create | `src/retrieval/policies/__init__.py` | policy 导出 |
| Create | `src/retrieval/policies/base.py` | `RetrievalPolicy` Protocol |
| Create | `src/retrieval/policies/evidence_only.py` | EvidenceOnlyPolicy |
| Create | `src/retrieval/policies/verified.py` | VerifiedPolicy |
| Create | `src/retrieval/orchestrator.py` | 模式选择 + Policy 分发 |
| Create | `src/retrieval/shadow_comparator.py` | 新旧结果对比（无敏感正文） |
| Modify | `src/services/search_service.py` | 暴露适配委托点；Facade 路由；默认 legacy |
| Modify | `config.example.yaml` | 增加 `retrieval.orchestrator: legacy` |
| Create | `tests/retrieval/__init__.py` | 测试包 |
| Create | `tests/retrieval/test_models.py` | 契约不可变/字段 |
| Create | `tests/retrieval/test_verified_provider.py` | Serving 边界 |
| Create | `tests/retrieval/test_raw_retriever.py` | Raw 边界 |
| Create | `tests/retrieval/test_retrieval_policies.py` | Policy 行为 |
| Create | `tests/retrieval/test_orchestrator.py` | 模式选择 |
| Create | `tests/retrieval/test_shadow_comparison.py` | Shadow 对比 |
| Create | `tests/retrieval/test_wiki_serving_gates_phase2.py` | Spec §7 Wiki 门禁 |
| Create | `docs/superpowers/reviews/maintainability-phase2-acceptance.md` | 二期验收 |
| Create | `docs/superpowers/handoffs/2026-07-14-maintainability-phase2-handoff.md` | 三期承接 |

---

## 2. 目标内部契约

```python
# src/retrieval/models.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class RawRetrievalResult:
    candidates: tuple[dict[str, Any], ...]
    trace: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    fallbacks: tuple[dict[str, Any], ...] = ()

@dataclass(frozen=True)
class VerifiedServingResult:
    eligible_claims: tuple[dict[str, Any], ...]  # serializable claim rows / pairs
    disclose_claims: tuple[dict[str, Any], ...]
    conflicts: tuple[dict[str, Any], ...] = ()
    trace: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    fallback_reason: str | None = None
    # internal: keep (Claim, ServingDecision) for fusion without re-query
    claim_pairs: tuple[Any, ...] = ()
```

`SearchExecution` 继续从 `src.models.search_execution` 导入；`src/retrieval/execution.py` 仅 re-export + `build_execution(results, state_dict)` 小工具，**不得复制/改字段**。

---

## 3. 任务分解

### Task 0: 准入核验与分支

**Files:** 无代码改动

- [ ] **Step 0.1:** 确认 Phase-1 门禁

```bash
pytest tests/test_public_search_contract.py \
  tests/test_public_ask_contract.py \
  tests/test_wiki_serving_contract.py \
  tests/test_search_request_isolation.py \
  tests/test_mcp_contract.py -q
```

Expected: 全绿（或仅既有 skip）

- [ ] **Step 0.2:** 创建功能分支

```bash
git checkout -b feat/maintainability-phase-2-retrieval-wiki
```

---

### Task 1: 定义 Retrieval 内部契约（Spec 2.1）

**Files:**
- Create: `src/retrieval/__init__.py`
- Create: `src/retrieval/models.py`
- Create: `src/retrieval/execution.py`
- Create: `tests/retrieval/__init__.py`
- Create: `tests/retrieval/test_models.py`

- [ ] **Step 1.1: 写失败测试**

```python
# tests/retrieval/test_models.py
from dataclasses import FrozenInstanceError
import pytest
from src.retrieval.models import RawRetrievalResult, VerifiedServingResult
from src.retrieval.execution import SearchExecution  # re-export

def test_raw_result_frozen():
    r = RawRetrievalResult(candidates=({"id": "a"},), trace={"mode": "raw"})
    with pytest.raises(FrozenInstanceError):
        r.candidates = ()  # type: ignore[misc]

def test_verified_result_defaults():
    v = VerifiedServingResult(eligible_claims=(), disclose_claims=())
    assert v.conflicts == ()
    assert v.fallback_reason is None
    assert v.warnings == ()

def test_search_execution_reexport_is_same_type():
    from src.models.search_execution import SearchExecution as Canonical
    assert SearchExecution is Canonical
```

- [ ] **Step 1.2:** `pytest tests/retrieval/test_models.py -v` → FAIL（模块不存在）

- [ ] **Step 1.3: 最小实现**

```python
# src/retrieval/__init__.py
"""Retrieval orchestration package (Phase-2 maintainability)."""
from src.retrieval.models import RawRetrievalResult, VerifiedServingResult

__all__ = ["RawRetrievalResult", "VerifiedServingResult"]

# src/retrieval/models.py — 见 §2
# src/retrieval/execution.py
from src.models.search_execution import SearchExecution
__all__ = ["SearchExecution"]
```

- [ ] **Step 1.4:** 测试 PASS

- [ ] **Step 1.5:** Commit `feat(retrieval): define raw and verified execution contracts`

---

### Task 2: 抽取 VerifiedProvider 适配器（Spec 2.2）

**Files:**
- Create: `src/retrieval/verified_provider.py`
- Create: `tests/retrieval/test_verified_provider.py`
- Modify: `src/services/search_service.py` — 将 claim 检索逻辑抽为可被 Provider 调用的共享函数，或让 Provider 内联现有逻辑（**优先独立实现同等逻辑，不改 Gate**）

**行为契约（与 `_safe_verified_claim_retrieve` 对齐）：**

1. `wiki_repository is None` → 空结果，`fallback_reason="no_wiki_repository"`
2. Gate 过滤：`include_disclose=True`，limit 生效
3. 按 `claim_retrieval_score` 排序截断
4. 异常不抛出 → `fallback_reason` + 空 claims
5. 输出拆分：`eligible`（非 disclose_only）vs `disclose_only`

- [ ] **Step 2.1: 失败测试**

```python
# tests/retrieval/test_verified_provider.py
from unittest.mock import MagicMock
from src.retrieval.verified_provider import VerifiedProvider

def test_no_repo_returns_fallback():
    p = VerifiedProvider(wiki_repository=None, wiki_serving_gate=None)
    r = p.serve("q", limit=5)
    assert r.eligible_claims == ()
    assert r.fallback_reason == "no_wiki_repository"

def test_gate_exception_does_not_raise():
    repo = MagicMock()
    repo.list_claims.side_effect = RuntimeError("boom")
    p = VerifiedProvider(wiki_repository=repo, wiki_serving_gate=MagicMock())
    r = p.serve("q", limit=5)
    assert r.eligible_claims == ()
    assert r.fallback_reason is not None
    assert "boom" in (r.fallback_reason or "")

def test_filters_via_gate_not_raw_list():
    """Provider must call gate.filter_servable, not invent eligibility."""
    from src.models.wiki_v2 import Claim, ClaimStatus  # 按项目实际模型
    # 构造 mock claim + gate 返回 pairs
    ...
```

- [ ] **Step 2.2:** 实现 `VerifiedProvider`

```python
# src/retrieval/verified_provider.py
class VerifiedProvider:
    """受 Serving Gate 保护的 Wiki Claim 读取能力。

    不负责 Raw / Vector / RRF / Rerank / Answer / MCP / 写入。
    """
    def __init__(self, wiki_repository=None, wiki_serving_gate=None, config=None):
        self._repo = wiki_repository
        self._gate = wiki_serving_gate
        self._config = config or {}

    def serve(self, query: str, *, limit: int = 10) -> VerifiedServingResult:
        ...
```

实现要点：把 `SearchService._safe_verified_claim_retrieve` 的逻辑迁入/复制到此，然后让 SearchService 方法委托 Provider（**行为不变**）。

- [ ] **Step 2.3:** 更新 `SearchService._safe_verified_claim_retrieve` 委托 Provider（legacy 路径行为不变）

```python
def _safe_verified_claim_retrieve(self, query, limit, state=None):
    from src.retrieval.verified_provider import VerifiedProvider
    provider = VerifiedProvider(self._wiki_repository, self._wiki_serving_gate, self._config)
    result = provider.serve(query, limit=limit)
    if state is not None and result.fallback_reason:
        state.claim_error = result.fallback_reason
    return list(result.claim_pairs)
```

- [ ] **Step 2.4:** `pytest tests/retrieval/test_verified_provider.py tests/test_verified_hybrid_search.py tests/test_wiki_serving_contract.py -q` PASS

- [ ] **Step 2.5:** Commit `refactor(wiki): isolate verified serving provider`

---

### Task 3: 抽取 RawRetriever 适配器（Spec 2.3）

**Files:**
- Create: `src/retrieval/raw_retriever.py`
- Create: `tests/retrieval/test_raw_retriever.py`

**首轮策略：适配器委托 SearchService 现有方法，禁止搬迁全部 Raw 代码。**

```python
class RawRetriever:
    """证据检索边界。首轮委托 SearchService 私有管线片段。"""

    def __init__(self, search_service: "SearchService"):
        self._svc = search_service

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        include_legacy_wiki_fts: bool = True,
    ) -> RawRetrievalResult:
        """对齐 _search_legacy_pipeline 中 raw 部分的结果语义。

        include_legacy_wiki_fts=True 时与 evidence_only 主路径一致
        （legacy wiki FTS 前置 + raw package）。
        """
        # 委托：rewrite → raw_retrieve → rerank → diversity → package
        # 或直接调用 svc 的一个新公开适配方法 _legacy_raw_pipeline_result
```

推荐：在 `SearchService` 增加 **非公开** 方法 `_run_raw_pipeline_for_adapter(...)` 返回 `(list[dict], trace_fragment, warnings)`，避免 RawRetriever 直接摸一堆私有细节；方法体从 `_search_legacy_pipeline` 抽出，`_search_legacy_pipeline` 调用它。

- [ ] **Step 3.1:** 测试：无 hybrid 时 fallback；warnings 结构；candidates 为 tuple

- [ ] **Step 3.2:** 实现适配器 + 抽取 helper

- [ ] **Step 3.3:** 既有 `tests/test_search_service.py` 仍 PASS

- [ ] **Step 3.4:** Commit `refactor(retrieval): add raw retriever boundary`

---

### Task 4: EvidenceOnlyPolicy + VerifiedPolicy（Spec 2.4）

**Files:**
- Create: `src/retrieval/policies/base.py`
- Create: `src/retrieval/policies/evidence_only.py`
- Create: `src/retrieval/policies/verified.py`
- Create: `src/retrieval/policies/__init__.py`
- Create: `tests/retrieval/test_retrieval_policies.py`

```python
# base.py
from typing import Protocol, Any
from src.models.search_execution import SearchExecution

class RetrievalPolicy(Protocol):
    def execute(
        self,
        query: str,
        *,
        top_k: int = 5,
        query_spec: Any = None,
        deadline: float | None = None,
    ) -> SearchExecution: ...
```

#### EvidenceOnlyPolicy

```text
query_spec? → 结构化结果
else RawRetriever(include_legacy_wiki_fts=True) → SearchExecution
```

首轮为保证 100% 行为一致：**直接委托** `SearchService._search_legacy_pipeline` + `_to_execution`，内部使用 RawRetriever 仅做结构化包装也可；验收以契约快照为准。

推荐实现（安全优先）：

```python
class EvidenceOnlyPolicy:
    def __init__(self, search_service):
        self._svc = search_service
        self._raw = RawRetriever(search_service)

    def execute(self, query, *, top_k=5, query_spec=None, deadline=None) -> SearchExecution:
        # 与 SearchService.execute 的 legacy 分支完全一致
        return self._svc._execute_legacy(query, top_k=top_k, query_spec=query_spec)
```

#### VerifiedPolicy

```text
RawRetriever + VerifiedProvider → fusion → SearchExecution
保证：Wiki 失败不阻断 Raw；Gate 不可绕过；conflict 进 execution.conflicts；fallback 记录
```

首轮同样委托 `_search_verified_hybrid` / 抽出的 `_execute_verified`，Provider 已在 claim 路径生效。

**Verified 必须保证（测试钉死）：**

| 条件 | 期望 |
|---|---|
| Gate 抛错 | Raw 结果仍返回，`fallbacks` 含 reason |
| stale/unsupported/retracted | 不进 eligible（Gate 测 + Provider 测） |
| conflict | `execution.conflicts` 非空且 trace.conflict_disclosed |
| empty wiki | fallback empty_wiki_to_raw |

- [ ] **Step 4.1–4.4:** TDD 实现 + `tests/test_public_search_contract.py` PASS

- [ ] **Step 4.5:** Commit `feat(retrieval): add evidence-only and verified policies`

---

### Task 5: RetrievalOrchestrator（Spec 2.5）

**Files:**
- Create: `src/retrieval/orchestrator.py`
- Create: `tests/retrieval/test_orchestrator.py`

```python
class RetrievalOrchestrator:
    """选择 Policy，返回 SearchExecution。不生成 Answer，不构造 MCP Envelope。"""

    def __init__(self, search_service, config=None):
        self._svc = search_service
        self._config = config if config is not None else search_service._config

    def _orchestrator_mode(self) -> str:
        # retrieval.orchestrator: legacy | shadow | unified
        # 支持 Config 对象与 dict
        ...

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        query_spec=None,
        deadline=None,
    ) -> SearchExecution:
        mode = self._orchestrator_mode()
        if mode == "legacy":
            return self._svc._execute_primary_legacy(query, top_k=top_k, query_spec=query_spec)
        if mode == "shadow":
            primary = self._svc._execute_primary_legacy(...)
            try:
                candidate = self._execute_unified(...)
                compare_and_log(primary, candidate)  # no full text
            except Exception as e:
                log shadow failure only
            return primary
        # unified
        return self._execute_unified(query, top_k=top_k, query_spec=query_spec, deadline=deadline)

    def _execute_unified(...):
        if query_spec is not None:
            return self._svc._execute_query_spec(...)
        if self._svc._should_use_verified_hybrid():
            return VerifiedPolicy(self._svc).execute(...)
        return EvidenceOnlyPolicy(self._svc).execute(...)
```

- [ ] **Step 5.1:** 测试 mode 选择、unified 不改 SearchExecution 字段

- [ ] **Step 5.2:** 实现

- [ ] **Step 5.3:** Commit `feat(retrieval): introduce retrieval orchestrator`

---

### Task 6: Shadow Comparator + 配置（Spec 2.6）

**Files:**
- Create: `src/retrieval/shadow_comparator.py`
- Create: `tests/retrieval/test_shadow_comparison.py`
- Modify: `config.example.yaml` 增加：

```yaml
# 检索编排切换（Phase-2）：legacy | shadow | unified
retrieval:
  orchestrator: legacy
```

**对比字段（禁止记录全文）：**

```python
@dataclass
class ShadowDiff:
    source_id_overlap_top_k: float
    claim_ids_match: bool
    conflicts_match: bool
    fallbacks_match: bool
    citation_keys_match: bool
    exception_types: tuple[str, ...]
    latency_ms_primary: float
    latency_ms_candidate: float
    notes: tuple[str, ...]
```

Source ID 提取规则：

- `knowledge_id` / `block_id` / `claim_id` / `source` 组合键
- Top-K overlap = `|A∩B| / max(|A|,1)` 对 top_k 列表

- [ ] **Step 6.1–6.3:** TDD + 确保日志只有 ID/计数/reason，无 `text`/`content` 全文

- [ ] **Step 6.4:** Commit `feat(retrieval): add shadow comparison mode`

---

### Task 7: SearchService 变为 Facade（Spec 2.7）

**Files:**
- Modify: `src/services/search_service.py`

目标结构：

```python
def execute(self, query, top_k=5, query_spec=None) -> SearchExecution:
    orch = self._get_orchestrator()
    return orch.search(query, top_k=top_k, query_spec=query_spec)

def _get_orchestrator(self) -> RetrievalOrchestrator:
    # 懒创建，挂实例缓存即可（无请求状态）
    if getattr(self, "_orchestrator", None) is None:
        from src.retrieval.orchestrator import RetrievalOrchestrator
        self._orchestrator = RetrievalOrchestrator(self, self._config)
    return self._orchestrator

def _execute_primary_legacy(...):
    """原 execute 主体，供 legacy/shadow 主路径与回滚。"""
    # 现有 execute 逻辑原样搬入
```

**约束：**

- 默认配置缺失时视为 `legacy`
- `search()` 仍 `list(execute().results)`
- **不改** Container 构造
- 旧 `_search_*` 方法本期保留

- [ ] **Step 7.1:** 契约回归

```bash
pytest tests/test_public_search_contract.py \
  tests/test_public_ask_contract.py \
  tests/test_search_service.py \
  tests/test_verified_hybrid_search.py \
  tests/test_search_request_isolation.py \
  tests/retrieval/ -q
```

- [ ] **Step 7.2:** Commit `refactor(search): route facade through unified orchestrator`

---

### Task 8: Wiki + Shadow 门禁测试（Spec §7–§8）

**Files:**
- Create: `tests/retrieval/test_wiki_serving_gates_phase2.py`
- Create: `tests/retrieval/test_shadow_cutover_gates.py`（阈值表断言）

覆盖 Spec §7 每一条（可用 mock gate/claim）：

```text
active Claim 正常增强
stale / unsupported / retracted 过滤
Evidence 丢失过滤
Conflict 披露
Projection/Repo 不可用 → Raw fallback
Authoring 关闭不影响 Serving（read gate 独立）
Auto Publish 保持关闭（仅断言配置/不调用 publish API）
```

Shadow 门槛（单元级 mock 相等场景必须 100%）：

```text
Eligible Claim 一致率 100%
Conflict 一致率 100%
Fallback 一致率 100%
Citation Completeness 100%
```

- [ ] **Step 8.1:** 实现门禁测试

- [ ] **Step 8.2:** 全量相关回归 PASS

- [ ] **Step 8.3:** Commit `test(retrieval): enforce wiki and shadow cutover gates`

---

### Task 9: 切换策略与「不删除 Legacy」（Spec 2.8）

**本期落地状态目标（务实）：**

| 模式 | 状态 |
|---|---|
| `legacy` | **默认正式**，完整保留主路径代码 |
| `shadow` | 可用；测试中强制跑一次对比 |
| `unified` | 实现完整；测试可切换验证；**默认不改生产 config.yaml** |

**不得**在首次 unified 可用时删除 `_search_legacy_pipeline` / `_search_verified_hybrid`。

删除旧主管线属于 **后续版本**（需 Shadow 门槛 + 至少一版候选验证），本 PLAN 只准备：

- 文档标明删除条件
- 代码中用注释 `LEGACY_PRIMARY_PIPELINE` 标记可删边界

- [ ] **Step 9.1:** 测试 `orchestrator=unified` 时契约快照仍通过

- [ ] **Step 9.2:** 测试 `orchestrator=legacy` 回滚

- [ ] **Step 9.3:** Commit `test(retrieval): verify legacy rollback and unified parity`

---

### Task 10: 验收报告与三期交接（Spec §9–§12）

**Files:**
- Create: `docs/superpowers/reviews/maintainability-phase2-acceptance.md`
- Create: `docs/superpowers/handoffs/2026-07-14-maintainability-phase2-handoff.md`
- Optional: bump `src/version.py` → `1.8.2` + release notes（仅验收全绿后）

验收清单：

```text
[ ] Search 对外仍是 SearchService.execute / search
[ ] Orchestrator 存在且为 unified 路径唯一入口
[ ] VerifiedProvider 独立；Gate 不可绕过
[ ] EvidenceOnly / Verified 经 Policy 区分
[ ] SearchExecution 字段语义不变
[ ] Search/Ask/Wiki 快照全通过
[ ] legacy 可回滚
[ ] Answer / MCP / Container / DB 无架构性改动
[ ] 默认仍为 legacy（或文档明确 unified 启用条件）
```

- [ ] **Step 10.1:** 跑最终回归

```bash
pytest tests/retrieval/ \
  tests/test_public_search_contract.py \
  tests/test_public_ask_contract.py \
  tests/test_wiki_serving_contract.py \
  tests/test_search_request_isolation.py \
  tests/test_search_service.py \
  tests/test_verified_hybrid_search.py \
  tests/test_verified_answer.py \
  tests/test_mcp_contract.py -q
```

- [ ] **Step 10.2:** 写验收与 handoff

- [ ] **Step 10.3:** Commit `docs(architecture): record phase-2 handoff contract`

---

## 4. 推荐提交拆分（与 Spec §14 对齐）

```text
feat(retrieval): define raw and verified execution contracts
refactor(wiki): isolate verified serving provider
refactor(retrieval): add raw retriever boundary
feat(retrieval): add evidence-only and verified policies
feat(retrieval): introduce retrieval orchestrator
feat(retrieval): add shadow comparison mode
refactor(search): route facade through unified orchestrator
test(retrieval): enforce wiki and shadow cutover gates
test(retrieval): verify legacy rollback and unified parity
docs(architecture): record phase-2 handoff contract
```

---

## 5. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 适配器委托导致双重计时/双 trace | unified 路径只走 Policy 一次；shadow 才双跑 |
| ThreadPool 行为差异 | 不改并发结构，委托原方法 |
| Config 读取 Config vs dict | 复用 `SearchService._cfg` 或统一 helper |
| 误改算法 | 禁止改 fusion/rerank/citation 文件逻辑；只搬调用 |
| 默认切 unified 过早 | 默认 `legacy`；unified 仅测试强制 |

---

## 6. Self-Review（对照 Spec）

| Spec 条目 | 对应 Task |
|---|---|
| 2.1 内部契约 | Task 1 |
| 2.2 VerifiedProvider | Task 2 |
| 2.3 RawRetriever | Task 3 |
| 2.4 Policies | Task 4 |
| 2.5 Orchestrator | Task 5 |
| 2.6 Shadow | Task 6 |
| 2.7 SearchService Facade | Task 7 |
| 2.8 删除双主管线 | Task 9（本期**不删除**，仅保留开关与标记） |
| §7 Wiki 门禁 | Task 8 |
| §8 切换标准 | Task 8–9 |
| §10 回滚 | Task 7 + 9 |
| §12 三期交付 | Task 10 |

**Placeholder 扫描：** 无 TBD；具体代码在执行时以本 PLAN 与现网 `search_service.py` 为准。

**类型一致性：** `RetrievalPolicy.execute → SearchExecution`；Provider → `VerifiedServingResult`；Raw → `RawRetrievalResult`。

---

## 7. 执行说明

用户已要求「编制 PLAN 后自主推进」。执行策略：

1. 本文件落地后立即在 `feat/maintainability-phase-2-retrieval-wiki` 分支按 Task 0→10 推进  
2. 采用 **inline executing-plans**（编排重构上下文耦合紧，不适合无共享上下文的多 agent 并行改同一文件）  
3. 每完成逻辑切片即跑相关 pytest；Task 10 前跑完整相关回归  
4. 默认配置保持 `legacy`；unified 可测可切，不在本机强改用户 `config.yaml` 默认行为（仅更新 `config.example.yaml`）
