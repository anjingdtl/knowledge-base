# Final Migration Governance — Baseline Snapshot (WP0-T1)

> **日期:** 2026-07-14  
> **分支:** `master`  
> **基线 SHA:** `4eb1f8bdcb558c98b37295c3f8b189b293e9281f`  
> **规格:** `docs/superpowers/specs/05-final-migration-governance-spec.md`  
> **范围:** 仅记录当前已知迁移/Schema 行为，不假设 WP2–WP5 最终状态

---

## 1. 环境

| 项 | 值 |
|---|---|
| Git SHA | `4eb1f8bdcb558c98b37295c3f8b189b293e9281f` |
| Python | 3.14.3 |
| OS | Windows |
| Alembic head | `j003_maintenance_control_plane` |

---

## 2. 运行时 Schema 修改行为（当前）

### 2.1 `Database.__init__()` / `_connect_internal()`

| 问题 | 当前答案 |
|---|---|
| 是否执行 `_SCHEMA`？ | **是** — `_base_conn.executescript(_SCHEMA)` |
| 是否调用 `_migrate()`？ | **是** — `self._migrate()` |
| 顺序 | connect → PRAGMA/sqlite-vec → `_SCHEMA` → `_migrate()` → commit → set `_instance` |

源码位置：`src/services/db.py` → `_connect_internal()`。

### 2.2 `create_container()` 中 Migration Gate 顺序

| 步骤 | 当前实现 |
|---|---|
| 1 | `Config.load` |
| 2 | `Database(str(db_path))`（**已执行 _SCHEMA + _migrate**） |
| 2.5 | `enforce_startup_gate(db_path, config=config)` |
| 3+ | VectorStore / BlockStore / Graph / AI services |

**结论（问题现状）：** Migration Gate 发生在 Database 构造**之后**，因此 gate 失败时数据库可能已被 `_SCHEMA` / `_migrate()` 修改。  
目标顺序（WP2，本基线不实施）：inspect → gate → 仅合法后 open runtime。

源码位置：`src/core/container.py` → `create_container()`。

### 2.3 `allow_unstamped` 默认值

| 来源 | 值 |
|---|---|
| `resolve_allow_unstamped(None)` | **`True`** |
| `startup_gate.py` `_cfg_bool(..., True)` | 默认 **True** |
| `config.example.yaml` | 注释说明 transition；未强制 `false` |

**含义：** 已有表但无 `alembic_version` 的 Unstamped 库在写模式下默认允许启动（仅 warning）。  
目标（WP4）：默认 `false`，写启动拒绝。

### 2.4 architecture-closure CI 是否使用 `--strict`

| 项 | WP0 基线 | WP1-T1 后 |
|---|---|---|
| Job | `architecture-closure` | 同左 |
| 命令 | `python tools/report_closure_debt.py`（**无** `--strict`） | `python tools/report_closure_debt.py --strict` |
| 严格模式实现 | 工具已支持 `--strict`，CI 未启用 | **CI 已启用**；禁止 `\|\| true` / `continue-on-error` |

源码位置：`.github/workflows/ci.yml`。

---

## 3. `_SCHEMA` / `_migrate()` 规模（源码静态计数）

计数规则：对 `src/services/db.py` 中 `_SCHEMA` 字符串与 `_migrate` 方法体做正则 DDL 关键字统计。

### 3.1 `_SCHEMA` Schema 对象数量

| 类型 | 数量 |
|---|---|
| `CREATE TABLE` | 43 |
| `CREATE INDEX` / `CREATE UNIQUE INDEX` | 45 |
| `CREATE VIRTUAL TABLE` | 6 |
| `CREATE TRIGGER` | 9 |
| **合计 Schema 对象** | **103** |

### 3.2 `_migrate()` Schema 修改语句数量

| 类型 | 数量（关键字出现次数） |
|---|---|
| `ALTER TABLE` | 12 |
| `CREATE TABLE` | 4 |
| `CREATE INDEX` | 9 |
| `CREATE VIRTUAL TABLE` | 1 |
| `DROP TABLE` / `DROP INDEX` | 2 |
| **合计修改语句** | **28** |

---

## 4. Alembic 升级后 Schema 快照（临时库）

所有测量在 `tmp_path` 中执行，**不访问**默认用户数据库路径。

### 4.1 空数据库 → `alembic upgrade head`

| 项 | 值 |
|---|---|
| Revision | `j003_maintenance_control_plane`（= head） |
| 表数量（含 FTS 影子表，`sqlite_%` 除外） | **67** |
| 用户索引数量（非 `sqlite_%`） | **41** |
| Trigger 数量 | **3** |
| 关键业务表 | 含 `knowledge_items`、`blocks`、`wiki_pages`、`wiki_pages_v2`、`alembic_version`、`maintenance_*` 等 |

### 4.2 v1.9 风格（`j002_evidence_stale`）→ head

| 项 | 值 |
|---|---|
| 起始 Revision | `j002_evidence_stale` |
| 起始表数量 | **61** |
| 升级后 Revision | `j003_maintenance_control_plane` |
| 升级后表数量 | **67** |
| 新增表（j003） | `maintenance_jobs`, `maintenance_reviews`, `maintenance_schedules`, `maintenance_source_events`, `maintenance_dead_letters`, `maintenance_health_snapshots` |

---

## 5. 架构债务指标（当前，非本 Task 修改）

`python tools/report_closure_debt.py --json`（基线时刻）：

| 指标 | 值 |
|---|---|
| mcp_server_lines | 146 |
| mcp_server_tool_functions | 0 |
| mcp_tools_real_impl_count | 65 |
| database_instance_refs_outside_infra | **0** |
| get_active_container_refs_outside_whitelist | **15** |
| search_service_has_legacy_pipeline | false |
| search_service_has_verified_hybrid | false |
| raw_retriever_calls_search_service | false |
| answering_depends_on_verified_answer | false |
| alembic_env_reads_test_url | true |
| migration_tests_have_skip_paths | false |
| `--strict` 退出码（当前） | **0**（注意：`_strict_failures` **尚未**将 `get_active_container_refs_outside_whitelist > 0` 计为失败） |

**风险标记：** Spec WP1-T2 要求 `get_active_container_refs_outside_whitelist == 0`。当前为 15，若直接加断言且将 GAC 纳入 strict 判定，CI 会红。需在 WP1 中单独处理（清零引用或确认门禁语义），**不得**为通过 CI 放宽阈值。

---

## 6. 本基线不改变的行为

- 不调整 Database 启动顺序（WP2）
- 不拆除 `_SCHEMA` / `_migrate()`（WP5）
- 不改变 `allow_unstamped` 默认值（WP4）
- 不修改 Retrieval / Wiki / MCP / API 契约

---

## 7. 对应测试

```text
tests/migrations/test_runtime_schema_mutation_baseline.py
```

运行：

```bash
pytest tests/migrations/test_runtime_schema_mutation_baseline.py -q
```
