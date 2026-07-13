# ShineHeKnowledge Verified Hybrid 融合收束纠偏规格说明（Spec）

> 日期：2026-07-13
> 状态：**执行中；Phase 0–1 已完成**
> 适用仓库：`anjingdtl/knowledge-base`
> 当前基线：`master` / `21737ff` / `v1.7.0`
> 建议目标版本：`v1.8.0`
> 原始依据：`docs/ShineHeKnowledge 融合收束开发规格说明.md`
> 配套执行计划：`docs/superpowers/plans/2026-07-13-verified-hybrid-convergence-correction.md`

---

## 0. 执行授权边界

本 Spec 只定义修复，不授权立即修改生产代码。

在用户明确确认前：

- 只允许新增或修订本 Spec 与配套 PLAN；
- 不修改 `src/`、`tests/`、`client/`、配置、数据库或运行数据；
- 不提交、不推送、不创建 Release；
- 不清理现有未跟踪评测产物、`raw/` 或本地 schema 文件。

用户确认后，严格按 PLAN 的阶段顺序执行。任何扩大范围、改变数据语义或降低验收阈值的行为必须再次请求确认。

---

## 1. 问题定义与验收基线

### 1.1 当前结论

v1.7.0 已完成以下核心能力：

- Raw Retrieval 与 Gate 通过的 Wiki Claim 可在 `SearchService` 中融合；
- `search` / `ask` 可返回 Claim + Evidence 引用；
- Wiki 空、异常或 Claim 不可 Serving 时可回退 Raw；
- Verified 默认隐藏 Agent 写工具；
- 离线 175 例 Hybrid 契约评测通过；
- 全量 pytest 与前端构建可通过。

但原 Spec 的完整收束目标尚未完成，主要缺口为：

1. 当前实际 `config.yaml` 仍是旧 `wiki_first`，有效运行时未启用 Verified Hybrid；
2. `config.example.yaml` 将多组 `wiki.*` 配置错误嵌套到 `maintenance.*`；
3. Serving Gate 没有持久化的 Validation / Review / Published Revision 证明；
4. Maintenance Job / Review / Dead Letter 为进程内存储；
5. Maintenance Center 未统一接入 Source 更新/删除事件，旧 rebuild scheduler 仍是并行入口且默认关闭；
6. Review UI 只能查看摘要，不能完成 Evidence 对照、批准、拒绝、修正和发布门禁；
7. 175 例评测证明了契约安全，但没有证明 Wiki 对真实最终答案的增益；
8. Ruff、mypy、Python 版本矩阵、Docker 与 Windows 入口未达到原 DoD。

### 1.2 2026-07-13 实测快照

| 项目 | 当前结果 | 判定 |
|---|---:|---|
| `pytest tests -q` | 1646 passed / 2 skipped | 通过 |
| 聚焦收束测试 | 110 passed | 通过 |
| 前端 `npm run build` | PASS | 通过 |
| Hybrid deterministic eval | 175 cases / PASS | 契约通过 |
| Raw fake-embedding eval | PASS | 回归通过 |
| Knowledge evolution eval | PASS；projection parity 为 skipped-as-pass | 部分有效 |
| Wiki compilation eval | orphan page rate = 0.8462 | 质量债 |
| Ruff（CI 精确命令） | 8 errors | 失败 |
| mypy（CI 精确命令） | 14 errors / 5 files | 失败 |
| 真实 LLM 首轮 | 13/15 PASS，但 15/15 均为 `raw_only` | 未证明 Wiki 增益 |
| 真实 LLM 后续复测 | 5/8，overall FAIL | 不稳定 |
| 当前有效 Verified Ask | `False` | 未切到目标运行态 |
| Docker 本机验证 | Docker 不可用 | 未验证 |
| Python 3.10/3.11/3.12 | CI 仅 3.12 | 未满足矩阵 |

上述快照是纠偏前基线。修复不得通过删除困难样本、降低阈值或把 skipped 计为成功来制造改善。

---

## 2. 纠偏目标

本轮必须把“代码具备部分能力”收口为“默认配置、现有配置、查询、维护、评测和发布证据一致”。

最终目标：

> Raw Evidence 始终可用；Verified Canonical Knowledge 在通过证据、校验和审阅门禁后默认增强回答；Source 变化自动进入唯一、持久、可重试、可审阅的 Maintenance Control Plane；真实 A/B 评测证明 Hybrid 不劣于 Raw，并在 Claim 适用问题上产生可重复增益。

具体目标：

1. 新配置、示例配置、旧 `wiki_first` 配置拥有一致的有效语义；
2. 普通查询不要求用户手工理解或切换 Raw/Wiki 两套工具；
3. 任何主结论都具备可验证的原始 Block 证据和审阅/校验记录；
4. Source 变化可自动、幂等地触发持久维护任务；
5. R1 自动保护，R3 只生成 Draft/Review，R4 必须显式人工确认；
6. Review Queue 可在 UI/API/CLI 中完成真实工作，而非只显示计数；
7. CI、Docker、Windows 入口与版本矩阵形成发布门禁；
8. 最终评审只引用本轮真实执行结果，不沿用旧报告结论。

---

## 3. 非目标

本轮不做：

- 重写整个 `src/services`；
- 删除 Raw Retrieval、Canonical Wiki V2、Projection、Outbox 或既有迁移能力；
- 新增外部向量库、图数据库、消息队列或 SaaS；
- 多租户、RBAC、协作编辑；
- 自动修改原始文档；
- 自动发布语义变化；
- 为提高分数而缩减评测集、删除失败样本或放宽质量门槛；
- 清理与本轮无关的历史代码、未跟踪文件或本地数据；
- 在修复提交中混入 UI 全面改版、命名重构或大范围格式化。

---

## 4. 不可破坏的架构铁律

1. Raw Document / Knowledge Item / Block 是最终证据源。
2. Maintenance Store 只能保存任务、审阅、租约、快照和审计引用，不保存第二份 Claim/Page 真相。
3. Canonical 写入必须经过 `WikiRepository.transaction()`、Outbox、Projection 和 Operation Log。
4. Serving Gate 必须是 `search` / `ask` 获取 Claim 的唯一入口。
5. Gate、Validator、Maintenance 任一失败都不得阻断 Raw Retrieval。
6. R1 只能缩小 Serving 面，不能修改 statement、提升 Claim 状态或发布页面。
7. R3 只能产生 Draft、差异或 Review Item。
8. R4 的 merge、conflict resolution、publish、retract、delete、primary migration 必须由真实人工确认触发。
9. HTTP 写默认关闭；MCP write policy 与内部保护性维护保持分离。
10. 所有 schema 变化必须是非破坏性、可回滚迁移。

---

## 5. 配置与有效运行时语义

### 5.1 单一有效配置解析器

新增或收敛为一个 `EffectiveKnowledgeSettings` 解析入口。所有 Search、Ask、MCP、Doctor、ProjectSetup、Maintenance 和 UI 状态必须读取同一有效语义，不得各自用不同默认值。

建议位置：

```text
src/utils/knowledge_settings.py
```

建议输出：

```python
@dataclass(frozen=True)
class EffectiveKnowledgeSettings:
    mode: str
    wiki_read_enabled: bool
    authoring_enabled: bool
    verified_hybrid_enabled: bool
    maintenance_enabled: bool
    automation_level: str
    mcp_tool_profile: str
    mcp_write_policy: str
    allow_http_write: bool
    canonical_write_mode: str
    compatibility_warnings: tuple[str, ...]
```

### 5.2 兼容解析规则

显式新字段优先；字段缺失时按模式推导，但不改写磁盘：

| 输入 | 有效模式 | Wiki Read | Authoring | Verified Hybrid | 默认写策略 |
|---|---|---:|---:|---:|---|
| 未配置 mode | `verified` | true | false | true | disabled |
| `verified` | `verified` | true | false | true | disabled |
| `authoring` | `authoring` | true | true | true | local_confirm |
| `evidence_only` | `evidence_only` | false | false | false | disabled |
| `wiki_first` | `authoring` | true | true | true | 保留显式旧值，否则 local_confirm |
| `legacy` | `evidence_only` | false | false | false | disabled |

要求：

- 旧 `wiki_first` 不再因缺少 `rag.verified_knowledge.enabled` 而静默走旧 Answer 路径；
- 旧配置的显式 `write_policy`、`canonical_v2.mode` 和用户自定义字段必须保留；
- 兼容解析只影响运行时，不自动写回 `config.yaml`；
- Doctor 必须显示 raw value、resolved value、来源和迁移建议；
- 非法 mode 启动失败或 Doctor 返回明确 error，不能默认为 verified。

### 5.3 `config.example.yaml` 正确结构

`canonical_v2`、`claims`、`rebuild`、`projection`、`validation`、`site` 必须位于 `wiki` 下；Serving 字段必须完整位于 `wiki.serving`。

YAML 枚举值 `off` 必须加引号，避免 PyYAML 1.1 将其解析为布尔值。

```yaml
knowledge_workflow:
  mode: verified

wiki:
  enabled: true
  read_enabled: true
  authoring_enabled: false
  auto_compile: false
  auto_publish: false
  serving:
    enabled: true
    allowed_claim_statuses: [active]
    require_block_evidence: true
    require_validation_passed: true
    require_review_approved: true
    require_published_revision: true
    exclude_stale: true
    exclude_unsupported: true
    exclude_retracted: true
    on_failure: raw_fallback
  canonical_v2:
    mode: "off"
  claims: {}
  rebuild: {}
  projection: {}
  validation: {}
  site: {}

maintenance:
  enabled: true
  center_enabled: true
  automation_level: supervised

mcp:
  tool_profile: core
  write_policy: disabled
  allow_http_write: false

rag:
  search_mode: hybrid_verified
  verified_knowledge:
    enabled: true
```

---

## 6. Serving Eligibility Gate 纠偏

### 6.1 持久化 Serving Validation Record

Claim 增加向后兼容的可选字段；旧 Claim 可读取，但在严格门禁下先 fail closed，再由迁移/Validator 生成记录。

建议模型：

```python
@dataclass
class ClaimServingValidation:
    passed: bool
    review_approved: bool
    validated_revision: int
    published_revision: int | None
    serving_evidence_ids: list[str]
    validator_version: str
    validated_at: str
    review_id: str | None
    operation_id: str | None
```

`Claim` 增加：

```python
serving_validation: ClaimServingValidation | None = None
```

读取旧 Claim 时缺失该字段合法；Serving 时按配置 fail closed。

### 6.2 严格门禁条件

可靠主结论必须同时满足：

```text
status == active
AND serving_validation.passed == true
AND serving_validation.review_approved == true
AND serving_validation.validated_revision == claim.revision
AND serving_validation.published_revision == claim.revision
AND serving_evidence_ids is not empty
AND every serving_evidence_id resolves to a current non-stale Block
AND every resolved Block hash matches Evidence.excerpt_hash
AND review_required == false
AND claim is not unsupported/retracted/superseded
```

任何条件失败：

- Claim 不进入主结果；
- 记录稳定 reason code；
- Search/Ask 继续 Raw；
- 若属于可修复状态，进入 Maintenance Review/Job。

### 6.3 发布与重新验证

- Validator 通过本身不自动发布；
- Review Approve 后运行 Validator；
- Validator + Projection Parity 通过后，显式 Publish 才写入 `published_revision`；
- Claim revision 改变后旧 validation 自动失效；
- Source hash 改变后 Gate 可实时 fail closed，不依赖异步任务及时完成；
- revalidate 命令支持 dry-run、单 Claim、单 Page 和批量上限。

---

## 7. 唯一 Maintenance Control Plane

### 7.1 统一入口

Source created/updated/deleted 的正式链路：

```text
File Watcher / Indexer / API Ingest
        ↓
SourceEventAdapter
        ↓
MaintenanceService.enqueue_source_event()
        ↓
Durable Job Store + Idempotency
        ↓
Impact Plan → Policy → R1/R2/R3/R4
        ↓
Repository Transaction → Outbox → Projection
        ↓
Review / Audit / Health
```

旧 `wiki_rebuild_scheduler` 不再作为平行控制面：

- 要么降级为 `MaintenanceService` 的兼容 adapter；
- 要么在所有调用方迁移后删除；
- 不允许 File Watcher 同时向两个任务系统投递。

### 7.2 持久化数据

使用现有 SQLite 增加非破坏性表：

| 表 | 用途 |
|---|---|
| `maintenance_source_events` | Source 事件、revision/hash、去重状态 |
| `maintenance_jobs` | 状态、风险、attempt、lease、计划、结果 |
| `maintenance_reviews` | Before/Proposed/Evidence、决策、note |
| `maintenance_dead_letters` | 超限失败、最后错误、恢复状态 |
| `maintenance_health_snapshots` | 质量与积压历史趋势 |
| `maintenance_schedules` | cron 名称、下次执行、租约 |

这些表只保存控制面状态和对象 ID，不复制 Claim/Page 正文。

### 7.3 幂等键

当前 `src:<event_type>:<knowledge_id>` 会把同一来源后续所有更新永久去重，必须替换为：

```text
source:<event_type>:<knowledge_id>:<source_revision_or_content_hash>
```

要求：

- 相同 revision 的重复 watcher 事件只生成一个 Job；
- 新 revision 必须生成新 Job；
- delete 使用 tombstone revision/hash；
- 幂等约束由数据库唯一索引保证，不依赖进程内 dict。

### 7.4 Job 状态机

```text
pending → leased → running → completed
                    ├→ waiting_review
                    ├→ retry_wait → pending
                    ├→ cancelled
                    └→ dead_letter
```

要求：

- lease 超时可恢复；
- retry 使用配置 backoff；
- cancel 必须协作式传入 rebuild；
- 进程重启后 pending/retry_wait/expired lease 可继续；
- completed/failed retention 可配置；
- max attempts、backoff、dead letter 均从配置读取，不硬编码。

### 7.5 调度

必须实现并测试：

- Source event debounce；
- Daily validation；
- Projection parity check；
- Weekly quality audit；
- Monthly full audit；
- 多实例租约下同一周期任务只有一个执行者；
- 关闭 scheduler/center 后 Raw Search 不受影响。

---

## 8. Review、发布与审计

### 8.1 Review Service 规则

- Reject 必须填写 Note；
- Conflict Resolution 必须填写 Note；
- Correct 保存为 Draft，不直接 Active；
- Approve 幂等，重复请求不产生二次 Revision；
- R4 必须传入由交互确认生成的 confirmation token；
- 禁止 `human_confirmed or True` 一类强制真值；
- 发布前强制 Validator 和 Projection Parity；
- 批量操作有上限，高风险批量发布禁止；
- 每次决策写 Operation Log、Correlation ID 和 Rollback Reference。

### 8.2 Web Maintenance Center

本轮以 React Web UI 为正式 Maintenance UI，桌面 GUI 只需保持兼容，不要求双份实现。

UI 必须包含：

1. Overview：Serving、stale、unsupported、projection drift、job/review backlog；
2. Jobs：筛选、详情、retry、cancel、dead letter；
3. Reviews：Before / Proposed / Evidence Diff；
4. Review actions：Approve / Reject / Correct / Defer；
5. Health：历史趋势、P0/P1 告警、reason code；
6. Source impact：dry-run 计划与受影响 Claim/Page；
7. 所有写操作调用共享 Service API，不在前端重写业务规则。

---

## 9. 真实 Hybrid 评测契约

### 9.1 两级评测

**CI 契约层：** 保留 175+ 离线确定性评测，用于 Gate、引用、冲突、fallback 和状态安全。

**Release 质量层：** 新增真实 SearchService + VerifiedAnswerService A/B，同一数据、同一问题分别运行：

- Raw Only；
- Hybrid Verified。

不得用“真实 embedding Raw Eval”替代“真实 Hybrid A/B”。

### 9.2 Release 数据集

最低 60 题：

| 类别 | 最低数量 |
|---|---:|
| Claim 应明显获益：定义、跨文档综合、结构化数值 | 20 |
| Raw 应优先：页码、原文定位、最新文件 | 15 |
| 冲突、限定条件、时效 | 10 |
| stale/unsupported/retracted/fallback | 10 |
| 无答案 | 5 |

至少 30 题为通信领域，所有题保留固定期望、证据和失败分类。

### 9.3 Release 门槛

- 总体 Hybrid accuracy ≥ Raw accuracy；
- Claim-benefit 子集 Hybrid accuracy > Raw accuracy，且绝对提升不少于 5 个百分点；
- Raw-preferred 子集回归不超过 2 个百分点；
- stale / unsupported / retracted serving rate = 0；
- citation correctness ≥ 0.95；
- evidence resolvability ≥ 0.99；
- conflict disclosure recall ≥ 0.90；
- Raw fallback success = 1.0；
- Hybrid 评测中必须实际出现 `verified_claim`，不接受全部 `raw_only`；
- 最新完整运行必须 PASS；部分复测、低于最小样本数或旧报告不能覆盖最新失败。

若 Hybrid 低于 Raw，立即触发停止条件，不允许调低阈值掩盖。

---

## 10. 工程与发布门禁

### 10.1 CI

必须全部通过：

```powershell
python -m ruff check src tests evals tools scripts
python -m mypy src tools --ignore-missing-imports
python -m pytest tests -q
python evals/run_retrieval_eval.py --all --fake-embedding --baseline evals/baselines/local.json --max-regression 0.05
python evals/run_hybrid_eval.py --strict --json
python evals/run_knowledge_evolution_eval.py --json
```

Python test matrix：3.10、3.11、3.12。Ruff/mypy 不允许用“基线债务”豁免。

### 10.2 前端、Docker、Windows

```powershell
Set-Location client
npm ci
npm run build
```

```powershell
docker build --target api .
docker build --target mcp .
```

Windows smoke 至少验证：

- `shinehe --help`；
- 临时目录 `shinehe init` 默认 verified；
- index 一个 fixture；
- MCP stdio initialize → `kb_capabilities` → `search` → `ask` → `read` → `ping`；
- Wiki 目录损坏时 Raw Search 仍成功；
- 启动/停止脚本不残留进程。

### 10.3 版本一致性

- `src/version.py`、Release Notes、README、PROGRESS 保持一致；
- 若前端沿用统一产品版本，`client/package.json` 同步为 1.8.0；
- 若前端采用独立版本，README 必须明确说明，不能出现无解释漂移。

---

## 11. 迁移与回滚

### 11.1 配置迁移

- 运行时兼容旧配置，不自动写盘；
- 提供 `shinehe doctor --explain-config`；
- 提供 `shinehe config migrate-verified-hybrid --dry-run`；
- apply 前备份，原子写，保留未知字段；
- 修正 `config.example.yaml` 不代表自动覆盖用户文件。

### 11.2 Claim Validation 迁移

- dry-run 统计缺少 validation record 的 Active Claim；
- apply 逐 Claim 运行 Validator；
- 只有通过 Evidence Resolution、Review/Published 证明可恢复的 Claim 才生成 record；
- 无法证明的 Claim 保持非 Serving，并生成 Review Item；
- 失败不修改 Claim statement/status；
- 迁移有备份、锁、Operation Log 和 rollback report。

### 11.3 Maintenance Schema 回滚

- Alembic upgrade 为新增表/索引，不改写 Canonical 正文；
- 关闭 `maintenance.center_enabled` 即停止 worker/UI 写操作；
- 回滚不删除历史 Job/Review/Audit；
- 已执行的 R1 通过 Canonical Revision/Operation Log 恢复，不直接反写 Raw。

---

## 12. 分阶段交付与提交边界

| Phase | 目标 | 建议提交 |
|---|---|---|
| 0 | 纠偏基线与状态标记 | `docs: open verified hybrid correction` |
| 1 | 配置解析、示例与旧配置兼容 | `fix(config): converge effective knowledge settings` |
| 2 | 严格 Serving Validation Gate | `fix(wiki): enforce reviewed validated serving revisions` |
| 3 | 持久 Maintenance Store | `feat(maintenance): persist jobs reviews and health` |
| 4 | Source Event、worker、scheduler | `feat(maintenance): unify source event automation` |
| 5 | Review Service、API、CLI、UI | `feat(maintenance-ui): complete review control plane` |
| 6 | 真实 Raw/Hybrid A/B | `test(eval): add real verified hybrid ablation` |
| 7 | CI、Python 矩阵、Docker、Windows | `ci: enforce convergence release gates` |
| 8 | 迁移、文档、最终评审与版本 | `docs: close verified hybrid convergence correction` |

每个 Phase 必须：

- 先红测试/可复现证据；
- 最小实现；
- 聚焦测试转绿；
- 邻域回归；
- 更新阶段报告；
- 独立可回滚；
- 不包含用户已有未跟踪文件。

---

## 13. Definition of Done

### 产品与运行态

- [ ] 新安装默认 Verified Hybrid；
- [ ] 当前旧 `wiki_first` 配置运行时有效启用 Authoring + Verified Hybrid；
- [ ] Evidence-only 可立即关闭 Wiki Serving；
- [ ] `search` / `ask` / `read` 是默认统一入口；
- [ ] Wiki/Maintenance 故障稳定 Raw fallback。

### Serving 正确性

- [ ] Active Claim 必须具备当前 revision 的 Validation + Review + Publish 记录；
- [ ] Serving Evidence 集全部可解析且 hash 一致；
- [ ] stale/unsupported/retracted serving rate = 0；
- [ ] 冲突被披露；
- [ ] Source 更新后 Gate 可立即 fail closed；
- [ ] 所有 Claim 引用回到原始 Block。

### Maintenance

- [ ] Source 事件进入唯一持久控制面；
- [ ] 新 revision 不会被旧幂等键错误吞掉；
- [ ] Job/Review/Dead Letter 重启后仍存在；
- [ ] lease、retry、cancel、backoff、retention 可用；
- [ ] R1 自动保护；
- [ ] R3 只生成 Draft/Review；
- [ ] R4 必须显式人工确认；
- [ ] Review UI 可完成 Evidence 对照和决策；
- [ ] Validator/Parity 失败阻止发布；
- [ ] Health 历史与告警可查看；
- [ ] Maintenance 故障不影响 Raw。

### 评测与工程

- [ ] 全量 pytest 通过；
- [ ] Ruff 0 error；
- [ ] mypy 0 error；
- [ ] Python 3.10/3.11/3.12 通过；
- [ ] 前端构建通过；
- [ ] Docker API/MCP 构建通过；
- [ ] Windows smoke 通过；
- [ ] 离线 Hybrid 契约评测通过；
- [ ] 真实 Raw/Hybrid A/B 达到 §9.3；
- [ ] 最新完整真实评测 PASS。

### 文档与发布

- [ ] `config.example.yaml` 结构测试通过；
- [ ] Migration 覆盖旧配置、Claim Validation、Maintenance Schema；
- [ ] README/README_zh/PROGRESS/Release Notes 版本一致；
- [ ] 最终评审逐项引用可复现证据；
- [ ] 最终报告明确回答 Wiki 是否真实提升；
- [ ] 发布前工作树只包含预期改动，用户未跟踪文件未被纳入。

---

## 14. 停止条件

出现任一情况必须停止当前 Phase 并报告：

- Hybrid 真实 A/B 低于 Raw；
- 任一 stale/unsupported/retracted Claim 进入主回答；
- 旧 `wiki_first` 无法运行或写能力意外丢失；
- Claim Validation 迁移需要破坏性改写或无法回滚；
- Source Event 被永久误去重；
- Maintenance 产生第二份 Claim/Page 真相；
- R3/R4 绕过 Review/Publish；
- P0 保护失败后 Claim 仍可 Serving；
- Maintenance 故障导致 Raw Search 失败；
- 全量测试出现无法解释的系统性失败；
- 为通过评测需要删除样本或降低阈值。

阻塞报告路径：

```text
docs/superpowers/handoffs/verified-hybrid-correction-blocker-<date>.md
```

---

## 15. 最终交付物

1. 有效配置解析器与兼容测试；
2. 正确的 `config.example.yaml`；
3. Claim Serving Validation 模型与迁移；
4. 严格 Serving Gate 与 reason codes；
5. SQLite Maintenance Store 与 Alembic migration；
6. 唯一 Source Event Adapter；
7. Worker、lease、retry、cancel、dead letter、cron；
8. Review Service、API、CLI 与 Web UI；
9. Operation Log、Correlation、Rollback Reference；
10. 真实 Raw/Hybrid A/B 数据集、runner 和报告；
11. Python 矩阵、Ruff、mypy、前端、Docker、Windows 门禁；
12. 配置/Claim/Maintenance 迁移文档；
13. Phase 0–8 阶段报告；
14. `docs/superpowers/reviews/verified-hybrid-correction-final-review.md`；
15. README、README_zh、PROGRESS、Release Notes 与版本同步。

只有本 Spec 的 DoD 全部满足，才允许把“融合收束已完成”重新写回 PROGRESS 和最终评审。
