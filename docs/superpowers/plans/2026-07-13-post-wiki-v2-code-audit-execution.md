# Post Wiki V2 代码审计落地执行 PLAN

> **For agentic workers:** 按 Task 顺序执行；每 Task 用 checkbox 跟踪。修复遵循 TDD（先失败测试或先固定复现，再最小改动）。  
> **配套审查方案：** `docs/superpowers/plans/2026-07-13-post-wiki-v2-code-audit-charter.md`  
> **Goal:** 对 v1.6.0 / Wiki V2 大改造后的代码库做分段审计，修复已确认 P0/P1 BUG，并产出审计报告。  
> **Architecture:** 只读审计为主；修复仅触及缺陷点与对应测试。共享服务层（Container → Repo → Wiki/Search/MCP）按风险段推进。  
> **Tech Stack:** Python 3.12+、pytest、ruff、mypy、SQLite/FTS5/sqlite-vec、FastAPI、FastMCP

---

## 文件与职责（审计触达地图）

| 区域 | 关键文件 | 职责 |
|------|----------|------|
| DI | `src/core/container.py` | 服务装配与 lazy 依赖 |
| 配置 | `src/utils/config.py` | 配置单例、canonical_v2.mode |
| 数据 | `src/services/db.py`, `vectorstore.py`, `block_store.py`, `src/repositories/*` | 持久化与查询 |
| Wiki 主写 | `src/services/wiki_repository.py`, `wiki_projection.py`, `wiki_primary_workflow.py`, `wiki_merge_engine.py` | Canonical 写与投影 |
| Matcher | `src/services/wiki_claim_matcher.py` | claim 对齐/ demote |
| 失效 | `src/services/wiki_dependency_service.py`, `wiki_rebuild_service.py`, `wiki_rebuild_scheduler.py` | 依赖图与重建 |
| 迁移反馈 | `src/services/wiki_v2_migrator.py`, `wiki_feedback_service.py`, `wiki_validator.py` | Phase 6 |
| 检索 | `src/services/search_service.py`, `hybrid_search.py`, `rag_pipeline.py` | 混合检索与 RAG |
| 对外 | `src/mcp_server.py`, `src/api/**`, `src/cli.py` | MCP/API/CLI |
| 守卫测试 | `tests/test_canonical_write_guards.py` | 禁止直接写回归 |
| 报告 | `docs/superpowers/reviews/2026-07-13-post-wiki-v2-code-audit-report.md` | 审计结论 |

**Finding 记录模板（报告内使用）：**

```markdown
### F-XXX — [标题]
- **段:** S#
- **严重度:** P0|P1|P2|P3
- **位置:** `path:line` / 符号名
- **现象:** …
- **证据:** 测试名 / 复现步骤 / 代码摘录
- **期望:** …
- **处置:** fixed | deferred | wontfix
- **验证:** 命令 + 结果
```

---

### Task 0: 建立基线（S0）

**Files:**
- Create: `docs/superpowers/reviews/2026-07-13-post-wiki-v2-code-audit-report.md`（草稿头）

- [ ] **Step 0.1: 确认工作树干净或记录已有改动**

```bash
git status
git log --oneline -15
```

Expected: 审计开始前无未说明的脏改动；若有，记入报告「工作树状态」。

- [ ] **Step 0.2: 跑全量 pytest 基线**

```bash
pytest tests/ -q --tb=no
```

Expected: 记录 `passed / skipped / xfailed / failed`。  
若有 **failed**：逐条进入修复队列（视为默认 P0/P1 候选），**先于**深度静态审计处理「门禁红灯」。

- [ ] **Step 0.3: 静态检查基线**

```bash
ruff check src tests
mypy src
```

Expected: 记录 exit code；新增错误记入 findings。

- [ ] **Step 0.4: 写报告草稿头**

写入报告：日期、版本、基线数字、commit SHA、`git rev-parse HEAD`。

---

### Task 1: S1 容器与配置审计

**Files:**
- Read: `src/core/container.py`, `src/utils/config.py`, `src/api/__init__.py`, `src/app.py`
- Test: `tests/test_core.py`, `tests/test_service_failure_config.py`, `tests/test_llm_configuration.py`

- [ ] **Step 1.1: 检查 create_container 依赖拓扑**

核对：`Config → Database → VectorStore → BlockStore → Embedding/LLM → Repos → Wiki/Search services`  
确认 lazy 属性不会在构造期互相递归创建。

- [ ] **Step 1.2: 检查 canonical_v2.mode 解析与默认值**

搜索 `canonical_v2` 在 config 中的读取路径；确认非法 mode 有明确失败或安全默认，而非静默错误主写。

- [ ] **Step 1.3: 跑相关测试**

```bash
pytest tests/test_core.py tests/test_service_failure_config.py tests/test_llm_configuration.py -q --tb=short
```

- [ ] **Step 1.4: 记录 findings F-S1-***

---

### Task 2: S2 数据层审计

**Files:**
- Read: `src/services/db.py`（软删/FTS 相关方法）, `src/services/vectorstore.py`, `src/services/block_store.py`
- Read: `src/repositories/knowledge_repo.py`, `wiki_repo.py`, `job_repo.py`
- Test: `tests/test_db.py`, `tests/test_block_store.py`, `tests/test_search_excludes_soft_deleted.py`, `tests/test_migration.py`

- [ ] **Step 2.1: 软删除一致性清单**

静态搜索：`deleted_at` / `is_deleted` / soft 在 search、block、vector、graph 路径是否一致过滤。

- [ ] **Step 2.2: 事务与 job 僵尸回收**

检查 `claim_next_pending_job`、async worker 启动回收（已知 BUG#8 类修复是否仍完整）。

- [ ] **Step 2.3: 跑相关测试**

```bash
pytest tests/test_db.py tests/test_block_store.py tests/test_search_excludes_soft_deleted.py tests/test_migration.py tests/test_indexed_file_repo.py -q --tb=short
```

- [ ] **Step 2.4: 记录 findings F-S2-***

---

### Task 3: S3 Canonical 主写与守卫（P0 段）

**Files:**
- Read: `src/services/wiki_repository.py`, `wiki_projection.py`, `wiki_primary_workflow.py`, `wiki_merge_engine.py`, `wiki_claim_matcher.py`
- Read: `src/api/routes/wiki.py`, `src/services/wiki_workflow.py`, `wiki_compiler.py`
- Test: `tests/test_canonical_write_guards.py`, `tests/test_wiki_primary_workflow.py`, `tests/test_wiki_repository.py`, `tests/test_wiki_projection.py`, `tests/test_wiki_claim_matcher.py`, `tests/test_wiki_v2_golden_eval.py`, `tests/test_wiki_v2_transaction_recovery.py`

- [ ] **Step 3.1: 守卫测试必须绿**

```bash
pytest tests/test_canonical_write_guards.py -q --tb=short
```

Expected: PASS；`ALLOWED_DIRECT_WRITES` 为空。

- [ ] **Step 3.2: 搜索潜在直接写回归**

```bash
# 在 src 中搜索 wiki 目录直接写文件、绕过 repository 的 update 路径
rg -n "write_text|open\(.*[\"']w|UPDATE knowledge_items|INSERT INTO wiki" src/services src/api --glob "*.py"
```

人工确认每个命中是否在 allowlist/投影/合法路径内。

- [ ] **Step 3.3: Matcher / Merge 契约抽检**

```bash
pytest tests/test_wiki_claim_matcher.py tests/test_wiki_merge_engine.py tests/test_wiki_v2_golden_eval.py tests/test_wiki_v2_transaction_recovery.py -q --tb=short
```

- [ ] **Step 3.4: Primary + API + projection 簇**

```bash
pytest tests/test_wiki_primary_workflow.py tests/test_wiki_repository.py tests/test_wiki_projection.py tests/test_wiki_api_canonical_routes.py tests/test_wiki_workflow_canonical.py tests/test_wiki_compiler_canonical.py tests/test_wiki_compiler_primary_adapter.py -q --tb=short
```

- [ ] **Step 3.5: 记录 findings F-S3-***

---

### Task 4: S4 依赖图与重建

**Files:**
- Read: `src/services/wiki_dependency_service.py`, `wiki_rebuild_service.py`, `wiki_rebuild_scheduler.py`
- Test: `tests/test_wiki_dependency_service.py`, `tests/test_wiki_rebuild_service.py`, `tests/test_wiki_rebuild_scheduler.py`, `tests/test_wiki_v2_phase5_e2e.py`

- [ ] **Step 4.1: 审查 plan_rebuild / apply 事务边界**

确认：staging 失败不污染正式 claim；cancel 可中断且不留 processing 死锁。

- [ ] **Step 4.2: 审查 debounce 与触发钩子**

确认 source 更新/删除 → scheduler → rebuild 链路无「只记日志不触发」的静默失败（除非 by design）。

- [ ] **Step 4.3: 跑 Phase 5 测试簇**

```bash
pytest tests/test_wiki_dependency_service.py tests/test_wiki_rebuild_service.py tests/test_wiki_rebuild_scheduler.py tests/test_wiki_v2_phase5_e2e.py -q --tb=short
```

- [ ] **Step 4.4: 记录 findings F-S4-***

---

### Task 5: S5 迁移 / 反馈 / 校验

**Files:**
- Read: `src/services/wiki_v2_migrator.py`, `wiki_feedback_service.py`, `wiki_validator.py`
- Test: `tests/test_wiki_v2_migrator.py`, `tests/test_wiki_v2_migration.py`, `tests/test_wiki_feedback_service.py`, `tests/test_wiki_validator.py`, `tests/test_wiki_validator_canonical.py`

- [ ] **Step 5.1: 铁律核对**

- migrate apply **不得**自动改 `canonical_v2.mode=primary`
- 迁移 claim **仅** draft/unsupported
- feedback **只**经 `WikiRepository.transaction`

- [ ] **Step 5.2: 跑 Phase 6 测试簇**

```bash
pytest tests/test_wiki_v2_migrator.py tests/test_wiki_v2_migration.py tests/test_wiki_feedback_service.py tests/test_wiki_validator.py tests/test_wiki_validator_canonical.py tests/test_cli_wiki.py -q --tb=short
```

- [ ] **Step 5.3: 记录 findings F-S5-***

---

### Task 6: S6 检索与 RAG

**Files:**
- Read: `src/services/search_service.py`, `hybrid_search.py`, `rag_pipeline.py`, `citation_builder.py`
- Test: `tests/test_search.py`, `tests/test_search_service.py`, `tests/test_rag_eval.py`, `tests/test_citation_builder.py`, `tests/test_mcp_rag_full.py`

- [ ] **Step 6.1: 软删与 citation 断链抽检**

- [ ] **Step 6.2: 跑检索簇**

```bash
pytest tests/test_search.py tests/test_search_service.py tests/test_search_excludes_soft_deleted.py tests/test_citation_builder.py tests/test_rag_messages.py tests/test_rag_sources.py tests/test_blend_fusion.py -q --tb=short
```

- [ ] **Step 6.3: 记录 findings F-S6-***

---

### Task 7: S7 MCP / API / CLI 契约

**Files:**
- Read: `src/mcp_server.py`（ask/search/index 与 timeout）, `src/mcp/tool_profiles.py`, `src/api/auth.py`
- Test: `tests/test_mcp_server.py`, `tests/test_mcp_contract.py`, `tests/test_mcp_tool_profiles.py`, `tests/test_mcp_stability.py`, `tests/test_api.py`, `tests/test_cli.py`

- [ ] **Step 7.1: ask 超时与线程边界**

对照 `docs/superpowers/plans/2026-07-13-mcp-ask-timeout-reliability.md` 与近期 fix commit `9baaa2e`，确认 worker 有界、取消可传播。

- [ ] **Step 7.2: profile 与快照一致性**

```bash
pytest tests/test_mcp_contract.py tests/test_mcp_tool_profiles.py tests/test_mcp_stability.py -q --tb=short
```

- [ ] **Step 7.3: API + CLI**

```bash
pytest tests/test_api.py tests/test_cli.py tests/test_cli_wiki.py tests/test_mcp_server.py -q --tb=short
```

- [ ] **Step 7.4: 记录 findings F-S7-***

---

### Task 8: S8 横切与安全

**Files:**
- Read: SSRF 相关（`parse_url` 所在模块）、`src/api/__init__.py` CORS/headers
- Test: 已有 security 相关测试；`tests/test_docs_consistency.py`, `tests/test_doctor.py`

- [ ] **Step 8.1: 危险模式扫描**

```bash
rg -n "except Exception:\s*\n\s*pass|shell=True|eval\(|pickle\.loads|allow_origins\s*=\s*\[\"\*\"\]" src --glob "*.py"
rg -n "TODO|FIXME|XXX|HACK" src --glob "*.py"
```

对高风险命中人工判定：真 bug vs 有意降级。

- [ ] **Step 8.2: 文档/版本一致性**

```bash
pytest tests/test_docs_consistency.py tests/test_doctor.py -q --tb=short
```

- [ ] **Step 8.3: 记录 findings F-S8-***

---

### Task 9: 修复批次（仅已确认缺陷）

对每个 **P0/P1**（及选定 P2）finding：

- [ ] **Step 9.x.1: 写失败测试或固定最小复现**

```bash
pytest tests/<focused>_test.py::test_<name> -v
```

Expected: FAIL（红）证明问题存在。若已有失败测试，跳过本步。

- [ ] **Step 9.x.2: 最小实现修复**

只改缺陷相关行；匹配现有风格；不顺手重构。

- [ ] **Step 9.x.3: 测试转绿 + 邻域回归**

```bash
pytest tests/<focused> -q --tb=short
# 邻域：同模块相关文件
```

- [ ] **Step 9.x.4: 更新 finding 状态为 fixed + 验证命令**

**修复原则（强制）：**

- Karpathy：Simplicity First + Surgical Changes
- 禁止为「审计完整性」而改无关代码
- 无法安全修复的记 `deferred` 并写清风险

---

### Task 10: 全量回归与报告收口

- [ ] **Step 10.1: 全量 pytest**

```bash
pytest tests/ -q --tb=line
```

Expected: failed 数 ≤ 基线；理想 = 0 unexpected fail。  
记录最终数字。

- [ ] **Step 10.2: ruff + mypy（若 Step 0 可用）**

```bash
ruff check src tests
mypy src
```

- [ ] **Step 10.3: 完成审计报告**

路径：`docs/superpowers/reviews/2026-07-13-post-wiki-v2-code-audit-report.md`

必须包含：

1. 基线 vs 最终门禁数字  
2. 各段结论表  
3. Findings 全表（含 deferred）  
4. 已修复 diff 概要  
5. Residual / 建议后续  

- [ ] **Step 10.4: （可选，经用户确认）更新 PROGRESS.md 审计条目**

- [ ] **Step 10.5: （仅当用户要求）git commit**

不要在用户未要求时自动 commit。

---

## 执行顺序与并行策略

```text
Task 0 (S0 基线)          ──必须先完成
    │
    ├─► 若存在 unexpected fail → 优先进入 Task 9 修门禁
    │
    ├─► Task 3 (S3) 与 Task 4 (S4) 与 Task 5 (S5) 可并行只读审计
    ├─► Task 1 + Task 2 可并行
    ├─► Task 6 + Task 7 可并行
    └─► Task 8
            │
            ▼
        Task 9 串行修复（按 P0→P1→P2）
            │
            ▼
        Task 10 全量回归 + 报告
```

推荐：**本会话 Inline Execution**（审计上下文连续）；若单段过重，可对「只读审计」派 explore 子代理，但**修复必须在主会话统一落地**以免冲突。

---

## Self-Review（计划自检）

| 检查项 | 结果 |
|--------|------|
| Charter 的 S0–S8 是否都有 Task？ | 是（Task 0–8 + 修复 9 + 收口 10） |
| 是否含占位符 TBD？ | 否；命令与路径具体 |
| 是否要求无关重构？ | 否 |
| 成功标准是否可验证？ | 是：pytest 数字 + 报告文件 |

---

## 开始执行前检查清单

1. 已阅读本 PLAN 与 Charter  
2. 工作目录为仓库根 `knowledge-base`  
3. Python 环境可运行 `pytest`  
4. 修复前每个 finding 满足「准入门槛」
