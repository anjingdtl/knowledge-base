# ShineHeKnowledge 当前状态

> 最后更新：2026-06-25
> 源码版本：`src/version.py` 中的 `1.4.0`
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

## v1.4.0 测试报告 BUG 修复 — 已完成 (2026-06-25)

基于 `shineheKB-MCP测试报告-30轮-v1.4.0.docx` 的 5 个 Bug 做代码层根因定位并全量修复。Commit: `1f79f7f`

### 修复清单

| Bug | 严重度 | 结论 | 主要改动 |
|-----|--------|------|---------|
| BUG-1 P0 route_query 路由退化 | 严重 | 路由分类器工作正确（测试查询均为语义查询），但 hybrid 模式缺少 query_spec | `route_engine.py`: ①EmbeddingRouter 阈值 0.75→0.60 ②LLMRouter/PlanetaryRouter hybrid 返回附带 fulltext query_spec |
| BUG-4 P2 标签覆盖率 3.7% | 中等 | 数据层面问题，需自动化补标手段 | `health.py`: 新增 `_get_kb_domain_summary()`、分级告警、recommendations 字段；`mcp_server.py`: 新增 `auto_tag` LLM 批量打标工具；`aliases.py`: 注册别名 |
| BUG-5 P1 ask_with_query 参数 BC | 中等 | 旧参数 `query` 不再被接受 | `mcp_server.py`: 新增 `query` 向后兼容别名（等价于 `search_query`） |
| BUG-6 P1 structured_query 参数 BC | 中等 | 旧参数 `filters` 不再被接受 | `mcp_server.py`: 新增 `filters` 向后兼容别名（等价于 `query_dsl`） |
| BUG-7 P0 ask 返回"未找到" | 中等 | 弱相关召回被 score_threshold 过滤 + 空上下文 LLM 误判 | `config.yaml`: score_threshold 0.35→0.25；`rag_pipeline.py`: GenerateStage 空上下文注入知识库领域概览兜底 |

### 待后续关注

- BUG-1 修复后需重新测试 route_query 在结构化场景下的表现
- auto_tag 工具的 LLM token 成本需评估（批处理上限 100 条/次）
- score_threshold 降低可能引入噪声结果，需观察实际召回质量

## 第5轮稳定性测试报告全量修复 — 已完成（2026-06-22）

基于 `docs/ShineHe_KB_MCP_稳定性测试报告第5轮.md` 的 8 个 Bug 做代码层根因定位（含交叉审查）并全量修复。

### 修复清单

| Bug | 结论 | 主要改动 |
|-----|------|---------|
| BUG-1 P0 LLM 认证 | 代码改进 + 部署配 key | `llm.py`/`embedding.py` 移除静默 `no-key` 兜底、加精确诊断与一次性告警；`container.py` 启动期 key 缺失检测；`windows_service.py` 启动时显式 `Config.load()` + 注入 secret 到进程环境，缺失时记 Windows 事件日志 |
| BUG-2 P0 Vector null | 与 BUG-1 同源 + 可观测性 | `hybrid_search._vector_search` 改返回 `(results, warnings)`（用返回值而非实例属性），降级原因透传到候选 `warnings`，keyword 通道独立性绝对不破坏；不改 `vector_score=None` 语义 |
| BUG-3 P1 route_query | 补完 3 个遗留缺陷 | `agentic_router`：graph 分支 mode 改 structured（消除 mode/query_spec 矛盾）；`_is_structured` 收紧为强信号子集（避免"哪些/状态"误命中语义查询）；`_try_llm` 加 debug 日志；恢复强断言 |
| BUG-7 P2 file_type | 真 bug 已修 | `file_graph.create_page` 补 file-type 键（原被丢弃致 sync_page fallback "md"） |
| BUG-8 P3 重复 | 已修，"未知"订正 | `path_indexer._ingest_file` 加 content_hash 幂等去重（与 `mcp_server.create` 一致）。"未知"标题是展示层回退非导入问题，不改 |

BUG-4/5/6 在 round 1/4（commit `82d2a99`/`fe19524`）已有代码层修复，报告基于旧快照；本轮补回归测试锁死。

### 验证

| 门禁 | 结果 |
|------|------|
| Python 全量测试 | `887 passed, 1 skipped in 267.54s` |
| 改动模块集成测试 | test_core/search/search_service/mcp_server/indexer/reranker_providers/mcp_stability/mcp_rag_full/query_revolution_phase3/llm_configuration/file_graph/path_indexer 零回归 |

### 部署侧待办（用户必做）

BUG-1/BUG-2 的 RAG 与语义搜索完全恢复，需在 Windows Service 环境注入 API Key（SYSTEM 账户读不到交互式账户的 keyring）：

```
setx SHINEHE_LLM_API_KEY <KEY> /M
setx SHINEHE_EMBEDDING_API_KEY <KEY> /M
```

重启 `ShineHeMCP` 服务后，`ops_ping` 的 `api_keys.llm/embedding` 应为 true，`vector_index.coverage` 应 > 0；历史 PDF 条目 file_type 需 `reindex_all` 修正（BUG-7）。

## v1.3.1 全仓库健康审查 — 已完成

本轮基于当前规格与实施计划，对源码、测试、GUI、MCP、索引、引用、评测、构建脚本和发布资料进行了全量审查与修复。

### 主要修复

- 修复目录索引、异步任务、文件解析、SQLite 图存储、GUI worker 清理和 MCP 工具契约中的实际缺陷。
- 修复 Block 元数据被兼容 chunk 写入覆盖的问题，确保 `source_path`、Block ID 和 Citation 可追溯。
- 更新过时的容器属性调用、PySide6 枚举和 pikepdf 参数，删除无效导入与旧式异常处理。
- 为 `Database` 兼容元类增加 mypy 插件，清零源码类型错误。
- 修复 Windows GBK 控制台下 Demo 状态符号崩溃，并将 Demo 测试隔离为确定性 fake 服务。
- 将 `scripts/` 纳入 Ruff 健康门禁，清理构建、迁移、诊断、压力和数据救援脚本。
- 修复检索评测中“引用完整性字段定义但从未计算”的死指标，建立真实非零 baseline。
- 修复 Linux CI 的 GUI 系统依赖、跨平台类型边界、缺失运行时依赖和依赖本机配置的测试，升级 Actions 到 Node 24 运行时版本。

### 发布前验证

| 门禁 | 结果 |
|------|------|
| Python 全量测试 | `828 passed, 2 skipped in 845.14s` |
| GitHub Actions Test | Ubuntu / Python 3.12：`828 passed, 2 skipped in 145.12s` |
| Ruff | `src tests evals tools scripts` 全绿 |
| mypy | `157 source files`，无错误 |
| Python compileall | `src scripts tests evals tools` 通过 |
| Web 客户端 | TypeScript + Vite 生产构建通过 |
| 检索评测 | CI 同款 fake-embedding 门禁通过 |
| 本地检索 Demo | `initial_hit=true`、`incremental_update=true`、`citation_complete=true` |
| 远端 CI | Test、Lint、Frontend Build、Retrieval Eval、Docker Build 五项全绿 |

### 当前检索基线

| 指标 | 综合结果 |
|------|----------|
| Recall@5 | 0.8667 |
| MRR | 0.7800 |
| nDCG@10 | 0.7938 |
| No-Answer Accuracy | 0.6667 |
| Citation Location Completeness | 1.0000 |

### 已知边界

- `retrieval_zh` Recall@5 仍为 `0.6000`，No-Answer Accuracy 为 `0.6667`，均已进入非零 baseline，后续优化不得回退超过 5%。
- 本机没有 Docker CLI，无法执行本地镜像构建；Dockerfile 由 GitHub Actions 的 `docker` job 继续作为远端发布门禁。

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

### SQLite 图谱存储收束（2026-06-22）

- **外部图数据库移除**：图谱存储统一使用 SQLite，本地 `data/kb.db` 中的 Page、Block、Tag、实体引用和语义关系表共同构成图视图
- **设置页收束**：GUI 不再提供外部后端切换、服务启停、自动部署或迁移按钮，只展示 SQLite 图谱存储说明
- **运行路径简化**：GUI、API、MCP 启动时不会检测或拉起外部图服务；旧配置中的非 SQLite provider 会兼容降级为 SQLite
- **依赖与部署简化**：核心安装、`all` extra、Docker Compose 和示例配置都不再包含外部图数据库服务
- **滚动条主题适配**：浅色/暗色 QSS 保留 `QScrollArea#graphBackendScroll` 与 `QScrollBar` 样式，handle 颜色与主色板一致

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
