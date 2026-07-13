# ShineHeKnowledge Verified Hybrid 融合收束纠偏执行计划（Plan）

> **状态：执行中；Phase 0–8 已完成本地门禁，等待最终远端 CI 发布门禁。**
> **配套 Spec：** `docs/superpowers/specs/2026-07-13-verified-hybrid-convergence-correction-design.md`
> **执行方式：** TDD；按 Phase 顺序；每个 Task 独立验证、独立提交、可回滚。
> **目标版本：** v1.8.0
> **工作分支建议：** `codex/verified-hybrid-convergence-correction`

---

## 0. 执行契约

### 0.1 开始条件

只有用户明确回复同意执行后，才能开始 Task 0。

### 0.2 强制规则

1. 每个行为修复先写失败测试或固定可重复复现；
2. 只做当前 Task 的最小实现，不顺手重构；
3. 每个 Phase 结束后运行聚焦测试和规定门禁；
4. 任何失败不得通过删除测试、改宽断言或降低指标阈值解决；
5. 用户已有未跟踪文件全部保留，不暂存、不删除、不覆盖；
6. 不读出或打印 `config.yaml` 中的 Secret；
7. 数据迁移先 dry-run、备份和锁，再 apply；
8. 未经用户另行要求，不 push、不创建 PR、不发布 Release。

### 0.3 当前需要保护的未跟踪范围

执行开始时重新运行 `git status --short`，至少保护当前已知项：

```text
artifacts/eval/ask-e2e-llm-retest.json
artifacts/eval/ask-e2e-real-llm.json
artifacts/eval/hybrid-report.md
artifacts/eval/retrieval-real-embedding.json
artifacts/eval/wiki-v2-semantic-real-embedding.json
raw/
reports/mcp_30round_prod_test_live.json
schema/AGENTS.md.local-wikifirst
```

这些文件只能作为只读证据，不进入修复提交。

---

## Phase 0：冻结纠偏基线与更正文档状态

### Task 0.1：建立可复现基线

**Files:**

- Create: `docs/superpowers/reviews/verified-hybrid-correction-baseline.md`
- Modify: `PROGRESS.md`（仅在用户确认执行后，将“全部收口”改为“纠偏进行中”）

- [ ] 记录工作树、HEAD、分支与远端一致性：

```powershell
git status --short --branch
git rev-parse HEAD
git rev-parse origin/master
git log -12 --oneline --decorate
```

- [ ] 使用仓库内 pytest 临时目录，避免 Windows 全局 Temp 权限问题：

```powershell
New-Item -ItemType Directory -Force .codex-pytest-tmp | Out-Null
$env:TEMP=(Resolve-Path .codex-pytest-tmp).Path
$env:TMP=$env:TEMP
python -m pytest tests -q --basetemp .codex-pytest-tmp/baseline
```

- [ ] 执行精确 CI 命令并保留失败清单：

```powershell
python -m ruff check src tests evals tools scripts
python -m mypy src tools --ignore-missing-imports
Set-Location client
npm run build
Set-Location ..
python evals/run_hybrid_eval.py --strict --json
python evals/run_retrieval_eval.py --all --fake-embedding --baseline evals/baselines/local.json --max-regression 0.05 --report json
python evals/run_knowledge_evolution_eval.py --json
```

- [ ] 安全删除本轮创建的 pytest 临时目录：

```powershell
$target=(Resolve-Path .codex-pytest-tmp).Path
$root=(Resolve-Path .).Path
if (-not $target.StartsWith($root + [IO.Path]::DirectorySeparatorChar)) { throw "unsafe temp path" }
Remove-Item -LiteralPath $target -Recurse -Force
```

**Expected:**

- pytest 与前端通过；
- Ruff 8 errors、mypy 14 errors 作为纠偏基线重新确认或记录最新数字；
- Hybrid 175 cases PASS；
- baseline 报告明确当前实际配置未启用 Verified Ask；
- 不修改或纳入用户未跟踪文件。

### Task 0.2：增加“报告不得虚假完成”护栏

**Files:**

- Create: `tests/test_verified_hybrid_release_evidence.py`
- Modify: `docs/superpowers/reviews/verified-hybrid-final-review.md`
- Modify: `PROGRESS.md`

- [ ] 写测试检查最终评审若标记 complete，必须引用：Ruff=0、mypy=0、Python matrix、Docker、Windows、真实 Hybrid A/B 完整报告。
- [ ] 先运行并确认测试因旧最终评审过度声明而失败。
- [ ] 将旧最终评审顶部状态改为“Historical / Superseded by correction”，不删除历史数据。
- [ ] PROGRESS 标记纠偏进行中并链接新 Spec/Plan。

```powershell
python -m pytest tests/test_verified_hybrid_release_evidence.py -q
```

**Phase 0 Gate:**

```powershell
python -m pytest tests/test_verified_hybrid_release_evidence.py tests/test_docs_consistency.py -q
git diff --check
```

**建议提交：** `docs: open verified hybrid correction`

---

## Phase 1：配置契约、示例配置与旧用户有效迁移

### Task 1.1：中央有效配置解析器

**Files:**

- Create: `src/utils/knowledge_settings.py`
- Modify: `src/utils/knowledge_mode.py`
- Create: `tests/test_knowledge_settings.py`

- [ ] 为下列矩阵写参数化失败测试：未配置、verified、authoring、evidence_only、wiki_first、legacy、非法值。
- [ ] 覆盖“显式字段优先、缺失字段按模式推导、不写回磁盘、未知字段保留”。
- [ ] 覆盖当前旧配置形态：`wiki_first` + 缺少 `rag.verified_knowledge.enabled` 必须解析为 `verified_hybrid_enabled=True`。
- [ ] 实现冻结 dataclass 和单一 resolver。

```powershell
python -m pytest tests/test_knowledge_settings.py tests/test_knowledge_mode.py -q
```

### Task 1.2：所有消费者改读有效配置

**Files:**

- Modify: `src/services/search_service.py`
- Modify: `src/mcp_server.py`
- Modify: `src/services/wiki_serving_gate.py`
- Modify: `src/services/maintenance_policy.py`
- Modify: `src/mcp/tool_registry.py`
- Modify: `src/core/container.py`
- Modify: `src/cli.py`
- Modify: `src/services/project_setup.py`
- Test: `tests/test_verified_hybrid_search.py`
- Test: `tests/test_verified_answer.py`
- Test: `tests/test_mcp_write_policy_filter.py`
- Test: `tests/test_project_setup.py`

- [ ] 写集成测试证明旧 `wiki_first` 配置走 Verified Search/Ask，同时保留 Authoring 与显式 write policy。
- [ ] 写测试证明 `legacy` 只走 Raw。
- [ ] 写测试证明新 `--mode authoring --local` 默认 `local_confirm`，HTTP 写仍关闭。
- [ ] 替换散落的 `Config.get(..., False)` 模式判断。

```powershell
python -m pytest tests/test_knowledge_settings.py tests/test_project_setup.py tests/test_verified_hybrid_search.py tests/test_verified_answer.py tests/test_mcp_write_policy_filter.py -q
```

### Task 1.3：修复 `config.example.yaml` 结构

**Files:**

- Modify: `config.example.yaml`
- Modify: `tests/test_project_setup.py`
- Modify: `tests/test_docs_consistency.py`

- [ ] 先加深层 schema 断言，确认当前示例失败：
  - `wiki.canonical_v2` 存在；
  - `wiki.claims/rebuild/projection/validation/site` 存在；
  - `maintenance` 不含上述键；
  - `wiki.serving` 含完整门禁字段；
  - `wiki.canonical_v2.mode` 是字符串 `off`，不是 bool；
  - example 与 `ProjectSetupService` 默认有效语义一致。
- [ ] 修正 YAML 层级和引号。

```powershell
python -m pytest tests/test_project_setup.py::TestConfigExampleConvergence tests/test_docs_consistency.py -q
```

### Task 1.4：Doctor 与配置迁移预览

**Files:**

- Modify: `src/services/doctor.py` 或现有 Doctor 实现文件
- Modify: `src/cli.py`
- Test: `tests/test_doctor.py`
- Test: `tests/test_cli.py`

- [ ] 增加 `doctor --explain-config` 输出 raw/resolved/source/warnings，但不输出 secrets。
- [ ] 增加 `config migrate-verified-hybrid --dry-run`，本 Phase 只实现预览；apply 在 Phase 8 完成。
- [ ] 验证 dry-run 零写入。

**Phase 1 Gate:**

```powershell
python -m pytest tests/test_knowledge_settings.py tests/test_knowledge_mode.py tests/test_project_setup.py tests/test_docs_consistency.py tests/test_doctor.py tests/test_cli.py tests/test_verified_hybrid_search.py tests/test_verified_answer.py tests/test_mcp_write_policy_filter.py -q
python -m ruff check src/utils/knowledge_settings.py src/utils/knowledge_mode.py src/services/project_setup.py tests/test_knowledge_settings.py tests/test_project_setup.py tests/test_docs_consistency.py
```

**建议提交：** `fix(config): converge effective knowledge settings`

---

## Phase 2：严格 Validation / Review / Published Revision Serving Gate

### Task 2.1：扩展 Claim Serving Validation 模型

**Files:**

- Modify: `src/models/wiki_v2.py`
- Modify: `src/services/wiki_repository.py`
- Test: `tests/test_wiki_v2_models.py`
- Test: `tests/test_wiki_repository.py`

- [ ] 写旧 Claim 无新字段仍能读取的兼容测试。
- [ ] 写新 record round-trip 测试。
- [ ] 写 strict 模式未知字段仍拒绝、已知 optional 字段接受的测试。
- [ ] 实现 `ClaimServingValidation` 与序列化。

```powershell
python -m pytest tests/test_wiki_v2_models.py tests/test_wiki_repository.py -q
```

### Task 2.2：Gate fail-closed 与 reason codes

**Files:**

- Modify: `src/services/wiki_serving_gate.py`
- Modify: `src/services/search_service.py`
- Modify: `src/services/verified_hybrid_fusion.py`
- Test: `tests/test_wiki_serving_gate.py`
- Test: `tests/test_verified_hybrid_search.py`
- Test: `tests/test_verified_answer.py`

- [ ] 增加失败测试：
  - 无 validation record；
  - validation failed；
  - review 未批准；
  - validated revision 过期；
  - published revision 过期；
  - serving evidence 集为空；
  - serving evidence 任一缺 block/hash mismatch/stale；
  - Gate 失败仍 Raw fallback。
- [ ] 增加稳定 reason codes 与 Doctor 统计。
- [ ] 实现严格门禁，不调用 LLM。

```powershell
python -m pytest tests/test_wiki_serving_gate.py tests/test_verified_hybrid_search.py tests/test_verified_answer.py -q
```

### Task 2.3：Validator 与 Publish Record

**Files:**

- Modify: `src/services/wiki_validator.py`
- Modify: `src/services/wiki_feedback_service.py`
- Modify: `src/services/wiki_primary_workflow.py`
- Modify: `src/services/wiki_write_service.py`
- Test: `tests/test_wiki_validator.py`
- Test: `tests/test_wiki_validator_canonical.py`
- Test: `tests/test_wiki_feedback_service.py`
- Test: `tests/test_wiki_primary_workflow.py`

- [ ] Validator 输出 serving evidence IDs、validator version 和 validated revision。
- [ ] Review approval只记录 approved，不自动 publish。
- [ ] Publish 必须校验 current validation + parity，成功后写 published revision。
- [ ] Claim revision 变化自动使旧 record 不合格。

### Task 2.4：旧 Claim validation dry-run

**Files:**

- Create: `src/services/wiki_serving_validation_migrator.py`
- Modify: `src/cli.py`
- Create: `tests/test_wiki_serving_validation_migrator.py`

- [ ] 实现 dry-run 统计，不写磁盘。
- [ ] 实现 apply 所需接口但保持 CLI apply 受 Phase 8 migration gate 控制。
- [ ] 无法证明 review/publish 的 Active Claim 不生成假 record，进入 Review proposal。

**Phase 2 Gate:**

```powershell
python -m pytest tests/test_wiki_v2_models.py tests/test_wiki_repository.py tests/test_wiki_serving_gate.py tests/test_verified_hybrid_search.py tests/test_verified_answer.py tests/test_wiki_validator.py tests/test_wiki_validator_canonical.py tests/test_wiki_feedback_service.py tests/test_wiki_primary_workflow.py tests/test_wiki_serving_validation_migrator.py -q
python evals/run_hybrid_eval.py --strict --json
```

**建议提交：** `fix(wiki): enforce reviewed validated serving revisions`

---

## Phase 3：持久化 Maintenance Store

### Task 3.1：数据库迁移与 Repository

**Files:**

- Create via Alembic: maintenance control-plane migration under `alembic/versions/`
- Create: `src/repositories/maintenance_repo.py`
- Create: `tests/test_maintenance_repo.py`
- Modify: `src/services/db.py`（仅必要 schema/bootstrap 兼容）

- [ ] 为 source events、jobs、reviews、dead letters、health snapshots、schedules 建表。
- [ ] 添加唯一 idempotency index、status/due/lease/review 查询索引。
- [ ] Alembic upgrade/downgrade 测试；downgrade 不触碰 Canonical 表。
- [ ] Repository CRUD、事务、并发 claim lease 测试。

```powershell
alembic revision -m "add maintenance control plane"
python -m pytest tests/test_maintenance_repo.py tests/test_migration.py -q
```

### Task 3.2：替换进程内 Job/Review 存储

**Files:**

- Modify: `src/services/wiki_maintenance_service.py`
- Modify: `src/core/container.py`
- Modify: `src/api/routes/maintenance.py`
- Modify: `src/cli.py`
- Test: `tests/test_maintenance_center.py`
- Test: `tests/test_maintenance_api.py`

- [ ] 先写“创建服务实例后重开仍能读 Job/Review/Dead Letter”的失败测试。
- [ ] 移除 `_jobs/_reviews/_jobs_by_idempotency/_dead_letter` 作为事实存储。
- [ ] Operation Log 仍是审计，不充当 Job Store。
- [ ] max attempts/backoff/retention 从配置读取。

### Task 3.3：幂等键修复

**Files:**

- Modify: `src/services/wiki_maintenance_service.py`
- Test: `tests/test_maintenance_center.py`
- Test: `tests/test_maintenance_repo.py`

- [ ] 测试相同 revision 重复事件只生成一个 Job。
- [ ] 测试同一 knowledge_id 的新 revision 生成新 Job。
- [ ] 测试 delete tombstone 幂等。
- [ ] 数据库 unique constraint 承担最终去重。

**Phase 3 Gate:**

```powershell
python -m pytest tests/test_maintenance_repo.py tests/test_maintenance_center.py tests/test_maintenance_api.py tests/test_migration.py -q
python -m ruff check src/repositories/maintenance_repo.py src/services/wiki_maintenance_service.py tests/test_maintenance_repo.py tests/test_maintenance_center.py
python -m mypy src/repositories/maintenance_repo.py src/services/wiki_maintenance_service.py --ignore-missing-imports
```

**建议提交：** `feat(maintenance): persist jobs reviews and health`

---

## Phase 4：统一 Source Event、Worker、Lease 与周期调度

### Task 4.1：Source Event Adapter 接主链路

**Files:**

- Create: `src/services/maintenance_event_adapter.py`
- Modify: `src/services/path_indexer.py`
- Modify: `src/services/knowledge_workflow.py`
- Modify: `src/services/index_scheduler.py`
- Modify: `src/services/file_watcher.py`
- Modify: `src/core/container.py`
- Test: `tests/test_maintenance_event_adapter.py`
- Test: `tests/test_file_watcher.py`
- Test: `tests/test_knowledge_workflow.py`

- [ ] 写 E2E 失败测试：索引 update/delete 后自动产生 durable maintenance job。
- [ ] 证明 Raw index 先成功，maintenance enqueue 失败只 warning。
- [ ] 证明没有双投递到旧 scheduler。
- [ ] 将 `wiki_rebuild_scheduler` 改成兼容 adapter 或迁移完调用后删除。

### Task 4.2：Durable Worker、Lease、Retry、Cancel

**Files:**

- Create: `src/services/maintenance_worker.py`
- Modify: `src/services/wiki_maintenance_service.py`
- Modify: `src/services/wiki_rebuild_service.py`
- Modify: `src/core/container.py`
- Create: `tests/test_maintenance_worker.py`

- [ ] 测试 pending → leased → running → completed。
- [ ] 测试 lease expiry 后另一个 worker 可恢复。
- [ ] 测试 retry_wait 与配置 backoff。
- [ ] 测试 cancel 把协作取消句柄传入 rebuild。
- [ ] 测试 max attempts 后 dead letter。
- [ ] 测试 worker 重启后继续 pending/retry_wait。

### Task 4.3：保护性失败 fail-closed

**Files:**

- Modify: `src/services/wiki_maintenance_service.py`
- Modify: `src/services/wiki_serving_gate.py`
- Test: `tests/test_maintenance_protective_e2e.py`

- [ ] 注入 rebuild/DB/projection 故障。
- [ ] 证明 Source hash 已变时 Gate 实时排除 Claim，即使维护任务失败。
- [ ] 证明 Raw Search 仍返回结果。
- [ ] 生成 P0 告警、dead letter、correlation 和 rollback reference。

### Task 4.4：周期 Scheduler

**Files:**

- Create: `src/services/maintenance_scheduler.py`
- Modify: `src/core/container.py`
- Modify: `src/app.py` 或统一 lifespan 入口
- Create: `tests/test_maintenance_scheduler.py`

- [ ] 实现 validation/projection/weekly/monthly schedule。
- [ ] 使用数据库 lease，测试双 scheduler 只有一个执行者。
- [ ] 测试关闭 scheduler 不影响 query。
- [ ] 测试 cron 配置解析失败时 Doctor 报错而非静默。

**Phase 4 Gate:**

```powershell
python -m pytest tests/test_maintenance_event_adapter.py tests/test_maintenance_worker.py tests/test_maintenance_scheduler.py tests/test_maintenance_protective_e2e.py tests/test_file_watcher.py tests/test_knowledge_workflow.py tests/test_wiki_rebuild_service.py tests/test_wiki_rebuild_scheduler.py tests/test_wiki_v2_phase5_e2e.py -q
```

**建议提交：** `feat(maintenance): unify source event automation`

---

## Phase 5：Review Gate、API/CLI 与可操作 Web UI

### Task 5.1：Review Service 规则补全

**Files:**

- Create: `src/services/maintenance_review_service.py`
- Modify: `src/services/wiki_maintenance_service.py`
- Modify: `src/services/maintenance_policy.py`
- Test: `tests/test_maintenance_review_service.py`

- [ ] 测试 Reject 无 Note 被拒绝。
- [ ] 测试 conflict resolution 无 Note 被拒绝。
- [ ] 测试 Correct 只生成 Draft。
- [ ] 测试重复 Approve 幂等。
- [ ] 测试 R4 未提供 confirmation token 被拒绝。
- [ ] 删除 `human_confirmed or True` 等绕过逻辑。
- [ ] 测试 Validator/Parity 任一失败阻止 Publish。
- [ ] 测试高风险批量操作与 max bulk 上限。

### Task 5.2：共享 API 与 CLI

**Files:**

- Modify: `src/api/routes/maintenance.py`
- Modify: `src/cli.py`
- Test: `tests/test_maintenance_api.py`
- Create or Modify: `tests/test_cli_maintenance.py`

- [ ] API 增加分页 Jobs/Dead Letters/Health History/Review Detail/Impact Dry Run。
- [ ] Review write endpoint 必须复用 Review Service。
- [ ] CLI 写操作默认 dry-run，apply 明确确认。
- [ ] HTTP 写遵守 `allow_http_write=false` 与认证策略。
- [ ] 错误 envelope 返回稳定 code。

### Task 5.3：Web UI 完整工作流

**Files:**

- Modify: `client/src/views/MaintenanceView.tsx`
- Modify: `client/src/api.ts` 或现有 API client
- Create as needed: `client/src/components/maintenance/*`
- Create or Modify: 前端测试文件（按当前测试框架）

- [ ] 增加 Overview / Jobs / Reviews / Health 页签或等价结构。
- [ ] Review 详情显示 Before、Proposed、Evidence Diff、reason codes。
- [ ] 支持 Approve、Reject、Correct、Defer；危险动作二次确认。
- [ ] Jobs 支持筛选、retry、cancel、dead letter 详情。
- [ ] Health 显示历史趋势与 P0/P1 告警。
- [ ] API 错误显示可操作原因，不吞异常。

```powershell
Set-Location client
npm run build
Set-Location ..
```

### Task 5.4：Maintenance E2E 场景 H

**Files:**

- Create: `tests/test_maintenance_center_e2e.py`

- [ ] 建立 Active + validated + published Claim 和 Raw Block fixture。
- [ ] 修改 Raw Source，等待 event/debounce/worker。
- [ ] 断言 Evidence stale、Claim unsupported 或非 Serving、Page review。
- [ ] 断言 Review 包含 Before/Proposed/Evidence Diff。
- [ ] 断言 Approve → Validator → Parity → 显式 Publish。
- [ ] 任一步失败时断言 Raw Search 成功。

**Phase 5 Gate:**

```powershell
python -m pytest tests/test_maintenance_review_service.py tests/test_maintenance_center.py tests/test_maintenance_api.py tests/test_cli_maintenance.py tests/test_maintenance_center_e2e.py -q
Set-Location client
npm run build
Set-Location ..
```

**建议提交：** `feat(maintenance-ui): complete review control plane`

---

## Phase 6：真实 Raw / Hybrid A/B 质量证明

### Task 6.1：建立真实 Hybrid Release 数据集

**Files:**

- Create: `evals/datasets/verified_hybrid_release.yaml`
- Create: `evals/verified_hybrid_release/__init__.py`
- Create: `evals/verified_hybrid_release/scoring.py`
- Create: `tests/test_verified_hybrid_release_dataset.py`

- [ ] 数据集不少于 60 题并满足 Spec §9.2 分类。
- [ ] 每题固定 Raw Evidence、Canonical Claim、期望答案、引用与 preferred mode。
- [ ] 通信领域不少于 30 题。
- [ ] 测试禁止重复 ID、缺 Evidence、空 expected、类别不足。

### Task 6.2：同源 Raw/Hybrid A/B Runner

**Files:**

- Create: `evals/run_verified_hybrid_release_eval.py`
- Create: `tests/test_verified_hybrid_release_eval.py`

- [ ] 同一 fixture DB、同一问题分别强制 Raw Only 和 Hybrid Verified。
- [ ] 走真实 `SearchService` + `VerifiedAnswerService`，不直接调用答案装配函数伪造命中。
- [ ] 报告 answer_mode、verified_claim_count、citation、reason codes、latency 和 failure category。
- [ ] 全部 `raw_only` 必须 fail。
- [ ] 小于 60 题必须 fail。
- [ ] 最新完整运行失败时不能引用旧 PASS。

### Task 6.3：CI 契约评测与 Release 真实评测分层

**Files:**

- Modify: `.github/workflows/ci.yml`
- Modify: `docs/evaluation/hybrid-knowledge.md`
- Create: `docs/superpowers/reviews/verified-hybrid-correction-release-eval.md`（运行后生成）

- [ ] PR/CI 保留 deterministic 175+。
- [ ] Release workflow 使用真实 embedding/LLM secret 运行 A/B，或在受控 release 环境手工运行并签入摘要。
- [ ] 报告明确 provider/model/dataset SHA/commit SHA/时间/完整样本数。

**Phase 6 Gate:**

```powershell
python -m pytest tests/test_hybrid_eval.py tests/test_verified_hybrid_release_dataset.py tests/test_verified_hybrid_release_eval.py -q
python evals/run_hybrid_eval.py --strict --json
python evals/run_verified_hybrid_release_eval.py --strict --json --output artifacts/eval/verified-hybrid-release.json
```

**Expected:** 满足 Spec §9.3；否则停止，不进入 Phase 7。

**建议提交：** `test(eval): add real verified hybrid ablation`

---

## Phase 7：清零工程债并建立真实发布门禁

### Task 7.1：Ruff 清零

**Files:**

- Modify only files reported by current Ruff
- Test: exact CI command

- [x] 修复未使用 import 与 import 排序，不做无关格式化。

```powershell
python -m ruff check src tests evals tools scripts
```

**Expected:** 0 errors。

### Task 7.2：mypy 清零

**Files:**

- Modify current error files, expected at least:
  - `src/services/file_graph.py`
  - `src/services/wiki_repository.py`
  - `src/services/verified_hybrid_fusion.py`
  - `src/services/search_service.py`
  - `src/services/verified_answer.py`

- [x] 用显式类型、局部变量改名、类型收窄修复，不用大范围 `# type: ignore`。

```powershell
python -m mypy src tools --ignore-missing-imports
```

**Expected:** 0 errors。

### Task 7.3：Python 3.10/3.11/3.12 Matrix

**Files:**

- Modify: `.github/workflows/ci.yml`
- Modify: `pyproject.toml`（仅在真实兼容性需要时）

- [x] CI matrix 覆盖 3.10、3.11、3.12。
- [x] 每个版本安装相同 extras 并运行全量 tests（待最终 push 取得远端运行证据）。
- [x] 不把 3.10/3.11 标为 allow-failure。

### Task 7.4：Docker Gate

**Files:**

- Modify: `Dockerfile`、CI 或依赖文件（仅构建失败时）

```powershell
docker build --target api .
docker build --target mcp .
```

- [x] API 和 MCP target 均由 CI 构建（本机 Docker 未安装，待最终 push 取得远端运行证据）。
- [x] API 容器 smoke 调用 health（同上）。

### Task 7.5：Windows Runtime Smoke

**Files:**

- Create: `scripts/windows-smoke.ps1`
- Modify: `.github/workflows/ci.yml` 或新增 Windows workflow
- Create: `tests/test_windows_smoke_contract.py`

- [x] 使用 Windows temp workspace，不碰仓库 `config.yaml/data/raw/wiki`。
- [x] 验证 CLI init/index 与 MCP initialize、capabilities、search、ask、read、ping。
- [ ] 模拟 Wiki 目录不可读/损坏，Raw 仍成功（转入 Phase 8 最终验收场景 C）。
- [x] 检查启动/停止脚本退出码和残留进程。

**Phase 7 Gate:**

```powershell
python -m ruff check src tests evals tools scripts
python -m mypy src tools --ignore-missing-imports
python -m pytest tests -q
Set-Location client
npm ci
npm run build
Set-Location ..
```

远端必须同时看到 Python matrix、Docker、Windows smoke 全绿。

**建议提交：** `ci: enforce convergence release gates`

---

## Phase 8：迁移、文档、最终 E2E 与发布评审

### Task 8.1：配置迁移 apply

**Files:**

- Modify: `src/cli.py`
- Create or Modify: 配置迁移 service
- Test: `tests/test_config_verified_hybrid_migration.py`

- [ ] dry-run 零写。
- [ ] apply 自动备份、原子写、保留未知字段。
- [ ] 旧 `wiki_first` 可选择只规范名称或完整迁移到 verified/authoring，不擅自改变用户选择。
- [ ] rollback 恢复字节级原配置。

### Task 8.2：Claim Validation 与 Maintenance Schema 迁移验证

**Files:**

- Modify: `src/services/wiki_serving_validation_migrator.py`
- Test: `tests/test_wiki_serving_validation_migrator.py`
- Test: `tests/test_migration.py`

- [ ] 在副本数据上执行 dry-run/apply/rollback。
- [ ] 不可证明的 Claim 保持非 Serving并进入 Review。
- [ ] 迁移失败不破坏 Raw 或 Canonical 原文件。

### Task 8.3：文档同步

**Files:**

- Modify: `README.md`
- Modify: `README_zh.md`
- Modify: `PROGRESS.md`
- Modify: `docs/migration/v1.6-to-v1.7-verified-hybrid.md`（标为历史）
- Create: `docs/migration/v1.7-to-v1.8-convergence-correction.md`
- Modify: `docs/maintenance/*`
- Modify: `docs/wiki/serving-gate.md`
- Modify: `docs/evaluation/hybrid-knowledge.md`
- Create: `docs/release/v1.8.0-release-notes.md`
- Create: `docs/superpowers/reviews/verified-hybrid-correction-final-review.md`

- [ ] 文档只写已实际验证的结果。
- [ ] 明确 deterministic 与真实模型结果。
- [ ] 明确当前/历史报告状态。
- [ ] 明确前端版本策略。

### Task 8.4：最终验收场景 A–H

**Files:**

- Create: `scripts/verified-hybrid-acceptance.ps1`
- Create: `docs/superpowers/reviews/verified-hybrid-correction-acceptance.md`

- [ ] A：新用户无 Wiki，默认 Verified，Raw 成功，无写工具。
- [ ] B：有 validated/published Claim，Hybrid 实际出现 verified_claim。
- [ ] C：Wiki 损坏，Raw fallback。
- [ ] D：Authoring local，写需 local_confirm，auto publish=false。
- [ ] E：冲突双方披露并可读 Evidence。
- [ ] F：Source 更新后 Claim 立即非 Serving并产生 durable Job/Review。
- [ ] G：旧 wiki_first 运行时 Authoring + Verified Hybrid。
- [ ] H：完整 Maintenance 自动闭环、重启恢复、Review、Validator、Parity、Publish。

### Task 8.5：最终 Release Gate

```powershell
python -m ruff check src tests evals tools scripts
python -m mypy src tools --ignore-missing-imports
python -m pytest tests -q
python evals/run_retrieval_eval.py --all --fake-embedding --baseline evals/baselines/local.json --max-regression 0.05 --report json
python evals/run_hybrid_eval.py --strict --json
python evals/run_knowledge_evolution_eval.py --json
python evals/run_verified_hybrid_release_eval.py --strict --json --output artifacts/eval/verified-hybrid-release.json
powershell -ExecutionPolicy Bypass -File scripts/verified-hybrid-acceptance.ps1
Set-Location client
npm ci
npm run build
Set-Location ..
docker build --target api .
docker build --target mcp .
```

远端验证：

- Python 3.10/3.11/3.12 全绿；
- Ruff/mypy 全绿；
- Frontend/Docker/Windows smoke 全绿；
- 最新完整真实 Hybrid A/B PASS。

### Task 8.6：最终工作树与发布决策

```powershell
git status --short --branch
git diff --check
git log --oneline --decorate -15
```

- [ ] 用户未跟踪文件仍未暂存。
- [ ] 每个 Phase 有报告、测试结果、commit SHA、回滚方式。
- [ ] Final Review 逐条回答原 Spec 的 12 个最终问题。
- [ ] 只有全部 Gate 通过才建议发布 v1.8.0。
- [ ] Commit/push/release 仍需用户单独授权。

**建议提交：** `docs: close verified hybrid convergence correction`

---

## 依赖顺序

```text
Phase 0 基线与状态纠偏
    ↓
Phase 1 有效配置
    ↓
Phase 2 严格 Serving Gate
    ↓
Phase 3 Durable Maintenance Store
    ↓
Phase 4 Event / Worker / Scheduler
    ↓
Phase 5 Review Service / UI
    ↓
Phase 6 真实 Hybrid A/B
    ↓
Phase 7 工程发布门禁
    ↓
Phase 8 Migration / E2E / Final Review
```

禁止跳过 Phase 1–2 直接扩大 Maintenance 自动化；禁止在真实 A/B 未通过时进入“完成/发布”文档收口。

---

## 阶段报告模板

每个 Phase 创建：

```text
docs/superpowers/reviews/verified-hybrid-correction-phase<N>-report.md
```

必须包含：

1. 修改文件；
2. 行为变化；
3. 兼容性；
4. 数据迁移；
5. 测试命令和真实结果；
6. Raw/Wiki/Hybrid 指标；
7. 未解决风险；
8. 回滚方式；
9. commit SHA；
10. 是否允许进入下一 Phase。

---

## 最终完成判定

只有以下陈述均为真，计划才可标记完成：

- 当前实际配置和新配置都进入预期有效模式；
- Active Claim 不能绕过 Validation/Review/Published Revision；
- Source 事件进入唯一、持久、可恢复 Maintenance Control Plane；
- Review UI 能完成真实审阅和发布门禁；
- 最新真实 Hybrid A/B 证明总体不劣于 Raw，Claim-benefit 子集有可重复增益；
- Ruff、mypy、Python matrix、pytest、Frontend、Docker、Windows 全绿；
- 最终报告不引用旧 PASS 覆盖新 FAIL；
- 用户未跟踪文件和本地数据未受影响。

确认门：**等待世恒哥确认本 Spec 与 PLAN 后，再从 Phase 0 开始执行。**
