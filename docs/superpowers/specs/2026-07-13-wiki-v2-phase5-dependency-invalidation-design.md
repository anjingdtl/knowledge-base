# Canonical Wiki V2 Phase 5：依赖图与失效传播（设计）

> **状态：** Draft（待评审）
> **日期：** 2026-07-13
> **分支：** `feature/wiki-v2-phase5-dependency-invalidation`
> **前置：** Phase 4C Primary 已验收（`docs/superpowers/reviews/2026-07-13-phase4c-primary-review.md`）
> **关联：**
> - 纠偏方案 `docs/superpowers/plans/2026-07-08-canonical-wiki-v2-correction-and-continuation.md` §6 Phase 5
> - 原始 spec `docs/superpowers/specs/2026-07-07-canonical-wiki-claim-provenance-design.md`（E2E-3/E2E-4 定义于 §1664-1684）
> - Claim 语义契约 `docs/architecture/wiki-v2-claim-merge-contract.md`
> - C2 黄金集 `evals/wiki_v2/README.md`、`source_update.jsonl`、`source_delete.jsonl`

---

## 1. 背景与目标

Phase 4C 已让 Canonical Wiki V2 成为唯一主写路径。当前来源（`knowledge_id`）更新或删除后，
依赖该来源的 Evidence / Claim / Wiki Page 不会自动失效，存在「事实陈旧但页面仍 published」的风险。

Phase 5 实现**精准、保守、可审、可重建**的依赖失效传播：

```
source(knowledge_id) 更新/删除
    ↓ block 哈希比对（只失效真正变化的块）
Evidence 标 stale（保留可审计）
    ↓
Claim 重新评估（仍有其他 supports → active；否则 → unsupported，不 retract）
    ↓
受影响 published Page → review（不自动发布）
    ↓ 经 WikiRepository 事务（复用 C3 staging）
canonical 落盘 → outbox → projection 刷新 → parity 复核
```

**核心目标**：来源变更不等于整页失效；先比对块哈希；保守迁移；变更可审；投影可重建。

---

## 2. 范围

### 2.1 纳入

- 只读依赖图（source→evidence→claim→page + claim↔claim 关系）
- 影响规划：`get_impacted_by_source` / `get_impacted_by_claim` / 拓扑有序 rebuild plan / dry-run
- 环检测与 `max_depth` 截断（claim↔claim 关系传递）
- Staged rebuild：source 更新 / source 删除，经 `WikiTransaction` 落盘
- `max_pages_per_job` 保护与协作取消
- per-kid debounce 调度器（独立于 `IndexScheduler`）
- `wiki_dependencies` 表投影（read model，可全量重建）
- 手动（CLI/API）→ canary（`rebuild.auto_allowlist`）→ auto（`rebuild.auto_on_source_update`）三级门控
- 启用 C2 `source_update.jsonl` / `source_delete.jsonl` 确定性评测

### 2.2 不纳入（铁律守住）

- **不删除** legacy fallback、**不扩大**自动发布、**不抢跑** Phase 6（迁移 / 反馈 / 正式评测）
- **不放宽** `contradicts` / `supersedes` 自动策略；**不改** matcher 语义与 normalize
- C2 的 5 个 xfail（单位 / 型号 / 地区 / 否定 / 强度词）**原样保留**，不绕过不删除
- 不引入跨进程持久化任务队列（属 Phase 6 基建）
- 不让 Projection 成为独立知识写源；planner 不依赖投影表已填充

---

## 3. 验收标准

| 项 | 要求 |
|---|---|
| **E2E-3 来源更新** | A v1 支持 Claim → A v2 删段 → 旧 Evidence stale → 无其他来源则 Claim `unsupported` → 引用页 `review` |
| **E2E-4 来源删除仍有他源** | A、B 均支持 Claim → 删 A → Claim 仍 `active`，仅剩 B 的 evidence |
| **u01** | 来源更新但相关 block 未变 → 未变化 evidence 保留，claim 仍 active |
| **u02** | 来源更新且相关 block 变 → 变化 evidence 标 stale，受影响页进 review |
| **u03** | 未变化 block 不触发重编译 |
| **d01** | 删来源但仍有其他有效 evidence → claim 仍 active，evidence 仅来自他源 |
| **d02** | 删来源且无其他有效 evidence → claim `unsupported`，受影响页进 review |
| **d03** | 来源删除**不物理删除** claim，审计历史保留 |
| 传播风暴防护 | `max_depth=5` / `max_pages_per_job=100` / 防环 / debounce 生效 |
| job cancel | 协作取消生效；已提交分事务保持一致，未处理项记入 plan |
| 黄金集 | `source_update.jsonl` + `source_delete.jsonl` 在确定性评测中通过（零 LLM/embedding） |
| 全量门禁 | pytest 不退化（基线 1455 passed / 2 skipped / 5 xfailed）、ruff 0、mypy 0、retrieval eval PASS、wiki eval 不退化 |

> C2 的 5 个 xfail 属 matcher 语义 gap，**不属** Phase 5 验收范围，保持 xfail。

---

## 4. 架构与组件

### 4.1 新增 / 改动文件

| 类型 | 文件 | 职责 |
|---|---|---|
| 新增 | `src/services/wiki_dependency_service.py` | 从 canonical 按需构建依赖图；影响规划；拓扑序；环检测；max_depth |
| 新增 | `src/services/wiki_rebuild_service.py` | block-diff → 标 stale → claim/page 状态迁移；经 WikiTransaction；projection refresh；max_pages；协作 cancel |
| 新增 | `src/services/wiki_rebuild_scheduler.py` | per-kid debounce 合并 + flush（复用 `IndexScheduler` 事件合并范式，独立调度） |
| 改动 | `src/models/wiki_v2.py` | `Evidence` 加 `stale` / `stale_at` |
| 改动 | `src/services/wiki_projection.py` | 投影 `stale` 列；claim/page upsert 时同步写 `wiki_dependencies`（read model） |
| 改动 | `src/services/db.py` + `alembic/versions/j002_*.py` | `wiki_claim_evidence` 加 `stale` / `stale_at` 列（幂等） |
| 改动 | `src/core/container.py` | 新增 3 个 lazy `@property`：`wiki_dependency_service` / `wiki_rebuild_service` / `wiki_rebuild_scheduler` |
| 改动 | `src/services/knowledge_workflow.py` | primary 模式下、门控命中时，raw 索引后触发 rebuild |
| 改动 | `src/services/path_indexer.py` / `file_watcher.py` | 文件变更/删除 → `rebuild_scheduler.schedule(kid, event)` |
| 改动 | `src/cli.py` | `shinehe rebuild --knowledge-id <id> [--dry-run]` |
| 改动 | 守卫测试（C6） | 扩展 AST/import boundary 扫描覆盖 3 个新文件 |

### 4.2 依赖注入（铁律 §3.5 / C6）

三个新服务全部**构造函数注入**，禁止 import `Config` / `Database` / `get_active_container`：

```python
class WikiDependencyService:
    def __init__(self, repository, config): ...

class WikiRebuildService:
    def __init__(self, repository, projection, block_repository,
                 dependency_service, config): ...

class RebuildScheduler:
    def __init__(self, rebuild_service, debounce_ms: int = 500): ...
```

- 测试可注入 fake repository / fake projection / fake block_repository / deterministic clock / deterministic ID。
- `config` 是容器传入的配置对象（dict-like），**非**全局 `Config` 单例。
- container 内 lazy `@property` + `_track_service`，与现有 wiki 服务同构。

---

## 5. 数据模型变更（最小）

### 5.1 Evidence 增加 stale 字段

```python
@dataclass
class Evidence:
    ...
    stale: bool = False        # 是否因来源变更/删除失效
    stale_at: str = ""         # 失效时间戳（注入 clock）
```

- `to_dict` / `from_dict` 同步；`from_dict(strict=False)` 容忍旧 canonical 文件无此字段 → **已有 claim YAML 零迁移**。
- **stale evidence 保留在 claim 中**（可审计，对齐 d03）；不物理删除。
- **决策**：stale 就地表达于模型，与 `ValidationFinding.category = "evidence_stale"` 语义一致，避免游离的 sidecar 注册表。

### 5.2 投影表列

`wiki_claim_evidence` 加两列：

```sql
stale INTEGER NOT NULL DEFAULT 0,
stale_at TEXT NOT NULL DEFAULT ''
```

**加列策略（SQLite 无 `ADD COLUMN IF NOT EXISTS`）**：

- `db.py` `_SCHEMA` 的建表语句补这两列 → **新库**直接建全。
- `alembic/versions/j002_evidence_stale.py` 对**老库**用 `inspect(connection)` 检查列缺失，再 `op.add_column(..., nullable=False, server_default="0"/"")`；`downgrade` **不删列**（保守，避免丢 stale 审计信息）。
- 已建库经 `db.py` 启动建表（`CREATE TABLE IF NOT EXISTS` 不会改已存在表结构），故老库必须靠 j002 补列；j002 幂等（先 inspect）。

`wiki_dependencies` 表已存在（j001），无需新建。

---

## 6. 依赖图设计（按需计算 + 表投影双轨）

### 6.1 真源

依赖图的唯一真源是 **canonical 状态**：

- `Evidence.knowledge_id` → source 链接
- `Claim.evidence[]` → evidence→claim
- `Claim.relations[]` → claim↔claim（`supersedes` / `refines` / `contradicts`，见契约 §3）
- `WikiPage.claim_ids` → claim→page

### 6.2 边集合（写入 `wiki_dependencies`，read model）

| from_type | from_id | to_type | to_id | relation |
|---|---|---|---|---|
| `source` | knowledge_id | `evidence` | evidence_id | `produces` |
| `evidence` | evidence_id | `claim` | claim_id | `evidences` |
| `claim` | claim_id | `page` | page_id | `cited_in` |
| `claim` | claim_id | `claim` | target_claim_id | `supersedes`/`refines`/`contradicts` |

### 6.3 双轨约束

- `WikiDependencyService` 的影响规划**从 canonical 按需计算**：遍历 `repository.list_claims()` / `list_pages()` 构建邻接，**不依赖** `wiki_dependencies` 表已填充。
- `wiki_dependencies` 表是**可重建 read model**：由 `WikiProjection` 在 `_upsert_claim` / `_upsert_page` 时同步写入（先删该对象相关边再插，幂等）；`rebuild()` 清空后全量重灌。
- planner 可用 fake repository 测试；表仅作 debug / SQL 图查询。满足铁律「Projection 不是第二个知识系统」。

### 6.4 环检测与 max_depth

- 环风险仅在 claim↔claim 关系传递（理论上 merge 应阻止，但必须防御）。
- 遍历用 BFS/DFS + `visited` 集合；遇已访问节点 → 记 `cycle_warning`，不重复展开。
- `max_depth` **只计 claim↔claim 关系传递深度**（source→evidence→claim→page 是固定浅链，深度≈3）。`max_depth=5` 限制 claim 关系扇出，防传播风暴。

---

## 7. 影响规划（ImpactPlan）

### 7.1 接口

```python
class WikiDependencyService:
    def get_impacted_by_source(self, knowledge_id: str, *, max_depth: int = 5) -> ImpactPlan: ...
    def get_impacted_by_claim(self, claim_id: str, *, max_depth: int = 5) -> ImpactPlan: ...
```

### 7.2 ImpactPlan 结构（确定性）

```python
@dataclass
class EvidenceImpact:
    evidence_id: str
    claim_id: str
    reason: str            # block_changed / block_deleted / source_deleted

@dataclass
class ClaimImpact:
    claim_id: str
    current_status: str
    proposed_status: str   # active→active | active→unsupported
    reason: str

@dataclass
class PageImpact:
    page_id: str
    current_status: str
    proposed_status: str   # published→review
    reason: str

@dataclass
class ImpactPlan:
    root: str
    affected_evidence: list[EvidenceImpact]
    affected_claims: list[ClaimImpact]
    affected_pages: list[PageImpact]
    topological_order: list[str]   # claim 重编译顺序
    cycle_warnings: list[str]
    truncated: bool                # 命中 max_depth 或 max_pages_per_job
    stats: dict
```

### 7.3 确定性保证

- **拓扑稳定**：同层按 `claim_id` 字典序打破并列。
- 测试用注入 clock + 注入 ID + 零 LLM/embedding，结果可复现。
- **active 判定只看 `supports` evidence**（与 `Claim.validate()` 一致：active 必须有 supports）；
  `contradicts` / `qualifies` evidence 不影响 active 资格（它们不提供支持），保持保守与模型 invariant 一致。

### 7.4 dry-run

`WikiRebuildService.plan_rebuild(knowledge_id, *, event, dry_run=True)` → 返回 `ImpactPlan`，不写。

---

## 8. Staged Rebuild（复用 WikiTransaction）

### 8.1 source 更新流程（E2E-3 / u01-u03）

1. 经 `block_repository` 取 `knowledge_id` 当前 blocks 的新 `excerpt_hash` + `source_revision`。
2. 遍历该 source 的 evidence：
   - block 缺失 → stale（`reason=block_deleted`）
   - `excerpt_hash` 不同 → stale（`reason=block_changed`）→ u02
   - 相同 → **保留，不重编译** → u01 / u03
3. 评估受影响 claim：仍有其他 `supports` 且非 stale 的 evidence → 保持 `active`；否则 → `unsupported`。
4. 受影响 `published` page → `review`。
5. 全部变更经 **`repository.transaction()`**：`stage_claim`（带 stale evidence + 新 status）+ `stage_page`（review）→ `validate` → `commit`（复用 C3 staging / manifest / COMMITTED / outbox / recover）。
6. `projection.process_outbox()` 刷新；`verify_parity()` 复核。

### 8.2 source 删除流程（E2E-4 / d01-d03）

同 8.1，但该 source 的**所有** evidence → stale（`reason=source_deleted`）；claim 有其他 supports → 仍 `active`（d01）；无 → `unsupported`（d02）；claim **不物理删除**（d03）。

### 8.3 max_pages_per_job

- 规划阶段若受影响 page 数 > `max_pages_per_job` → `truncated=True`，仅处理前 N 页（按 page_id 字典序），其余记入 `ImpactPlan.stats.pending_pages`。
- **不静默丢弃**：超限项显式记入 plan 并 `log()`。

### 8.4 协作取消

- `RebuildJob` 持 `cancel_event`（`threading.Event`）。
- rebuild 在每个 claim / page 处理前检查；取消则：已提交的分事务保留（一致状态）、未处理项记入 plan，返回 `cancelled=True`。
- 同步进程内执行（与 `IndexScheduler.flush` 一致），无后台线程、无跨进程状态。

### 8.5 projection refresh 策略

- **Phase 5 实现**：rebuild 事务提交后调 `projection.process_outbox()`（幂等重放 outbox）刷新受影响对象，再 `verify_parity()` 复核。
- **非 Phase 5 强制**（可选优化，留待后续）：targeted `_upsert_claim` / `_upsert_page` 跳过全量重放。本 phase 不实现，避免扩大范围。
- projection 失败**不回滚**已成功的 canonical 写入（铁律 §3.4）；drift 由 `verify_parity()` 检测、`fallback_to_full_rebuild` 修复。

---

## 9. 触发与 debounce（manual → canary → auto）

### 9.1 三级门控

| 级别 | 条件 | 行为 |
|---|---|---|
| manual | CLI / API 显式调用 | 始终可用，TDD 先行 |
| canary | `auto_on_source_update=false`（默认）**且** source 命中 `rebuild.auto_allowlist` | 仅 allowlist 内 source 变更时自动 rebuild |
| auto | `auto_on_source_update=true` | 全量自动（allowlist 忽略） |

> **`rebuild.auto_allowlist` 是 Phase 5 新增的、独立的 rebuild 允许集**（`knowledge_ids` + `source_paths`），
> 与 `canonical_v2.canary`（Phase 4 的 V2 写入 allowlist）**解耦**——避免在 primary 模式下
> （所有对象已走 V2）复用写入 allowlist 造成语义混淆。默认空 allowlist = **纯 manual**，最保守。

### 9.2 RebuildScheduler（per-kid 合并）

```python
class RebuildScheduler:
    def schedule(self, knowledge_id: str, event_type: str) -> None: ...  # update | delete
    def flush(self) -> RebuildBatchResult: ...
    @property
    def pending_count(self) -> int: ...
```

- per-kid 合并：`delete + update → delete`、多次 `update → 单 update`（复用 `IndexScheduler._merge_events` 范式）。
- `flush()` 对每个 pending kid 调 `rebuild_service.rebuild()`。
- 独立于 `IndexScheduler`（原始索引 vs 知识失效传播职责分离）。
- 新增 config `wiki.rebuild.debounce_ms`（默认 500，对齐 `watcher.debounce_ms`）。

### 9.3 文件监听接入（T5.3）

- `path_indexer` / `file_watcher` 在 raw 索引完成、得到新 `source_revision`（或确认删除）后 → `rebuild_scheduler.schedule(kid, update|delete)`。
- watcher 失败**不阻断**主进程（try/except + 日志，与 `IndexScheduler` 一致）。
- 仅 primary 模式 + 门控命中时才 schedule（`off` 模式零行为，对齐铁律）。

---

## 10. 配置变更

`config.yaml` → `wiki.rebuild` 段（部分已存在，补 `debounce_ms`）：

```yaml
wiki:
  rebuild:
    auto_on_source_update: false     # 已存在
    auto_publish_low_risk: false     # 已存在（Phase 5 保持 false，不扩大自动发布）
    max_pages_per_job: 100           # 已存在
    max_depth: 5                     # 已存在
    debounce_ms: 500                 # 新增
    auto_allowlist:                  # 新增（canary 级；与 canonical_v2.canary 解耦）
      knowledge_ids: []              # 显式允许自动 rebuild 的 knowledge_id
      source_paths: []               # 显式允许自动 rebuild 的 source_path/目录
```

---

## 11. 铁律对齐（逐条映射）

| 铁律（纠偏方案 §3） | Phase 5 落实 |
|---|---|
| Raw Source 是最终证据 | stale 判定基于 block 哈希比对，不凭 LLM 臆断；无完整 supports evidence 不自动 active |
| 自动化保守 | 失效后 published→**review**（不自动发布）；unsupported 不 retract |
| 单一 canonical 写入口 | 所有 claim/page 变更经 `repository.transaction()`，不直写文件/YAML/表 |
| Projection 是 read model | 依赖图表可全删重建；stale 字段由 canonical 投影；planner 不依赖表 |
| 构造函数 DI | 3 新服务全 DI；C6 守卫扩展覆盖新文件 |
| guard allowlist 空、扫描不空 | 不新增 allowlist；C6 守卫扩展扫描 3 个新文件 |
| 不删 legacy / 不扩自动发布 / 不抢跑 Phase 6 | rebuild 默认 off；低风险也不自动 publish；不碰迁移/反馈 |

---

## 12. 测试策略（TDD）

- 每个 Task：**先写红灯测试**（fake repo + 注入 clock/ID + 零 LLM/embedding），确认失败，再实现。
- **黄金集启用**：`tests/test_wiki_v2_golden_eval.py` 扩展加载 `source_update.jsonl` + `source_delete.jsonl`，断言 u01-u03 / d01-d03 行为。
- **E2E-3 / E2E-4**：专用集成测试（真实 `WikiRepository` + 临时 `wiki_dir` + 隔离 active container，复用 phase4c fixture 模式，避免跨测试泄漏）。
- **环检测 / max_depth / max_pages / cancel**：各自单元测试。
- **事务恢复**：rebuild 中途崩溃 → `recover()` 到一致（复用 C3 故障注入范式，新增 rebuild 段故障点）。
- **门禁**：每 Task 跑相关 pytest + ruff + mypy；每完成 phase 跑全量 pytest + retrieval eval + wiki eval，更新 PROGRESS + review 文档，再 commit。
- **纪律**：不把「测试通过」写成「语义正确」；黄金集 / E2E 必须同时报告。

---

## 13. TDD 任务拆分（对齐 T5.1 / T5.2 / T5.3）

| Task | 内容 | 验收 | commit |
|---|---|---|---|
| **T5.0** | `Evidence.stale` 字段 + 投影列 + alembic j002 | stale 序列化 / 投影 / 旧文件兼容 | `feat(wiki-v2): add stale flag to evidence` |
| **T5.1** | `WikiDependencyService`：图构建 + `get_impacted_by_source` + `get_impacted_by_claim` + 拓扑序 + 环检测 + max_depth | E2E-4（删 A 仍 active，剩 B） | `feat(wiki-v2): build source claim page dependency graph` |
| **T5.2a** | `WikiRebuildService.plan_rebuild(dry_run)` → ImpactPlan | u01-u03 / d01-d03 规划正确 | `feat(wiki-v2): plan source rebuild impact` |
| **T5.2b** | `WikiRebuildService.rebuild()`：staging 事务 + projection refresh + max_pages + cancel | E2E-3（来源更新→stale→unsupported→review） | `feat(wiki-v2): propagate source changes through affected knowledge` |
| **T5.2c** | `wiki_dependencies` 表投影 + parity | 依赖图 read model 可重建、parity 100% | `feat(wiki-v2): project dependency graph as read model` |
| **T5.3a** | `RebuildScheduler` per-kid debounce | 合并语义、不重复传播 | `feat(wiki-v2): add rebuild debounce scheduler` |
| **T5.3b** | `path_indexer` / `file_watcher` 接入 + CLI + canary/auto 门控 | 手动改文件触发 rebuild 冒烟 | `feat(wiki-v2): trigger incremental rebuild from source changes` |
| **T5.4** | 启用 C2 source_update/source_delete 黄金集 + E2E-3/E2E-4 集成测试 + 全量门禁 + PROGRESS/review | 黄金集 + E2E 通过、门禁不退化 | `test(wiki-v2): enable source evolution golden evaluation` |

每 Task 独立 commit，commit message 沿用 `feat(wiki-v2) / test(wiki-v2)` 风格。

---

## 14. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 传播风暴（大来源变更触发海量重编译） | `max_depth=5` + `max_pages_per_job=100` + 防环 + debounce + per-kid 合并 |
| stale 误判（block 哈希抖动） | 用 ingest 既有 `excerpt_hash`（非新哈希），与证据溯源链一致；unchanged block 零动作（u03） |
| active 误降级（漏看 supports） | active 判定只看 supports，与 `Claim.validate()` invariant 一致；保留可审计，不 retract |
| rebuild 中途崩溃半写 | 复用 C3 `WikiTransaction` + `recover()`；新增 rebuild 段故障注入测试 |
| active DI container 跨测试泄漏 | 新测试复用 phase4c fixture：重置 active container + per-test `wiki_dir` |
| 自动 rebuild 失控 | 默认 `auto_on_source_update=false`；canary 复用既有 allowlist；不扩自动发布 |
| `/wiki/` 运行产物误入版本控制 | `.gitignore` 已含 `/wiki/`；测试 fixture 隔离 `wiki_dir` |

---

## 15. 完成定义（Phase 5）

- [ ] E2E-3 / E2E-4 集成测试通过
- [ ] C2 `source_update` / `source_delete` 黄金集启用并通过（确定性）
- [ ] u01-u03 / d01-d03 行为符合预期
- [ ] 环检测 / max_depth / max_pages / cancel 各有测试
- [ ] `wiki_dependencies` 表可全量重建，parity 100%
- [ ] rebuild 中途崩溃可 `recover()` 到一致
- [ ] 三级门控（manual / canary / auto）各有测试；默认 off
- [ ] 3 个新服务构造函数 DI，C6 守卫覆盖
- [ ] guard allowlist 仍为空，扫描范围未收缩
- [ ] 全量 pytest / ruff / mypy / retrieval eval / wiki eval 不退化
- [ ] PROGRESS + review 文档更新
- [ ] C2 的 5 个 xfail 原样保留
