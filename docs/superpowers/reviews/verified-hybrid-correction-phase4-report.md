# Verified Hybrid 收束纠偏 Phase 4 报告

> 日期：2026-07-13
> 状态：通过，可进入 Phase 5
> 提交：待提交

## 行为变化

- Path Index 与 Knowledge Workflow 在 Raw 索引成功后通过唯一 `MaintenanceEventAdapter` 投递创建、更新、删除事件；投递失败只记录 warning，绝不回滚 Raw 索引或影响检索。
- 旧 `wiki_rebuild_scheduler` 不再作为 Source Event 并行控制面；R1 工作先持久入队，再由 `MaintenanceWorker` 通过 SQLite lease 异步执行。
- Worker 支持 lease、重启后的过期 lease 恢复以及配置化 `maintenance.jobs.max_attempts` 的 dead-letter 上限。
- `MaintenanceScheduler` 使用数据库 schedule lease，确保多个实例对同一周期任务只有一个执行者；已提供 validation/parity/quality audit 等任务的通用调度基础。

## 验证

```text
python -m pytest tests/test_maintenance_repo.py tests/test_maintenance_worker.py tests/test_maintenance_scheduler.py tests/test_file_watcher.py tests/test_knowledge_workflow.py tests/test_wiki_rebuild_service.py tests/test_wiki_rebuild_scheduler.py -q
51 passed

python -m ruff check <Phase 4 touched files>
All checks passed

python -m mypy src/services/maintenance_event_adapter.py src/services/maintenance_worker.py src/services/maintenance_scheduler.py src/services/wiki_maintenance_service.py src/repositories/maintenance_repo.py --ignore-missing-imports
Success: no issues found in 5 source files
```

## 兼容与回滚

Raw index 和 Search 始终先于 Maintenance 发生；关闭/故障时只停止自动维护。回滚可停止 Worker/Adapter 并保留审计与已排队 Job，不改写 Canonical、Raw 或 Projection 内容。

## 已知后续

Phase 5 将补齐真实 Review Service、分页 API/CLI 与 Web Maintenance UI；尤其要把 R4 confirmation、Reject/Correct 规则、Validator/Parity publish gate 统一到共享服务。
