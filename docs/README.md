# 文档索引

## 当前方向（权威入口）

> 产品版本：**v1.11.0** · 源码：`src/version.py` · 状态见 [PROGRESS](../PROGRESS.md)

### 界面定位（当前有效）

- **主界面：PySide6 桌面 GUI**（`python main.py`）。面向日常运营使用，持续维护产品能力与视觉体验。
- **备用界面：`client/` React Web UI**。仅保留有限的本地/API 访问能力，现阶段暂停维护；除非有明确的新产品决策，不新增功能或做视觉迭代。

### 桌面 GUI 界面修复（2026-07-23，当前）

- GUI 改用 Windows 中文无衬线字体栈，侧栏品牌以双行锁定展示，避免英文品牌名截断
- 浅/深主题统一为湖蓝主色、暖金强调色；导航、状态和输入区域均沿用同一色彩语义
- 运行时视觉复核需在安装 `.[gui]`（PySide6）的环境中执行

### 三项基础问题修复（2026-07-16，已发布 v1.10.5）

- [v1.10.5 发布说明](release/v1.10.5-release-notes.md)
- [三项基础问题修复报告](reports/production-pilot-foundation-three-fixes-2026-07-16.md)
- [Provider 运行时隔离图](architecture/provider-runtime-isolation-map.md)
- [执行 Spec](superpowers/specs/ShineHeKB_三项基础问题修复_Spec.md)
- 产物目录：`artifacts/foundation-three-fixes/`
- **结论：196 条候选尚未完成真实双人审核，不能进入独立全量验收**

### 生产试点最终验收（2026-07-16，已发布 v1.10.4）

- [v1.10.4 发布说明](release/v1.10.4-release-notes.md)
- [生产试点最终验收报告](reports/mcp-production-pilot-final-validation-2026-07-16.md)
- [门禁修复 Delta 报告](reports/mcp-production-pilot-gate-remediation-2026-07-16.md)
- [生产试点最终验收 Spec](superpowers/specs/ShineHeKB_MCP_生产试点前最终验收收尾_Spec.md)
- [门禁修复 PLAN](superpowers/plans/2026-07-16-production-pilot-gate-remediation.md)
- 产物目录：`artifacts/production-pilot-final-validation/`
- **结论：未达生产试点门槛**（可受控内测）

### MCP 最终收口（2026-07-16，已发布 v1.10.3）

- [v1.10.3 发布说明](release/v1.10.3-release-notes.md)
- [MCP 最终收口报告](reports/mcp-final-closure-2026-07-16.md)
- [MCP 最终收口 Spec](superpowers/specs/ShineHeKB_MCP_最终收口与本地真实MCP验证_Spec.md)
- 产物目录：`artifacts/final-closure/`

### 可维护性三期（2026-07-14，已发布）

- [v1.9.0 发布说明](release/v1.9.0-release-notes.md)
- [v1.8 → v1.9 迁移摘要](migration/v1.8-to-v1.9-maintainability.md)
- [弃用登记](migration/deprecation-register.md)
- [数据库迁移策略（`_migrate` 冻结）](architecture/database-migration-policy.md)
- Spec / Plan / 验收：
  - [一期 Spec](superpowers/specs/01-maintainability-phase-1-contract-isolation.md) · [验收](superpowers/reviews/maintainability-phase1-acceptance.md) · [v1.8.1](release/v1.8.1-release-notes.md)
  - [二期 Spec](superpowers/specs/02-maintainability-phase-2-retrieval-wiki.md) · [验收](superpowers/reviews/maintainability-phase2-acceptance.md) · [v1.8.2](release/v1.8.2-release-notes.md)
  - [三期 Spec](superpowers/specs/03-maintainability-phase-3-application-infrastructure.md) · [Plan](superpowers/plans/2026-07-14-maintainability-phase-3-application-infrastructure.md) · [验收](superpowers/reviews/maintainability-phase3-acceptance.md)

### Verified Hybrid 产品基线

- [融合收束开发规格（Verified Hybrid）](ShineHeKnowledge%20融合收束开发规格说明.md)
- [v1.8.0 发布说明（收束纠偏）](release/v1.8.0-release-notes.md)
- [v1.7.0 发布说明](release/v1.7.0-release-notes.md)
- [Verified Hybrid 最终评审](superpowers/reviews/verified-hybrid-final-review.md)
- [v1.6→v1.7 迁移](migration/v1.6-to-v1.7-verified-hybrid.md)
- [v1.7→v1.8 迁移](migration/v1.7-to-v1.8-convergence-correction.md)
- [Getting Started：verified / authoring / evidence-only](getting-started/verified-mode.md)
- [Hybrid 查询管线](architecture/hybrid-query-pipeline.md)
- [Wiki Serving 不变量](architecture/wiki-invariants.md)
- [Serving Gate](wiki/serving-gate.md)
- [维护中心](maintenance/maintenance-center.md)
- [Hybrid Eval](evaluation/hybrid-knowledge.md)
- [项目当前状态](../PROGRESS.md)

### 历史方案（只读参考）

- [Verified Hybrid Phase 0–6 报告](superpowers/reviews/verified-hybrid-baseline.md)
- [Canonical Wiki V2 纠偏与续建总方案](superpowers/plans/2026-07-08-canonical-wiki-v2-correction-and-continuation.md)
- [Canonical Wiki V2 Phase 4C Primary 执行计划](superpowers/plans/2026-07-09-canonical-wiki-v2-phase4c-primary-plan.md)
- [Canonical Wiki V2 Phase 4C Primary 验收 Review](superpowers/reviews/2026-07-13-phase4c-primary-review.md)
- [Canonical Wiki V2 Phase 4C 交接单（2026-07-13）](superpowers/handoffs/2026-07-13-canonical-wiki-v2-phase4c-handoff.md)
- [Karpathy Wiki-First 对齐 Spec(第一阶段)](superpowers/specs/2026-07-02-knowledge-base-karpathy-wiki-first-design.md)
- [Karpathy Wiki-First 第二阶段 Spec(检索执行层)](superpowers/specs/2026-07-02-knowledge-base-karpathy-wiki-first-phase2-design.md)
- [Karpathy Wiki-First 第二阶段 Plan(W1-W4)](superpowers/plans/2026-07-02-knowledge-base-karpathy-wiki-first-phase2.md)
- [MCP 本地高精准检索收束 Spec](superpowers/specs/2026-06-13-mcp-local-retrieval-focus-design.md)
- [MCP 本地高精准检索实施 Plan](superpowers/plans/2026-06-13-mcp-local-retrieval-focus.md)

`docs/superpowers/specs/` 和 `docs/superpowers/plans/` 只保留当前有效方案。已完成或被替代的方案统一放入 `docs/archive/`。

## MCP 文档

- [Agent 使用指南](mcp/agent-usage.md)
- [工具契约](mcp/tool-contract.md)
- [查询 DSL](mcp/query-dsl.md)
- [安全与撤销](mcp/safety-and-undo.md)
- [异步导入任务](mcp/ingest-jobs.md)

## 历史归档

- [归档说明](archive/README.md)

归档文档用于解释历史决策和迁移背景，不应作为当前实施入口。

## 生成文档

`scripts/build_docs.py` 生成版本化 DOCX 用户手册。生成的 `.docx` 被 `.gitignore` 忽略，不应提交到仓库。
