# ShineHeKnowledge 当前状态

> 最后更新：2026-06-13
> 源码版本：`src/version.py` 中的 `1.2.0`
> 当前分支：`master`
> 当前方向：收束为本地优先的 MCP 高精准知识检索引擎

## 权威文档

- [当前优化规格](docs/superpowers/specs/2026-06-13-mcp-local-retrieval-focus-design.md)
- [当前实施计划](docs/superpowers/plans/2026-06-13-mcp-local-retrieval-focus.md)
- [MCP 使用文档](docs/mcp/)
- [历史设计与已完成计划](docs/archive/README.md)

除上述当前规格和计划外，归档目录中的文档只用于追溯，不代表当前待办。

## 已完成的主要能力

- SQLite、FTS5、sqlite-vec 与 Block-first 存储。
- 向量检索 + 全文检索 + RRF 融合。
- query rewrite、reranker、Parent-Child、Evidence Compression。
- Block 级来源、source graph、结构化查询与 Agentic Router。
- MCP envelope、写操作策略、dry-run、审计、soft delete 与 undo。
- 大文件异步导入和任务查询。
- 51 个原始 MCP 工具、51 个命名空间别名、3 个资源、5 个 Prompt。
- GUI、REST API、Web 客户端、Docker、Windows 服务和安装脚本。
- CI：Python lint/test、前端构建和 Docker 构建。

## 当前改造

`MCP Local Retrieval Focus` 尚未开始编码，计划分为：

1. 冻结 MCP 与检索基线。
2. 增加本地初始化和统一 CLI。
3. 增加目录索引与文件监听。
4. 精简默认 MCP 工具配置档。
5. 统一检索候选分数和 Citation。
6. 增加可选本地 reranker provider。
7. 建立真实 golden source 的 Eval 门禁。
8. 重写 README、Demo 和发布文档。

## 文档清理记录

2026-06-13 完成首轮仓库清理：

- 旧的 Structured/Graph RAG、MCP-first 和全平台升级方案移入 `docs/archive/`。
- 删除失效的手册补丁脚本、弃用的 `requirements.txt`、旧图标和误提交的 `.superpowers` 临时文件。
- 保留被测试引用的迁移脚本。
- 保留可能用于用户数据恢复的一次性脚本，并在 `scripts/README.md` 标明风险和用途。

## 验证原则

- MCP/RAG 改动优先运行对应 contract 和 targeted regression。
- 数据库迁移必须运行 `tests/test_migration.py`。
- 前端改动必须运行 `npm --prefix client run build`。
- 发布前再运行完整测试、Docker 构建和实际 GUI/MCP 启动验证。
- 测试结果只记录当次真实执行结果，不从历史文档复制通过数量。
