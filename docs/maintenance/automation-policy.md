# 自动化策略（风险分级）

| 等级 | 含义 | 默认 |
|---|---|---|
| R0 | 观测 / Dry-run | 自动 |
| R1 | 保护性、单调、可逆 | 自动（verified supervised） |
| R2 | 结构修复 | 策略允许时可自动 |
| R3 | 语义 Draft | 生成后待审阅 |
| R4 | 发布/删除/迁移/冲突裁决 | **人工确认** |

策略集中在 `MaintenancePolicyEngine`，禁止散落在 GUI/MCP/Scheduler。
