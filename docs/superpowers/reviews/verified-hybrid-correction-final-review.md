# Verified Hybrid 收束纠偏最终评审

> 状态：本地工程、迁移与确定性验收完成；待最终 push 的 required CI（Python matrix、Docker）成功后方可发布 v1.8.0。

## 结论

收束后的默认运行态是 Verified Hybrid：只有当前 revision 具备 review、validation、publish 与完整 Evidence 的 Claim 可以增强答案；任何 Gate、Wiki 或 Maintenance 故障都不能阻断 Raw 检索。来源事件进入持久 Maintenance 控制面，R4 仍需显式人工确认。

## 12 项发布问题

1. 默认档位是否 verified：是，配置解析与初始化测试覆盖。
2. 旧 `wiki_first` 是否保持 authoring：是，运行时兼容且迁移需显式 apply。
3. Gate 是否 fail-closed：是，缺少任一证明不会 Serving。
4. Evidence 是否可追溯：是，Serving validation 要求可解析 Evidence 与 hash。
5. 冲突是否披露：是，VerifiedAnswer 测试覆盖 conflict disclosure。
6. Source 更新是否立即保护：是，source-event/worker 回归覆盖 durable Job/Review。
7. Maintenance 是否另建事实库：否；仅协调 Canonical 与审阅记录。
8. R3/R4 是否可绕过：否；R3 仅 Draft/Review，R4 要确认。
9. Raw 是否在故障中可用：是，release eval 含 fallback guard，Windows smoke 走真实 Search/Ask。
10. 迁移是否可逆：配置 apply 备份 + 原子写 + 字节回滚；Canonical V2 有独立 rollback。
11. 静态与本地测试是否通过：以最终 Release Gate 输出为准，不以历史报告替代。
12. Wiki 是否证明提升真实模型质量：否；当前只证明确定性本地融合契约。真实 provider A/B 需要独立、可复现报告。

## 发布条件

不得仅凭本文发布。必须同时取得最新完整 pytest、Ruff、mypy、eval、frontend、Windows smoke，以及 GitHub Actions Python 3.10/3.11/3.12 与 Docker build/health 的绿色证据。
