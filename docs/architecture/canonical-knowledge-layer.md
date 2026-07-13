# Canonical Knowledge Layer

Wiki V2 Canonical Store：

- Page / Claim / Evidence / Relations  
- 状态机与 Revision  
- Projection / Outbox 为可读投影，**不是**第二事实库  

写入统一经 Repository 事务；Serving 只暴露 Gate 通过的 Claim。
