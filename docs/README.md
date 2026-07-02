# 文档索引

## 当前方向

- [Karpathy Wiki-First 对齐 Spec(第一阶段)](superpowers/specs/2026-07-02-knowledge-base-karpathy-wiki-first-design.md)
- [Karpathy Wiki-First 第二阶段 Spec(检索执行层)](superpowers/specs/2026-07-02-knowledge-base-karpathy-wiki-first-phase2-design.md)
- [Karpathy Wiki-First 第二阶段 Plan(W1-W4)](superpowers/plans/2026-07-02-knowledge-base-karpathy-wiki-first-phase2.md)
- [Karpathy Wiki-First W1 地基 Plan](superpowers/plans/2026-07-02-knowledge-base-karpathy-wiki-first-w1.md)
- [MCP 本地高精准检索收束 Spec](superpowers/specs/2026-06-13-mcp-local-retrieval-focus-design.md)
- [MCP 本地高精准检索实施 Plan](superpowers/plans/2026-06-13-mcp-local-retrieval-focus.md)
- [项目当前状态](../PROGRESS.md)

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
