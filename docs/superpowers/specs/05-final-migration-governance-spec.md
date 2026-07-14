# ShineHeKnowledge 最终迁移治理收尾 SPEC

> **建议文件名：** `05-final-migration-governance-spec.md`  
> **目标版本：** `v1.10.1`  
> **适用对象：** Codex、Claude Code、OpenAI Codex CLI 或其他能够修改代码、运行测试并操作 Git 的开发 Agent  
> **执行方式：** 严格按 Task 顺序执行；每个 Task 独立测试、独立提交、独立回滚  
> **范围说明：** 本 Spec 只处理 v1.10.0 复核后剩余的迁移治理与 CI 门禁问题，不再扩展新的产品功能或大规模架构重构

---

# 1. 背景

v1.10.0 已经完成以下主要可维护性目标：

- 请求级 `SearchExecution` 已替代共享 `last_*` 状态；
- Retrieval 已统一为 `RetrievalOrchestrator` 的 Unified-only 路径；
- Legacy/Shadow 检索主管线已删除；
- Answer 业务逻辑已迁入 `src/answering/`；
- MCP Server 已收束为薄壳，工具实现已按领域拆分；
- Container 能力分组已升级为拥有构造和生命周期的 Provider；
- Alembic 临时数据库升级测试与启动 Migration Gate 已建立。

但复核仍发现三项未完全闭环的问题：

1. `architecture-closure` CI 只运行债务报告，没有启用 `--strict`，架构债务重新出现时可能仍保持绿色；
2. `Database` 构造仍会先执行 `_SCHEMA` 和 `_migrate()`，Migration Gate 在数据库已被修改后才检查；
3. 已有表但没有 `alembic_version` 的 Unstamped 数据库默认允许写入，Alembic 尚未成为运行时唯一 Schema 权威。

本 Spec 的目标是完成最后一轮迁移治理闭环。

---

# 2. 最终目标

本 Spec 完成后，系统必须满足：

```text
启动
→ 读取数据库迁移状态
→ 判断是否允许写模式
→ 必要时拒绝启动或进入只读诊断
→ 仅当 Schema 合法后创建运行时 Database 连接
```

新数据库和旧数据库的 Schema 创建、升级、Stamp 必须由 Alembic 控制。

运行时业务代码不得再主动：

```text
CREATE TABLE
ALTER TABLE
CREATE INDEX
重建 FTS
补齐字段
```

架构门禁必须在 CI 中以非零退出码阻断回归。

---

# 3. 非目标

本轮明确不做：

- 不修改 Retrieval 排序、召回、RRF、Rerank；
- 不修改 Wiki Serving Gate；
- 不修改 Claim、Evidence、Canonical Wiki 语义；
- 不继续拆分 MCP 工具文件；
- 不继续拆分 SearchService；
- 不删除 `Database._instance` 兼容层；
- 不更换 SQLite；
- 不引入新的 ORM；
- 不修改公开 MCP/API 返回契约；
- 不新增产品功能；
- 不重写全部 Repository。

如果开发过程中发现上述问题，必须记录为后续 Issue，不得扩入本 Spec。

---

# 4. 执行顺序

必须按以下顺序执行：

```text
WP0 基线冻结
  ↓
WP1 CI 严格门禁
  ↓
WP2 Migration Gate 前置
  ↓
WP3 Alembic 新库初始化
  ↓
WP4 Unstamped 旧库迁移
  ↓
WP5 停用运行时 Schema 修改
  ↓
WP6 最终验收与发布
```

禁止同时开始 WP2、WP3、WP4 和 WP5。

---

# 5. Agent 通用规则

## 5.1 每个 Task 的固定流程

Agent 对每个 Task 必须执行：

1. 阅读本 Task 涉及的生产代码、测试和文档；
2. 搜索所有调用方；
3. 先补测试或修改测试；
4. 运行测试，确认测试能够识别当前问题；
5. 实施最小代码修改；
6. 运行目标测试；
7. 运行相关模块测试；
8. 运行全量测试；
9. 运行 Ruff、MyPy、Retrieval Eval、Hybrid Eval；
10. 更新交接报告；
11. 创建单一职责 Commit。

## 5.2 禁止行为

Agent 不得：

- 在同一提交中同时修改迁移逻辑和业务检索逻辑；
- 通过 `pytest.skip` 隐藏迁移失败；
- 使用 `try/except Exception: pass` 忽略迁移错误；
- 在测试中修改用户真实数据库；
- 自动 Stamp 未知结构的数据库；
- 无备份地修改已有生产数据库；
- 删除 `_migrate()` 后不提供旧库迁移路径；
- 将 `SHINEHE_SKIP_MIGRATION_GATE` 设为默认；
- 修改公开 Search、Ask、MCP 契约；
- 为使 CI 通过而放宽架构门禁指标。

## 5.3 停止条件

出现以下任一情况，Agent 必须停止并报告：

- 无法确认某个历史数据库版本的 Schema；
- Alembic Upgrade 可能造成不可逆数据丢失；
- 空数据库升级和旧数据库升级产生不同最终 Schema；
- 迁移需要修改超过 3 个 Alembic Revision；
- 目标 Task 需要修改检索、Wiki 或 MCP 契约；
- 全量测试基线在修改前已经失败；
- 迁移测试会访问真实用户数据路径；
- 无法为旧库提供可验证的备份与恢复路径。

---

# 6. WP0：冻结最终迁移治理基线

## Task WP0-T1：建立当前迁移行为快照

创建：

```text
docs/superpowers/reviews/final-migration-governance-baseline.md
tests/migrations/test_runtime_schema_mutation_baseline.py
```

基线必须记录：

- `Database.__init__()` 是否执行 `_SCHEMA`；
- `Database.__init__()` 是否调用 `_migrate()`；
- `create_container()` 中 Migration Gate 的调用顺序；
- `allow_unstamped` 当前默认值；
- `architecture-closure` 是否使用 `--strict`；
- 空数据库升级后的表、索引和 Revision；
- v1.9 数据库升级后的表、索引和 Revision；
- `_SCHEMA` 中包含的 Schema 对象数量；
- `_migrate()` 中包含的 Schema 修改语句数量。

测试只记录或断言当前已知状态，不能先假设最终结果。

## Task WP0-T2：建立最终 Schema 指纹工具

创建：

```text
tools/schema_fingerprint.py
tests/migrations/test_schema_fingerprint.py
```

Schema Fingerprint 至少包括：

- table 名称；
- 每张表的字段名、类型、默认值、非空约束、主键；
- index 名称和字段；
- trigger 名称；
- FTS/虚拟表名称；
- `alembic_version`；
- 不包含业务数据行。

建议输出稳定 JSON：

```json
{
  "revision": "...",
  "tables": {},
  "indexes": {},
  "triggers": {},
  "virtual_tables": []
}
```

必须支持：

```bash
python tools/schema_fingerprint.py --db path/to/db.sqlite --json
```

## WP0 验收

- 基线文档生成；
- 指纹工具对同一数据库重复执行结果一致；
- 指纹中不包含业务数据；
- 测试不会访问默认用户数据库。

## Commit

```text
test(migration): freeze final governance baseline and schema fingerprint
```

---

# 7. WP1：CI 架构门禁严格化

## Task WP1-T1：启用严格债务门禁

修改：

```text
.github/workflows/ci.yml
```

将：

```bash
python tools/report_closure_debt.py
```

改为：

```bash
python tools/report_closure_debt.py --strict
```

要求：

- 任何 residual debt 必须令 Job 失败；
- 普通报告仍可作为日志输出；
- 不允许在 Workflow 中追加 `|| true`；
- 不允许使用 `continue-on-error: true`。

## Task WP1-T2：补齐架构测试断言

修改：

```text
tests/architecture/test_closure_debt_baseline.py
```

新增强制断言：

```python
assert m["database_instance_refs_outside_infra"] == 0
assert m["get_active_container_refs_outside_whitelist"] == 0
```

同时断言：

```python
assert _strict_failures(m) == []
```

为了测试 `_strict_failures`，允许从工具模块导出该函数，或新增公开函数：

```python
validate_strict_metrics(metrics) -> list[str]
```

## Task WP1-T3：增加负向门禁测试

创建：

```text
tests/architecture/test_closure_debt_strict_mode.py
```

通过临时目录构造最小伪仓库，分别注入：

- `Database._instance` 非白名单引用；
- 非白名单 `get_active_container()`；
- `SearchService._search_legacy_pipeline`；
- `server.py` 中 MCP 工具函数；
- Alembic 测试中的 `pytest.skip`。

断言严格模式返回失败。

## WP1 验收

```bash
python tools/report_closure_debt.py --strict
```

退出码必须为 0。

负向测试必须证明任何一项债务重新出现时，退出码为非零。

## 回滚

仅回滚 Workflow 和测试，不影响运行时。

## Commit

```text
ci(architecture): enforce strict maintainability closure gate
```

---

# 8. WP2：Migration Gate 前置

## 8.1 问题定义

当前流程是：

```text
Database(db_path)
→ executescript(_SCHEMA)
→ _migrate()
→ Migration Gate
```

目标流程必须变为：

```text
Resolve DB path
→ Inspect migration status using read-only sqlite connection
→ Enforce Migration Gate
→ 必要时运行 Alembic
→ 创建 Database runtime connection
```

## Task WP2-T1：拆分数据库路径解析与连接创建

修改：

```text
src/core/container.py
src/services/db.py
```

新增纯函数或独立模块：

```text
src/storage/database_bootstrap.py
```

建议接口：

```python
@dataclass(frozen=True)
class DatabaseBootstrapPlan:
    db_path: Path
    exists: bool
    empty: bool
    migration_status: MigrationStatus
    readonly: bool
    write_allowed: bool
    action: str
```

提供：

```python
def inspect_database_bootstrap(
    db_path: str | Path,
    *,
    config=None,
) -> DatabaseBootstrapPlan:
    ...
```

该函数只能：

- 检查文件是否存在；
- 使用只读 SQLite 连接读取 `sqlite_master` 和 `alembic_version`；
- 计算 Gate Decision；
- 不得创建数据库文件；
- 不得修改 Schema。

## Task WP2-T2：将 Gate 移到 Database 构造前

重构 `create_container()`：

```python
db_path = config.get_db_path()
plan = inspect_database_bootstrap(db_path, config=config)
enforce_bootstrap_plan(plan)
db = Database.open_runtime(db_path, readonly=plan.readonly)
```

禁止：

```python
db = Database(...)
enforce_startup_gate(...)
```

## Task WP2-T3：只读模式使用只读连接

当：

```text
storage.readonly=true
```

或 Gate 判定只能只读时，SQLite 连接必须使用只读 URI：

```text
file:path/to/db.sqlite?mode=ro
```

只读模式不得：

- 创建数据库文件；
- 执行 `_SCHEMA`；
- 执行 `_migrate()`；
- 执行 Alembic Upgrade；
- 创建 WAL；
- 写入配置或健康表。

## Task WP2-T4：前置门禁测试

创建：

```text
tests/storage/test_database_bootstrap_order.py
```

覆盖：

1. Behind-head 数据库：
   - `Database` 构造函数不得被调用；
   - Schema 指纹在失败前后完全一致；
   - 抛出 `MigrationGateError`。

2. 只读 Behind-head：
   - 允许创建只读 Container；
   - `write_allowed=False`；
   - Schema 指纹不变化。

3. 无数据库文件：
   - 检查阶段不得创建文件；
   - 后续由 WP3 的 Alembic 初始化负责创建。

4. Unstamped 数据库：
   - 默认不得在检查阶段修改；
   - 具体处理交由 WP4。

## WP2 验收

Migration Gate 必须发生在任何 Schema 修改之前。

使用 Monkeypatch 或 Fake Database 证明 Gate 失败时 `Database.open_runtime()` 未被调用。

## Commit

```text
refactor(storage): enforce migration gate before database mutation
```

---

# 9. WP3：Alembic 负责新数据库初始化

## Task WP3-T1：提供显式 Alembic Upgrade 服务

创建：

```text
src/storage/alembic_runner.py
```

建议接口：

```python
@dataclass(frozen=True)
class AlembicUpgradeResult:
    db_path: str
    before_revision: str | None
    after_revision: str
    upgraded: bool

def upgrade_to_head(
    db_path: str | Path,
    *,
    project_root: Path | None = None,
) -> AlembicUpgradeResult:
    ...
```

要求：

- 显式传入目标 DB URL；
- 不读取用户默认数据库作为回退；
- 失败时抛出明确异常；
- 不吞掉 stdout/stderr；
- 能够被 CLI 和启动流程复用。

## Task WP3-T2：空数据库使用 Alembic 创建 Schema

目标行为：

```text
数据库文件不存在
→ 写模式启动
→ Alembic upgrade head
→ 验证 at_head
→ Database.open_runtime()
```

不允许再通过 `_SCHEMA` 创建正式新库。

是否自动运行 Alembic 由配置控制：

```yaml
storage:
  migration_gate:
    auto_upgrade_empty: true
```

建议默认：

```text
true
```

仅对“文件不存在或完全空库”生效。

不得对 Behind-head 或 Unstamped 非空数据库自动升级。

## Task WP3-T3：新库 Schema 等价性测试

创建：

```text
tests/migrations/test_new_database_runtime_bootstrap.py
```

测试：

1. 启动不存在的数据库；
2. Alembic 创建数据库；
3. 数据库位于 Head；
4. Runtime Database 成功打开；
5. Schema Fingerprint 与 `alembic upgrade head` 直接创建的数据库完全一致；
6. 重复启动不修改 Schema。

## Task WP3-T4：Database Runtime Open 模式

修改 `Database`：

建议增加：

```python
@classmethod
def open_runtime(
    cls,
    db_path: str | Path,
    *,
    readonly: bool = False,
) -> "Database":
    ...
```

Runtime Open 只负责：

- 连接；
- PRAGMA；
- sqlite-vec；
- thread-local；
- transaction；
- health。

不得创建或升级正式 Schema。

在过渡期，原 `Database(db_path)` 可以保留兼容行为，但生产 `create_container()` 必须改用 `open_runtime()`。

## WP3 验收

- 新数据库完全由 Alembic 创建；
- Runtime Open 不执行 Schema 修改；
- 空库启动和直接 `alembic upgrade head` 的 Schema 指纹相同；
- 重复启动幂等。

## 回滚

保留旧构造入口一个版本，但生产启动路径不得回滚为 `_SCHEMA` 初始化。

## Commit

```text
feat(storage): initialize new databases exclusively through Alembic
```

---

# 10. WP4：Unstamped 旧库安全迁移

## 10.1 默认策略

修改默认配置：

```yaml
storage:
  migration_gate:
    allow_unstamped: false
```

Unstamped 非空数据库在写模式下必须拒绝启动。

只读模式可以启动，但：

```text
write_allowed = false
```

## Task WP4-T1：实现旧库识别

创建：

```text
src/storage/legacy_schema_detector.py
```

建议接口：

```python
@dataclass(frozen=True)
class LegacySchemaMatch:
    matched_version: str | None
    confidence: str
    fingerprint: dict
    reasons: tuple[str, ...]

def detect_legacy_schema(db_path: str | Path) -> LegacySchemaMatch:
    ...
```

只允许识别明确支持的历史版本，例如：

```text
v1.8.x
v1.9.0
v1.9.x
```

如果结构不匹配，必须返回未知，不得猜测。

## Task WP4-T2：增加迁移 CLI

创建或扩展：

```text
src/cli.py
src/storage/migration_cli.py
```

建议命令：

```bash
shinehe db status
shinehe db backup
shinehe db migrate
shinehe db stamp --from-version v1.9.0
shinehe db verify
```

最低要求：

### `db status`

输出：

- DB 路径；
- 当前 Revision；
- Head；
- 是否 Unstamped；
- 检测到的历史版本；
- 是否允许写入；
- 推荐操作。

### `db backup`

创建：

```text
<db>.backup-YYYYMMDD-HHMMSS.sqlite
```

必须使用 SQLite Backup API，不得简单复制活动 WAL 数据库。

### `db migrate`

执行：

```text
备份
→ 历史版本识别
→ 必要时 Stamp 到对应 Revision
→ Alembic upgrade head
→ Schema Fingerprint
→ Integrity Check
```

### `db stamp`

仅允许用户显式指定版本，且 Schema Detector 必须匹配。

禁止：

```bash
shinehe db stamp head
```

直接把未知 Schema Stamp 到 Head。

## Task WP4-T3：旧库迁移前后验证

迁移必须验证：

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

并验证：

- 关键表仍存在；
- 业务数据行数不减少；
- `alembic_version` 为 Head；
- Schema Fingerprint 与标准 Head 结构一致；
- Wiki、Block、Knowledge、Job 关键数据数量一致。

## Task WP4-T4：迁移失败自动恢复

若 Upgrade 失败：

- 原数据库必须关闭；
- 恢复备份；
- 保留失败日志；
- 不删除失败副本，可重命名为：

```text
<db>.failed-migration-YYYYMMDD-HHMMSS.sqlite
```

恢复后必须重新执行 Integrity Check。

## Task WP4-T5：Unstamped 测试矩阵

创建：

```text
tests/migrations/test_unstamped_legacy_migration.py
```

覆盖：

1. 已知 v1.9 Unstamped：
   - 默认启动被阻止；
   - CLI 能备份、Stamp、Upgrade；
   - 数据不丢失。

2. 未知 Unstamped：
   - 启动被阻止；
   - 自动迁移拒绝；
   - 只读模式允许；
   - 必须人工处理。

3. 已知版本但 Schema 被人工修改：
   - Detector 不得高置信匹配；
   - 自动 Stamp 拒绝。

4. 迁移中断：
   - 恢复备份；
   - 原数据可读取。

## WP4 验收

- `allow_unstamped` 默认 false；
- 非空 Unstamped 写启动被阻止；
- 已知旧库有安全迁移命令；
- 未知旧库不会自动 Stamp；
- 迁移失败可恢复。

## Commit

```text
feat(storage): add safe legacy database stamp and migration workflow
```

---

# 11. WP5：停用运行时 Schema 修改

## Task WP5-T1：拆除生产 Runtime 的 `_SCHEMA`

目标：

生产 Runtime 连接不得执行：

```python
self._base_conn.executescript(_SCHEMA)
```

可以暂时将 `_SCHEMA` 保留为：

- 历史测试 Fixture；
- Legacy Detector 参考；
- Alembic 基线校验输入。

但生产路径不得调用。

## Task WP5-T2：停用 `_migrate()`

目标：

生产 Runtime 不得调用：

```python
self._migrate()
```

处理方式二选一：

### 方案 A：保留但禁止生产调用

```python
def _migrate(self):
    raise RuntimeError(
        "Runtime schema migration removed; run `shinehe db migrate`"
    )
```

### 方案 B：迁移到 Compatibility 模块

```text
src/compatibility/runtime_schema_migrate.py
```

仅供明确的历史迁移命令使用，生产启动不导入。

推荐方案 B。

## Task WP5-T3：防回归静态门禁

扩展：

```text
tools/report_closure_debt.py
```

新增指标：

```text
database_runtime_executes_schema
database_runtime_calls_migrate
container_gate_after_database_open
allow_unstamped_default_true
```

严格失败条件：

```python
database_runtime_executes_schema is False
database_runtime_calls_migrate is False
container_gate_after_database_open is False
allow_unstamped_default_true is False
```

同时加入：

```text
tests/architecture/test_closure_debt_baseline.py
```

## Task WP5-T4：Runtime Schema Mutation 负向测试

创建：

```text
tests/storage/test_runtime_schema_is_read_only.py
```

运行时打开一个 Head 数据库后，记录 Schema Fingerprint。

执行：

- Container 创建；
- Search；
- Ask；
- Index 读取；
- Wiki Serving 读取；
- 关闭 Container。

再次记录 Schema Fingerprint，必须完全一致。

写业务数据可以改变行数，但不能改变 Schema。

## WP5 验收

生产启动路径中不存在：

```text
executescript(_SCHEMA)
self._migrate()
ALTER TABLE
CREATE TABLE
CREATE INDEX
```

新增 Schema 必须只能通过 Alembic Revision。

## Commit

```text
refactor(storage): remove runtime schema creation and migration authority
```

---

# 12. WP6：最终验收与发布

## Task WP6-T1：全量验证

必须运行：

```bash
python tools/report_closure_debt.py --strict
pytest tests/ -q
ruff check .
mypy src
python evals/run_retrieval_eval.py \
  --all \
  --fake-embedding \
  --baseline evals/baselines/local.json \
  --max-regression 0.05
python evals/run_hybrid_eval.py --strict
```

迁移专项：

```bash
pytest \
  tests/migrations/ \
  tests/storage/ \
  tests/test_alembic_baseline.py \
  -q
```

契约专项：

```bash
pytest \
  tests/test_public_search_contract.py \
  tests/test_public_ask_contract.py \
  tests/test_wiki_serving_contract.py \
  tests/test_mcp_contract.py \
  -q
```

## Task WP6-T2：迁移测试矩阵

必须验证：

| 场景 | 预期 |
|---|---|
| DB 不存在 | Alembic 创建 Head DB |
| 空文件 DB | Alembic 创建 Head DB |
| 已在 Head | 正常启动，不改 Schema |
| Stamped Behind Head | 写启动拒绝 |
| Stamped Behind Head + readonly | 只读启动 |
| 已知 Unstamped v1.9 | 写启动拒绝，可 CLI 迁移 |
| 未知 Unstamped | 写启动拒绝，不自动 Stamp |
| 迁移中断 | 自动恢复备份 |
| 重复 migrate | 幂等 |
| 错误数据库路径 | 不触碰默认用户 DB |

## Task WP6-T3：文档

创建或更新：

```text
docs/architecture/database-migration-policy.md
docs/migration/v1.10-to-v1.10.1-migration-governance.md
docs/release/v1.10.1-release-notes.md
PROGRESS.md
README.md
README_zh.md
config.example.yaml
```

文档必须明确：

- 新数据库由 Alembic 创建；
- 运行时不再修改 Schema；
- Unstamped 数据库默认拒绝写启动；
- 如何执行 `shinehe db status`；
- 如何备份和迁移；
- 如何使用只读诊断；
- 如何恢复失败迁移；
- `SHINEHE_SKIP_MIGRATION_GATE` 仅用于紧急诊断，不推荐生产使用。

## Task WP6-T4：最终验收报告

创建：

```text
docs/superpowers/reviews/final-migration-governance-acceptance.md
```

包含：

- 修改文件；
- Commit 列表；
- 所有测试结果；
- Schema Fingerprint 对比；
- 空库与旧库迁移结果；
- 备份恢复结果；
- Retrieval/Hybrid 指标；
- 已知风险；
- 是否允许发布。

## Commit

```text
release: v1.10.1 final migration governance closure
```

---

# 13. CI 最终要求

`.github/workflows/ci.yml` 至少包含：

## Architecture Closure

```bash
python tools/report_closure_debt.py --strict
pytest tests/architecture/ -q
```

## Migration Gate

```bash
pytest \
  tests/migrations/ \
  tests/storage/ \
  tests/test_alembic_baseline.py \
  -q
```

环境：

```text
SHINEHE_ENFORCE_MIGRATION_GATE=1
```

## Contract Gate

```bash
pytest \
  tests/test_public_search_contract.py \
  tests/test_public_ask_contract.py \
  tests/test_wiki_serving_contract.py \
  tests/test_mcp_contract.py \
  -q
```

## Retrieval Eval

保持当前 Retrieval 和 Hybrid Eval 门禁。

所有 Job 均不得：

```text
continue-on-error: true
|| true
pytest.skip 迁移失败
```

---

# 14. 发布与回滚策略

## 14.1 建议版本

```text
v1.10.1
```

本版本属于迁移治理修复，不改变公开 API/MCP 契约。

## 14.2 发布前备份

升级前必须建议用户：

```bash
shinehe db status
shinehe db backup
shinehe db migrate
```

## 14.3 回滚

如果代码需要回滚至 v1.10.0：

- 已升级到 Head 的数据库应保持向前兼容；
- 不执行自动 Downgrade；
- 代码回滚前先备份；
- v1.10.0 仍可能执行 `_SCHEMA + _migrate()`，必须在发布说明中警告；
- 数据库 Schema 不应因为代码回滚而自动降级。

---

# 15. 最终不变量

完成后必须自动化保护以下不变量：

```text
MIG-001 新数据库只能由 Alembic 创建
MIG-002 运行时 Database 不修改 Schema
MIG-003 Migration Gate 在 Database Runtime Open 前执行
MIG-004 Stamped Behind Head 的写启动必须失败
MIG-005 Unstamped 非空数据库默认拒绝写启动
MIG-006 未知 Schema 不得自动 Stamp
MIG-007 旧库迁移前必须创建 SQLite 一致性备份
MIG-008 迁移失败必须可恢复
MIG-009 空库与升级库最终 Schema Fingerprint 必须一致
MIG-010 Architecture Debt CI 必须使用 strict 模式
MIG-011 Search/Ask/Wiki/MCP 契约不得因迁移治理变化
MIG-012 Retrieval 和 Hybrid Eval 不得退化
```

---

# 16. Agent 每个 Task 的完成报告格式

```markdown
# Task 完成报告

## Task
- ID：
- 基线 SHA：
- 分支：

## 修改范围
- 生产代码：
- 测试：
- 文档：

## 明确未修改
- Retrieval：
- Wiki：
- MCP Contract：
- 数据模型：

## 验证结果
- Targeted tests：
- Migration tests：
- Full pytest：
- Ruff：
- MyPy：
- Retrieval Eval：
- Hybrid Eval：
- Debt strict：

## Schema 证据
- Before revision：
- After revision：
- Fingerprint equal：
- Integrity check：
- Foreign key check：

## 回滚方式
- ...

## 已知风险
- ...

## 是否允许进入下一 Task
- YES / NO
- 原因：

## Commit
- `<sha> <message>`
```

---

# 17. 首批执行边界

Agent 首批只允许执行：

```text
WP0-T1
WP0-T2
WP1-T1
WP1-T2
WP1-T3
```

首批完成后必须停止，并提交：

```text
final-migration-governance-baseline.md
WP1 完成报告
CI strict 运行结果
```

未获得确认前，不得进入 WP2 的数据库启动顺序调整。

---

# 18. 完成定义

只有以下条件全部成立，本 Spec 才能标记 Completed：

- `architecture-closure` 使用 `--strict`；
- 严格债务指标全部为 0；
- Migration Gate 在 Database Runtime Open 前执行；
- 新数据库由 Alembic 创建；
- `allow_unstamped` 默认 false；
- 已知旧库支持备份、识别、Stamp、Upgrade；
- 未知旧库不会自动 Stamp；
- 生产 Runtime 不执行 `_SCHEMA`；
- 生产 Runtime 不调用 `_migrate()`；
- 空库、旧库、Head 库 Schema Fingerprint 一致；
- 迁移失败恢复测试通过；
- 全量测试、Ruff、MyPy 通过；
- Retrieval Eval、Hybrid Eval 不下降；
- Search、Ask、Wiki、MCP 契约保持兼容；
- v1.10.1 迁移文档和发布说明完成。

完成后，Alembic 才可以被正式认定为：

> **ShineHeKnowledge 唯一的运行时 Schema 创建与升级权威。**
