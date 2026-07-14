# 最终迁移治理验收报告 — v1.10.1

> Spec：`docs/superpowers/specs/05-final-migration-governance-spec.md`（WP6 最终验收与发布）  
> 基线 SHA：`b635ca7`（WP0 冻结，2026-07-14）  
> 分支：`master`  
> 验收日期：2026-07-14  
> **结论：允许发布 v1.10.1。**

---

## 1. WP0–WP5 合入确认

工作区在 WP6 开始前为 clean，以下 commit 已在 `master`：

| WP | Commit | 说明 |
|---|---|---|
| WP0-T1 | `5e9c141` | freeze final governance baseline |
| WP0-T2 | `eb73bfd` | schema fingerprint tool |
| WP1-T1 | `34b9827` | enable strict closure debt gate |
| WP1-T2 | `67c2c6a` | enforce strict zero assertions |
| WP1-T3 | `d6a6c84` | strict debt negative gates |
| WP2-T1 | `0c9e040` | non-mutating database bootstrap inspection |
| WP2-T2 | `f9cd78e` | enforce migration gate before `open_runtime` |
| WP2-T3 | `bb24ce2` | readonly `open_runtime` uses `mode=ro` |
| WP2-T4 | `7e528f6` | bootstrap gate order matrix |
| WP3-T1 | `90af210` | explicit `upgrade_to_head` runner |
| WP3 | `54e703e` | new databases exclusively through Alembic |
| WP4 | `6cc43d5` | safe legacy database stamp and migration workflow |
| WP5 | `7ea3d17` | remove runtime schema creation and migration authority |

---

## 2. WP6 修改范围

WP6 为验收 + 发布，不重构架构。唯一代码改动是**修复阻塞最终验收的明确缺陷**：

### 2.1 验收阻塞缺陷修复（ruff / mypy 退化 + 2 个 MCP 工具 `NameError` 类 bug）

WP2–WP5 开发期间累积的 lint/type 退化（基线 ruff 12 / mypy 3 → 验收前 ruff 143 / mypy 12），以及两个真实缺陷：

- **`preview_operation(op="ingest_file")`**（`src/mcp/tools/administration.py`）引用未导入的 `ingest_file`，调用即 `NameError` → 改为仿 `_preview_reindex_all` 的 lazy import。
- **`kb_capabilities.hidden_by_policy`**（`src/mcp/tools/retrieval.py`）因 `_HIDDEN_BY_POLICY` 未导入、`globals()` 守卫恒为 `False`，字段永远返回空列表 → 去除对未定义名的引用，字段保持 `[]`（工具层不改变既有返回，符合"不修改公开 MCP 契约"）。

类型修复均为最小标注（`cast` / `set()` 包裹 / 类型注解），不改变运行时逻辑：

- `src/mcp/server.py`：`_get_container` 返回 `cast(AppContainer, c)`；`_container` 加 `AppContainer | None` 注解；恢复 `parse_file, parse_url` re-export（`# noqa: F401`，测试 monkeypatch surface，曾被 ruff --fix 误删）。
- `src/services/search_service.py`、`src/retrieval/raw_retriever.py`、`src/mcp/tools/support.py`：`getattr`/动态调度返回值 `cast` 标注。
- `src/mcp/registration.py`：`compute_hidden_groups` 返回 `set(...)` 包裹（注解为 `set[str]`）。
- `src/core/service_groups.py`：`MaintenancePolicyEngine(cast(Any, config))`（`_cfg` 已 duck-type 处理 Config-like）。

### 2.2 ruff --fix 自动修复

`ruff check . --fix` 自动修复 136 项（F401 unused-import / I001 unsorted-imports / F811 redefined-while-unused）。手动修复 9 项（E402 ×5、F821 ×2、F841 ×2）。

### 2.3 文档

- 新建 `docs/release/v1.10.1-release-notes.md`
- 新建 `docs/migration/v1.10-to-v1.10.1-migration-governance.md`
- 更新 `docs/architecture/database-migration-policy.md`（v1.9.0 冻结版 → v1.10.1 最终治理）
- 更新 `PROGRESS.md`、`README.md`、`README_zh.md`、`config.example.yaml`
- 本验收报告

### 2.4 明确未修改

- Retrieval 排序 / 召回 / RRF / Rerank：未修改
- Wiki Serving Gate / Claim / Evidence 语义：未修改
- MCP 公开契约（工具名、参数、返回结构）：未修改
- 数据库 Schema：无新 Alembic revision（head 仍为 `j004_runtime_schema_parity`）

---

## 3. 验证结果（全量门禁）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 架构债务 strict | `python tools/report_closure_debt.py --strict` | ✅ exit 0（No residual debt） |
| Ruff | `ruff check .` | ✅ All checks passed（143 → 0） |
| MyPy | `mypy src` | ✅ Success: no issues in 264 files（12 → 0） |
| 迁移 + Storage + Alembic | `pytest tests/migrations/ tests/storage/ tests/test_alembic_baseline.py` | ✅ 62 passed |
| 契约（search/ask/wiki/mcp） | `pytest tests/test_public_*_contract.py tests/test_mcp_contract.py` | ✅ 70 passed, 1 skipped |
| 全量 pytest | `pytest tests/ -q` | ✅ 1861 passed, 2 skipped（修复回归后最终确认） |
| Retrieval Eval | `run_retrieval_eval.py --all --fake-embedding --baseline … --max-regression 0.05` | ✅ PASS |
| Hybrid Eval strict | `run_hybrid_eval.py --strict` | ✅ PASS（175 cases, raw/wiki/hybrid=1.000） |

债务门禁关键指标（strict clean）：
```
database_runtime_executes_schema = False
database_runtime_calls_migrate  = False
container_gate_after_database_open = False
allow_unstamped_default_true    = False
migration_tests_have_skip_paths = False
database_instance_refs_outside_infra = 0
get_active_container_refs_outside_whitelist = 0
```

> 注：全量 pytest 的 2 skipped 为既有平台/可选依赖 skip，迁移专项 0 skip；非"用 skip 隐藏迁移失败"。

---

## 4. Schema Fingerprint（MIG-001 / MIG-009 证据）

空数据库经 `upgrade_to_head` 升至 head：

- **Head revision**：`j004_runtime_schema_parity`
- **Upgrade**：`None → j004_runtime_schema_parity`（upgraded=True）
- **Tables**：77　**Indexes**：48　**Triggers**：3
- **Virtual tables (FTS)**：`agent_memory_fts`、`block_fts`、`chunk_fts`、`knowledge_fts`、`wiki_fts`、`wiki_pages_v2_fts`
- **Fingerprint SHA256**：`7e24f1844ae4a25e2609d711da93ade38c8720ba85523dcda93714a58c1740f9`
- 指纹不含业务数据行（`tools/schema_fingerprint.py` 只读 `mode=ro`）

空库启动与直接 `alembic upgrade head` 的 Schema Fingerprint 一致（`test_new_database_runtime_bootstrap.py` 覆盖）；重复启动幂等。

---

## 5. 迁移矩阵（WP6-T2）

由 `tests/migrations/` + `tests/storage/`（62 passed）覆盖：

| 场景 | 预期 | 结果 |
|---|---|---|
| DB 不存在 | Alembic 创建 Head DB | ✅ `auto_upgrade_empty` |
| 空文件 DB | Alembic 创建 Head DB | ✅ |
| 已在 Head | 正常启动，不改 Schema | ✅ 幂等 |
| Stamped Behind Head（写） | 写启动拒绝 | ✅ `MigrationGateError` |
| Stamped Behind Head（readonly） | 只读启动（`mode=ro`） | ✅ `write_allowed=False` |
| 已知 Unstamped v1.9 | 写启动拒绝，可 CLI 迁移 | ✅ detector + `db migrate` |
| 未知 Unstamped | 写启动拒绝，不自动 Stamp | ✅ |
| 迁移中断 | 自动恢复备份 | ✅ `*.failed-migration-*` 保留 |
| 重复 migrate | 幂等 | ✅ |
| 错误数据库路径 | 不触碰默认用户 DB | ✅ 只读检查不创建文件 |

---

## 6. 备份恢复

`shinehe db migrate` 流程：SQLite Backup API 备份 → `legacy_schema_detector` 识别 → 显式 stamp（detector 匹配）→ `upgrade_to_head` → Schema Fingerprint → `PRAGMA integrity_check` / `foreign_key_check`。Upgrade 失败自动恢复备份并保留失败副本（`tests/migrations/test_unstamped_legacy_migration.py` 覆盖中断恢复）。

---

## 7. 不变量核对（MIG-001 ~ MIG-012）

全部由自动化测试 / CI 门禁保护：MIG-001/009（Schema Fingerprint 一致）、MIG-002/003（runtime 不改 schema、gate 前置）、MIG-004/005/006（behind-head / unstamped / 未知 schema 拒绝）、MIG-007/008（备份 + 失败恢复）、MIG-010（strict CI）、MIG-011（契约不变）、MIG-012（eval 不退化）。

---

## 8. 已知风险

1. **ruff --fix 误删 re-export**：本轮 `ruff check . --fix` 曾将 `server.py` 的 `parse_file, parse_url`（注释标明 `re-export for tests/patches`）当作 F401 删除，导致 2 个 monkeypatch 测试失败。已恢复并加 `# noqa: F401`。后续若再跑 `ruff --fix`，需警惕未被测试覆盖的 re-export 被静默删除——建议为测试 patch surface 统一登记 `__all__` 或 `# noqa: F401`（独立 Issue）。
2. **`kb_capabilities.hidden_by_policy` 字段**：本轮保持工具层返回 `[]`（最小修复、不改契约）。该字段在 MCP 工具层未接通 server bootstrap 的 `_registration.hidden_by_policy`，作为独立 MCP 工具改进 Issue，不扩入迁移治理范围。
3. **回滚**：代码回滚至 v1.10.0 安全，但 v1.10.0 可能重跑 `_SCHEMA + _migrate()`；已升级到 head 的数据库保持向前兼容，不自动降级。

---

## 9. Commit

- WP6 发布提交（本报告所属）：`release: v1.10.1 final migration governance closure`

（未创建 Git Tag、未创建 GitHub Release、未推送远程。）
