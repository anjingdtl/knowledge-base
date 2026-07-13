# Phase 5 阶段报告：自动化维护中心

> 日期：2026-07-13  
> Spec：§11 / §12.14–§12.20 / Phase 5

---

## 1. 修改文件列表

| 文件 | 变更 |
|---|---|
| `src/services/maintenance_policy.py` | **新增** MaintenancePolicyEngine（R0–R4） |
| `src/services/wiki_maintenance_service.py` | **新增** 维护编排：事件/Job/Review/Health |
| `src/core/container.py` | 懒加载 maintenance 服务 |
| `src/api/routes/maintenance.py` | Health/Jobs/Reviews/Source-events/Drafts/R4 API |
| `src/cli.py` | `shinehe maintenance …` |
| `config.example.yaml` | `maintenance.*` 默认配置 |
| `client/src/views/MaintenanceView.tsx` | Wiki 健康 + 审阅列表 |
| `tests/test_maintenance_center.py` | **新增** |

## 2. 行为变化

- Source Event → Impact Plan（复用 rebuild）→ Policy → R1 自动保护 / R3 审阅 / R4 人工
- R3 只进 Review，不发布
- R4 无 `human_confirmed` 不得执行
- 关闭 maintenance 不影响 Raw Search
- Health Snapshot 聚合 Claim / Review / Job 计数

## 3. 验收（本阶段范围）

| 项 | 状态 |
|---|---|
| Impact Plan 可 dry-run | ✅（rebuild.plan_rebuild） |
| R1 策略 auto | ✅ |
| R3 draft → review | ✅ |
| R4 无确认 block/review | ✅ |
| Job 可 cancel / retry / dead_letter | ✅（进程内） |
| 维护中心故障不影响 Raw | ✅ |
| 至少一个 UI | ✅ Web MaintenanceView |

## 4. 已知限制

- Job/Review 为进程内存储（可后续落 SQLite）；不设第二套 Claim 事实库
- 周期 cron 调度未单独 daemon 化（依赖现有 rebuild scheduler / 事件入口）

## 5. 回滚

`git revert <phase5-sha>`；配置键可选，缺省走默认策略。
