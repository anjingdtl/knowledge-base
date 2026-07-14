# 数据库迁移策略（v1.10.1 最终治理）

> **版本：** v1.10.1  
> **状态：** Alembic 是运行时唯一的 Schema 创建与升级权威；运行时不再修改 Schema

## 权威规则

1. **新数据库只能由 Alembic 创建**  
   - 文件不存在或完全空库 → `storage.migration_gate.auto_upgrade_empty=true`（默认）触发 `upgrade_to_head`  
   - 不得通过运行时 `_SCHEMA` 创建正式新库

2. **运行时 Database 不修改 Schema**  
   - 生产启动路径不执行 `_SCHEMA`、不调用 `_migrate()`、不 `ALTER/CREATE TABLE/INDEX`、不重建 FTS  
   - `_migrate()` 已迁至 `src/compatibility/runtime_schema_migrate.py`，仅供历史迁移命令使用，生产启动不导入  
   - 新增 Schema 必须只通过 Alembic revision（`alembic/versions/`）

3. **Migration Gate 前置于 Database 打开**  
   - `inspect_database_bootstrap()` 用只读连接读取 `sqlite_master` / `alembic_version` 并计算 Gate Decision  
   - Gate 失败时 `Database.open_runtime()` 不会被调用  
   - 实现：`src/storage/database_bootstrap.py`、`src/storage/startup_gate.py`

4. **启动与写模式**  
   - 写模式：当前 revision 落后 head → **拒绝启动**（`MigrationGateError`）  
   - 只读诊断：`storage.readonly=true` 或 `SHINEHE_READONLY=1`（使用 `file:...?mode=ro`，不创建文件/WAL）  
   - 强制门禁（含 pytest）：`SHINEHE_ENFORCE_MIGRATION_GATE=1`  
   - 跳过门禁（仅紧急诊断，不推荐生产）：`SHINEHE_SKIP_MIGRATION_GATE=1`

5. **Unstamped 旧库**  
   - `allow_unstamped` 默认 `false`：非空 Unstamped 库写启动被拒绝  
   - 已知历史版本（v1.8.x / v1.9.x）经 `src/storage/legacy_schema_detector.py` 识别后，可通过 `shinehe db migrate` 安全迁移  
   - 未知 Schema **不得自动 Stamp**；`shinehe db stamp` 必须显式 `--from-version` 且 Detector 匹配

6. **db CLI（`src/storage/migration_cli.py`）**  
   - `shinehe db status` / `backup` / `verify` / `migrate` / `stamp`  
   - 迁移流程：备份（SQLite Backup API）→ 历史识别 → Stamp → Alembic upgrade head → Schema Fingerprint → Integrity Check  
   - 迁移失败自动恢复备份，失败副本保留为 `<db>.failed-migration-<ts>.sqlite`

7. **Schema 等价性与幂等**  
   - 空库启动与直接 `alembic upgrade head` 的 Schema Fingerprint 必须一致  
   - 重复启动幂等，不改 Schema

## 冻结门禁

- 架构债务：`python tools/report_closure_debt.py --strict`（CI 非零退出码阻断回归）  
- 迁移矩阵：`pytest tests/migrations/ tests/storage/ tests/test_alembic_baseline.py`  
- Schema 指纹：`python tools/schema_fingerprint.py --db <path> --json`

## 回滚

- 不删除旧 Schema；不执行自动 Downgrade  
- 代码回滚至 v1.10.0 安全（v1.10.0 可能重跑 `_SCHEMA + _migrate()`，需在发布说明中警告）  
- 已升级到 head 的数据库保持向前兼容，不因代码回滚而降级
