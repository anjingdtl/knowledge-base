# Phase 3：Answer、MCP、Container 与存储治理 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (本工期跨层耦合紧，推荐 inline 顺序执行；每子阶段独立验收后再进入下一子阶段).

**Goal:** 在不改 Retrieval/Wiki Serving 语义的前提下，统一 Answer 编排、将 MCP 收束为协议适配层、按能力分组 Container，并冻结运行时 `_migrate()` + 登记 Legacy 弃用，形成 v1.9.0 可维护边界。

**Architecture:** 以二期 `SearchService.execute()` / `RetrievalOrchestrator` / `SearchExecution` 为唯一检索入口。新增 `src/answering/` 产出 `AnswerExecution`；MCP 拆为 `runtime/auth/policies/envelopes/tools/*`，`mcp_server.py` 降为兼容入口；Container 增加 Core/Verified/Authoring/Experimental 分组代理；DB 以文档+测试冻结 `_migrate()`，Alembic 为新 Schema 权威。

**Tech Stack:** Python 3.10+、pytest、FastMCP、现有 VerifiedAnswerService / tool_registry / AppContainer / Alembic

**Spec:** `docs/superpowers/specs/03-maintainability-phase-3-application-infrastructure.md`  
**前置：** Phase-1 ✅ + Phase-2 ✅（v1.8.2）；Retrieval 默认仍可 `legacy`，Answer 只依赖 `SearchService.execute()` → Orchestrator  
**目标版本：** `v1.9.0`

---

## 0. 现状勘察（2026-07-14）

| 项 | 现状 | 三期动作 |
|---|---|---|
| `SearchExecution` / Orchestrator | ✅ 二期 | Answer 只经 `execute()` 消费 |
| `VerifiedAnswerService.ask` | `src/services/verified_answer.py` ~495 行 | 抽 `AnswerService` + 兼容壳 |
| MCP | `mcp_server.py` ~3662 行单体 | 拆 runtime/auth/tools/* |
| Tool Registry | `src/mcp/tool_registry.py` 已声明式 | **保留** |
| Container | `AppContainer` 扁平 lazy 属性 | 分组 provider + 旧属性代理 |
| Repositories | 已有 knowledge/block/job/wiki… | 本期不搬 CRUD 大手术 |
| `_migrate()` | `db.py:743+` 历史补丁 | **冻结** + 策略测试 |
| Alembic | `alembic/versions/*` 已有 | 基线校验测试 |
| `get_active_container` / `_get_container` | 广泛使用 | 弃用登记；新代码禁止（架构测试） |

**非目标确认：** 不改 RRF/Gate/Claim/Canonical；不新增 MCP 工具；不换 FastMCP/SQLite；不一次性清空 `db.py`；不删全部 Legacy。

---

## 1. 文件地图

### 3A Answer

| 动作 | 路径 | 职责 |
|---|---|---|
| Create | `src/answering/__init__.py` | 导出 |
| Create | `src/answering/models.py` | `AnswerExecution` |
| Create | `src/answering/context_builder.py` | 上下文构造（委托现有 `_build_generation_context`） |
| Create | `src/answering/generation.py` | LLM / evidence-summary fallback |
| Create | `src/answering/service.py` | `AnswerService` 编排 |
| Create | `src/answering/shadow.py` | Shadow 对比（非 LLM 文本） |
| Create | `tests/answering/*` | 契约与 shadow |
| Modify | `src/services/verified_answer.py` | 兼容入口 → AnswerService |
| Modify | `src/mcp_server.py` `_do_ask` | 经 AnswerService |
| Modify | `config.example.yaml` | `answer.orchestrator` |

### 3B MCP

| 动作 | 路径 | 职责 |
|---|---|---|
| Create | `src/mcp/runtime.py` | container / lifespan / heartbeat |
| Create | `src/mcp/auth.py` | TokenVerifier |
| Create | `src/mcp/envelopes.py` | 再导出 envelope |
| Create | `src/mcp/policies.py` | write/authoring 选择 kwargs |
| Create | `src/mcp/server.py` | FastMCP 实例 + main + 注册 |
| Create | `src/mcp/tools/*.py` | 按域工具 |
| Rewrite | `src/mcp_server.py` | `from src.mcp.server import main, mcp` |

### 3C Container

| 动作 | 路径 | 职责 |
|---|---|---|
| Create | `src/core/service_groups.py` | 四组服务视图 |
| Modify | `src/core/container.py` | 挂载 groups + 属性代理 |
| Create | `tests/architecture/test_import_boundaries.py` | 禁止反向依赖 |

### 3D DB / Legacy

| 动作 | 路径 | 职责 |
|---|---|---|
| Create | `docs/architecture/database-migration-policy.md` | 冻结策略 |
| Create | `tests/test_database_migration_policy.py` | 禁止 `_migrate` 增长哨兵 |
| Create | `docs/migration/deprecation-register.md` | 弃用表 |
| Create | `tests/test_alembic_baseline.py` | 空库 upgrade 冒烟（若环境允许） |
| Create | 验收/发布文档 | v1.9.0 |

---

## 2. 子阶段 3A：统一 Answer

### Task A0: 准入 + 分支

```bash
pytest tests/test_public_ask_contract.py tests/test_public_search_contract.py \
  tests/retrieval/ tests/test_mcp_contract.py -q
git checkout -b feat/maintainability-phase-3-app-infra
```

### Task A1: AnswerExecution 契约

```python
# src/answering/models.py
@dataclass(frozen=True)
class AnswerExecution:
    answer: str
    answer_mode: str
    sources: tuple[dict, ...]
    claims_used: tuple[dict, ...]
    raw_evidence_used: tuple[dict, ...]
    conflicts: tuple[dict, ...]
    fallbacks: tuple[dict, ...]
    warnings: tuple[str, ...]
    trace_id: str
    # 兼容 MCP 扩展字段（不进 frozen 核心时可放 payload 方法）
    def to_ask_payload(self) -> dict: ...
```

额外字段 `route/source_graph/...` 由 `to_ask_payload()` 或 `AnswerService.ask_dict()` 补齐，保证 `REQUIRED_ASK_KEYS` 不变。

### Task A2: ContextBuilder + Generation

- `ContextBuilder.build(claim_rows, raw_rows, conflicts)` → 委托 `verified_answer._build_generation_context`
- `Generator.generate(question, context, llm)` → 现有 LLM 路径 + strip_think
- `Generator.evidence_summary_fallback(...)` → `_fallback_hybrid_text` / `_fallback_raw_text`

### Task A3: AnswerService

```python
class AnswerService:
    def __init__(self, search_service, llm=None, config=None):
        ...
    def execute(self, question, *, top_k=5, use_llm=True, llm_answer=None) -> AnswerExecution:
        # 1. search_service.execute → SearchExecution  （经 Orchestrator，不直接 DB/Wiki）
        # 2. assemble via existing assemble_answer_payload（或迁入 answering）
        # 3. 包装 AnswerExecution
    def ask(self, ...) -> dict:
        return self.execute(...).to_ask_payload()  # 含 route 等兼容字段
```

**禁止：** 直接 DB search、直接 Wiki repo、重跑 Gate、构造 MCP envelope。

### Task A4: Shadow + 配置

```yaml
answer:
  orchestrator: legacy   # legacy | shadow | unified
```

- `legacy`：现 `VerifiedAnswerService` 内部路径（可即委托同一 assemble）
- `shadow`：legacy 返回 + 对比 unified 结构字段
- `unified`：AnswerService 正式路径

对比：answer_mode / claim IDs / raw evidence IDs / conflicts / fallbacks / citation keys / no-answer — **不比 LLM 全文**。

### Task A5: 接线与回归

- `VerifiedAnswerService.ask` → 调用 `AnswerService.ask`（或按 mode 分支）
- MCP `_do_ask` 的 verified 分支 → `AnswerService`
- `pytest tests/answering tests/test_public_ask_contract.py tests/test_verified_answer.py -q`

默认 **legacy 或 unified 行为等价**（同一 assemble 时默认可直接 unified 委托，仍保留配置开关）。

---

## 3. 子阶段 3B：拆分 MCP

### 策略（务实）

1. 先抽 **非工具** 基础设施（runtime/auth/envelopes/policies）  
2. 将工具实现按域迁入 `src/mcp/tools/`（检索优先；其余域整文件迁移）  
3. `src/mcp/server.py` 持有 `mcp`、注册、prompts、main  
4. `src/mcp_server.py` 仅：

```python
from src.mcp.server import main, mcp
__all__ = ["main", "mcp"]
```

5. **不改** tool 名 / schema / profile / annotations / write_policy  

### Task B1–B4

- runtime: `_container`, `_get_container`, lifespan, heartbeat  
- auth: `_StaticTokenVerifier`  
- envelopes: re-export `ok/fail/ErrorCode/...`  
- policies: tool selection kwargs + write checks helpers  

### Task B5: tools 分域

| 模块 | 工具（现网名） |
|---|---|
| retrieval | ping, kb_capabilities, search, ask, read, list_knowledge, … |
| ingest | index_path, get_job, list_jobs, reindex_all, … |
| administration | create/update/delete/restore/ops/undo … |
| wiki | wiki_* |
| graph | graph_* experimental |
| memory | agent memory experimental |

共享 helper（`_heartbeat`, `_resolve_query_alias`）放 `src/mcp/tools/_common.py`。

### Task B6: 验收

```bash
pytest tests/test_mcp_contract.py tests/test_mcp_tool_profiles.py \
  tests/test_mcp_server.py tests/test_public_ask_contract.py -q
```

---

## 4. 子阶段 3C：Container 分组

### Task C1: service_groups

```python
@dataclass
class CoreEvidenceServices:
    db: Any
    search_service: Any  # lazy callable or property holder
    ...

class ServiceGroups:
    def __init__(self, container: AppContainer):
        self._c = container
    @property
    def core(self) -> CoreEvidenceServices: ...
    @property
    def verified(self) -> VerifiedServingServices: ...
    @property
    def authoring(self) -> AuthoringServices: ...
    @property
    def experimental(self) -> ExperimentalServices: ...
```

AppContainer 增加 `groups` 属性；**保留**全部现有 `@property search_service` 等代理（回滚路径）。

### Task C2: 架构测试

禁止（AST/路径扫描）：

```text
src/retrieval → src/mcp
src/answering → src/mcp
src/repositories → src/core/container
```

新业务模块扫描 `get_active_container(` / `Database._instance`（允许白名单：mcp/runtime、compat、gui）。

---

## 5. 子阶段 3D：DB + Legacy

### Task D1: 冻结 `_migrate()`

- 文档：`docs/architecture/database-migration-policy.md`
- 测试：对 `db.py` 中 `def _migrate` 函数体做 **hash/行数快照**，防止新增 ALTER/CREATE（允许注释微调则用结构化解析「禁止新增 ALTER TABLE/CREATE TABLE 语句」更稳）

推荐实现：解析 `_migrate` 源码，收集 `ALTER TABLE` / `CREATE TABLE` 语句集合，与冻结清单 `tests/fixtures/migrate_statements.json` 比较。

### Task D2: Alembic

- `tests/test_alembic_baseline.py`：临时目录 `alembic upgrade head` 不抛错（若 CI 无 alembic 则 skip）
- 不在本 PR 搬迁大量 CRUD

### Task D3: 弃用登记

`docs/migration/deprecation-register.md` 按 Spec §16 填表。

### Task D4: v1.9.0 验收报告

`docs/superpowers/reviews/maintainability-phase3-acceptance.md`  
`docs/release/v1.9.0-release-notes.md`  
bump `src/version.py` → `1.9.0`

---

## 6. 推荐提交拆分（与 Spec §22 对齐）

```text
docs(plan): phase-3 application infrastructure plan
feat(answer): introduce AnswerExecution and AnswerService
feat(answer): add shadow mode and config switch
refactor(mcp): extract runtime auth policies envelopes
refactor(mcp): split tools by domain and shrink mcp_server entry
refactor(container): group core verified authoring experimental
test(architecture): enforce dependency boundaries
docs(database): freeze runtime migration policy
test(database): alembic baseline smoke + migrate freeze
docs(migration): register legacy deprecations
docs(release): v1.9.0 maintainability phase-3 acceptance
```

---

## 7. 验收清单（Spec §18 摘要）

- [ ] Answer 单一编排入口 + AnswerExecution  
- [ ] no-answer/timeout/conflict/fallback 契约通过  
- [ ] mcp_server 无业务编排（仅 re-export）  
- [ ] MCP Tool Contract / Profile 通过  
- [ ] Container 四组边界 + 旧属性可用  
- [ ] 架构边界测试通过  
- [ ] `_migrate` 冻结测试通过  
- [ ] 弃用登记发布  
- [ ] 相关回归全绿  

---

## 8. 执行说明

用户要求「写 PLAN 后自主推进」。执行策略：

1. 本文件落盘后立即在功能分支按 **3A → 3B → 3C → 3D** 顺序实现  
2. **默认 answer.orchestrator 与行为保持契约快照通过**（优先同一 assemble 路径保证 100% 结构一致）  
3. 不改 `retrieval.orchestrator` 默认；不删 Legacy Search  
4. MCP 拆分以契约测试为门禁，失败立即回退单文件导入  
5. 完成后提交；是否 push master 由用户后续指示（本 PLAN 执行默认提交到功能分支）

## 9. Self-Review

| Spec | Task |
|---|---|
| 3A AnswerExecution/Service | A1–A5 |
| 3A Shadow | A4 |
| 3B MCP 目录/边界 | B1–B6 |
| 3C 四组 + 反向依赖 | C1–C2 |
| 3D freeze/Alembic/deprecations | D1–D4 |
| 非目标 | 全文禁止项 |
