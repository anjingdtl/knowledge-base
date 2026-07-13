# 维护中心

统一控制面：健康、来源事件、Impact Plan、Job、Review、审计。  
**不**编辑 Raw Source，**不**成为第二 Claim 库。

## 入口

| 面 | 路径 |
|---|---|
| Service | `WikiMaintenanceService` + `MaintenancePolicyEngine` |
| API | `/api/maintenance/health|jobs|reviews|source-events|...` |
| CLI | `shinehe maintenance health|jobs|reviews|source-event|...` |
| Web | 维护中心页 — Wiki 健康与待审阅 |

关闭 `maintenance.enabled` 后查询（Raw Search）仍正常。
