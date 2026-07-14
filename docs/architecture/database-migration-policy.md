# 数据库迁移策略（Phase-3 冻结）

> **版本：** v1.9.0  
> **状态：** 运行时 `_migrate()` 已冻结；新增 Schema **必须** 走 Alembic

## 规则

1. **`Database._migrate()` 仅用于历史兼容**  
   - 不得新增字段  
   - 不得新增表  
   - 不得增加表重建逻辑  
   - 允许的例外：纯注释 / 日志文案微调（需同步更新冻结测试清单）

2. **所有新 Schema 变化必须通过 Alembic revision**  
   - 路径：`alembic/versions/`  
   - 命令：`alembic revision --autogenerate -m "..."` → `alembic upgrade head`

3. **启动与写模式**  
   - 写模式启动前应检查 Alembic 版本（后续可增强 doctor / 启动钩子）  
   - 迁移失败时允许进入只读诊断模式（不在本期强制实现）

4. **Repository 渐进抽取**  
   - 一次只迁移一个领域  
   - `Database` Facade 暂时保留  
   - 不改变事务语义与 Canonical/Projection 权威关系

## 冻结门禁

自动化：`tests/test_database_migration_policy.py`  
对 `_migrate()` 中 `ALTER TABLE` / `CREATE TABLE` / `CREATE INDEX` / `DROP TABLE` 语句集合做快照比对。

## 回滚

- 不删除旧 Schema  
- 首次切换时不删除 `_migrate()`  
- Database Facade 保留  
- Alembic revision 必须可验证 upgrade
