# 仓库清理审计记录

> 日期：2026-06-13
> 范围：文档、脚本、图标、临时文件和生成文件
> 原则：有运行、构建、测试、迁移或数据恢复价值的文件不删除

## 已删除

| 文件 | 原因 | 影响确认 |
| --- | --- | --- |
| `.superpowers/brainstorm/...` | 误提交的本地 brainstorm HTML、PID 和状态文件 | 已被 `.gitignore` 排除，无代码或构建引用 |
| `requirements.txt` | 文件自身声明已弃用，依赖以 `pyproject.toml` 为唯一来源 | Docker、CI 和安装命令均使用 `pyproject.toml` |
| `docs/update_manual.py` | 只修改已不存在的 `ShineHeKnowledge_UserManual_v1.0.0.docx` | 当前手册由 `scripts/build_docs.py` 生成 |
| `icon/knowledge_old.ico` | 品牌升级前的旧图标 | 无代码、构建或安装脚本引用 |
| `docs/知识库架构与功能分析和优化方案0612.docx` | 未跟踪的阶段性输入文档，内容已落入 Markdown Spec/Plan | 无引用，且 `.docx` 本来就被忽略 |
| `.codex_tmp/` | 文档读取与渲染临时目录 | 仅包含本次审计中间产物 |

## 已归档

| 目录 | 内容 | 处理理由 |
| --- | --- | --- |
| `docs/archive/2026-06-04-structured-graph-rag/` | Logseq、Structured/Graph RAG、MCP-first、安全闭环 Spec/Plan/Progress | 已完成，保留架构决策和迁移背景 |
| `docs/archive/2026-06-12-full-platform-upgrade/` | 五阶段审计优化 Spec/Plan/Progress | 已完成，不再作为当前待办 |
| `docs/archive/2026-06-13-gui-performance/` | 旧根进度和 GUI 性能专项 | 保留性能根因和修复证据 |

归档文件与清理前 Git 内容做了规范化换行后的逐字比较，11 个文件全部一致。

## 已保留

### MCP 文档

`docs/mcp/` 仍被 contract tests 和当前 Prompt 流程使用。

### 当前 Spec/Plan

- `docs/superpowers/specs/2026-06-13-mcp-local-retrieval-focus-design.md`
- `docs/superpowers/plans/2026-06-13-mcp-local-retrieval-focus.md`

### 数据库迁移和恢复脚本

- `scripts/migrate_to_block_graph.py` 被 `tests/test_migration.py` 直接导入，不能移动或删除。
- 其他旧迁移和恢复脚本没有运行时引用，但可能用于旧数据库升级或用户数据救援。
- 这些脚本保留在原路径，风险和用途见 `scripts/README.md`。

### 构建脚本

`build_docs.py`、`build_windows.py`、`build_docker.py` 仍是 `CLAUDE.md` 中定义的发布入口。

## 已更新

- 根 `PROGRESS.md` 改为唯一当前进度入口。
- 新增 `docs/README.md` 和 `docs/archive/README.md`。
- 新增 `scripts/README.md`，区分日常、迁移和恢复脚本。
- 修正 README、CLAUDE、示例知识包和手册生成器中的 MCP 工具、资源、Prompt 和运行模式数量。
- 补充缺失的 MIT `LICENSE`，修复 README 的失效链接。

## 验证结果

- 当前 Markdown 链接检查：9 个入口文档，0 个缺失链接。
- 删除资产引用检查：通过。
- Python `compileall`：通过。
- 前端 `npm run build`：通过。
- DOCX 用户手册生成：通过。
- 定向 Python 回归：
  - `94 passed, 1 skipped`
  - `45 passed`
  - `74 passed`
- 完整 `pytest tests -q`：运行 5 分钟后超时，因此不记为通过。
