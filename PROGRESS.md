# ShineHeKnowledge 当前状态

> 最后更新：2026-06-13
> 源码版本：`src/version.py` 中的 `1.3.0`
> 当前分支：`master`
> 当前方向：本地优先的 MCP 高精准知识检索引擎

## 权威文档

- [当前优化规格](docs/superpowers/specs/2026-06-13-mcp-local-retrieval-focus-design.md)
- [当前实施计划](docs/superpowers/plans/2026-06-13-mcp-local-retrieval-focus.md)
- [MCP 使用文档](docs/mcp/)
- [高级功能](docs/advanced-features.md)
- [工具配置档迁移指南](docs/migration/mcp-tool-profiles.md)
- [历史设计与已完成计划](docs/archive/README.md)

除上述当前规格和计划外，归档目录中的文档只用于追溯，不代表当前待办。

## v1.3.0 MCP Local Retrieval Focus — 已完成

本次改造将 ShineHeKnowledge 收束为默认工具面精简、可一键本地初始化、可持续索引目录、引用可解释且质量可量化的 MCP 本地知识检索引擎。

### 已完成模块

| 模块 | 交付 | 验证 |
|------|------|------|
| M0 基线冻结 | MCP 工具 legacy snapshot、检索回归基线 | `test_mcp_tool_profiles.py`、`test_retrieval_candidate_contract.py` |
| M1 工具配置档 | core/extended/admin/full/legacy profiles、声明式 registry | `test_mcp_tool_profiles.py` (12 tests) |
| M2 CLI 初始化 | `shinehe init/index/watch/doctor/mcp`、provider presets | `test_cli.py`、`test_project_setup.py`、`test_doctor.py`、`test_provider_presets.py` |
| M3 目录增量索引 | indexed_files 表、PathIndexService、FileWatcher、IndexScheduler | `test_path_indexer.py`、`test_indexed_file_repo.py`、`test_file_watcher.py`、`test_index_scheduler.py` |
| M4 检索与引用统一 | RetrievalCandidate、Citation、CitationBuilder、score breakdown | `test_retrieval_candidate_contract.py`、`test_citation_builder.py` |
| M5 本地 reranker | API/local/LLM/disabled 四种 provider、lazy load、失败降级 | `test_reranker_providers.py` (31 tests) |
| M6 Eval 质量门禁 | fixture、golden source、Recall/MRR/nDCG、CI 门禁 | `test_eval_datasets.py`、`test_retrieval_eval_runner.py`、`run_retrieval_eval.py --all` |
| M7 文档与 Demo | README 重写、迁移指南、advanced-features、demo 脚本 | `test_mcp_docs_prompts.py`、`test_demo_local_retrieval.py` |

### 检索 Eval 基线指标

| 数据集 | Recall@5 | MRR | nDCG@10 |
|--------|----------|-----|---------|
| retrieval_code | 1.0000 | 1.0000 | 1.0000 |
| retrieval_table | 1.0000 | 1.0000 | 0.9779 |
| retrieval_zh | 0.6000 | 0.3400 | 0.4036 |
| retrieval_no_answer | — | — | — (No-Answer: 0.6667) |

### 延后项

- 本地 reranker `sentence-transformers` extra 未在 CI 中实际加载模型（仅验证 lazy load 和 fallback）
- `retrieval_zh` 中文检索指标偏低，后续可通过优化分词和 query rewrite 提升
- Docker MCP 镜像构建未在本次验证（需要 Docker 环境）
- GUI 未适配 tool profile 切换（GUI 仍使用完整工具集）

### 已知兼容风险

- 老配置未设置 `mcp.tool_profile` 时自动走 `legacy`，行为不变
- `shinehe-mcp` 入口保留，不破坏已有客户端配置
- `kb_capabilities` 新增 `tool_profile`/`visible_tools`/`hidden_groups` 字段

### 安全加固（2026-06-13）

- **SSRF 防护**：`parse_url()` 添加 DNS 解析后 IP 检查，阻止对内网/回环/链路本地地址的请求，限制最大重定向 5 次
- **安全响应头**：API 层添加 `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: strict-origin-when-cross-origin`、`Permissions-Policy`
- **CORS 安全**：wildcard origins 时自动禁用 `allow_credentials`，防止 token 泄露
- **错误日志**：3 处裸 `except: pass` 改为 `except: logger.debug(...)`，避免静默吞掉异常
- **SQL 审查**：确认所有 f-string SQL 中的变量均为内部硬编码或已白名单验证，无注入风险

## 既有能力（v1.2.0 及之前）

- SQLite、FTS5、sqlite-vec 与 Block-first 存储。
- 向量检索 + 全文检索 + RRF 融合。
- query rewrite、reranker、Parent-Child、Evidence Compression。
- Block 级来源、source graph、结构化查询与 Agentic Router。
- MCP envelope、写操作策略、dry-run、审计、soft delete 与 undo。
- 大文件异步导入和任务查询。
- 51 个原始 MCP 工具（legacy 模式下全部可用）、3 个资源、5 个 Prompt。
- GUI、REST API、Web 客户端、Docker、Windows 服务和安装脚本。
- CI：Python lint/test、前端构建和 Docker 构建。

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
