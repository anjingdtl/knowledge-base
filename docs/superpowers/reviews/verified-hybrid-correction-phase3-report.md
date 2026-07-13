# Verified Hybrid 收束纠偏 Phase 3 报告

> 日期：2026-07-13
> 状态：通过，可进入 Phase 4
> 提交：待提交

## 行为变化

- 新增 SQLite Maintenance Control Plane：Source Event、Job、Review、Dead Letter、Health Snapshot 与 Schedule 均有独立表；它们只保存控制面元数据和对象 ID，不复制 Canonical Claim/Page 正文。
- `MaintenanceRepository` 成为容器运行时的 Job/Review/Dead Letter 事实存储；进程内 dict 仅保留给无数据库的隔离调用兼容，应用容器固定注入 SQLite。
- Source Event 幂等键更正为 `source:<event_type>:<knowledge_id>:<source_revision>`。相同 revision/tombstone 只入队一次，新 revision 必定创建新 Job。
- 增加数据库 lease 原语，先保证同一 pending/retry Job 只能由一个 worker 获得；执行 worker 在 Phase 4 接入。
- Health Snapshot 写入持久表；Dead Letter 会随 Job 状态原子投影，HTTP Source Event 可传递 `source_revision`。

## 验证

```text
python -m pytest tests/test_maintenance_repo.py tests/test_maintenance_center.py tests/test_maintenance_api.py tests/test_migration.py -q
30 passed

python -m ruff check src/repositories/maintenance_repo.py src/services/wiki_maintenance_service.py src/api/routes/maintenance.py tests/test_maintenance_repo.py tests/test_maintenance_center.py
All checks passed

python -m mypy src/repositories/maintenance_repo.py src/services/wiki_maintenance_service.py --ignore-missing-imports
Success: no issues found in 2 source files
```

## 兼容与回滚

迁移只新增控制面表和索引；不触碰 `wiki_claims`、Canonical 文件、Raw 数据或旧 Wiki 表。数据库 unavailable 时 Maintenance Service 继续安全降级为隔离内存实例，Raw Search 不受影响。Alembic downgrade 保留控制面历史，避免删除审计记录。

## 已知后续

Source Event Adapter、真实 worker/retry/cancel 传递、lease expiry 恢复和周期调度尚未接入正式索引主链路；这些属于 Phase 4，当前持久表与 lease 原语已作为其前置条件。
