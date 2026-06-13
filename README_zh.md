<div align="center">

# ShineHe Knowledge

**面向 AI 助手的本地优先 MCP 知识检索引擎**

[\[English\]](README.md)

[![Version](https://img.shields.io/badge/version-1.3.0-blue.svg)](https://github.com/anjingdtl/knowledge-base)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.10-3776AB.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-10%20core%20tools-orange.svg)](src/mcp/tool_profiles.py)

</div>

---

## 它是什么

ShineHe Knowledge 是一个**本地优先、隐私优先的 MCP 知识检索引擎**，将你的文档转化为可供 Claude、Cursor、Cline 等 AI 助手调用的高精准检索服务。

- **索引本地文档**（PDF、DOCX、Markdown、Excel、代码等）到基于 SQLite 的向量 + 关键词搜索引擎
- **暴露 10 个核心 MCP 工具**供 AI 助手搜索、提问、获取带引用的回答
- **返回结构化引用**，包含文档路径、Block ID、分数组成、匹配原因
- **目录增量监听**自动重新索引变更文件
- **数据全程留在本地**（SQLite + sqlite-vec + FTS5），无任何云端存储依赖

## 30 秒演示

```bash
# 1. 安装
pip install -e ".[parsers]"

# 2. 初始化本地配置
shinehe init --local --path D:\docs --client claude-code

# 3. 索引你的文档
shinehe index D:\docs

# 4. 启动 MCP 服务器（Claude Desktop / Cursor / Cline 会自动连接）
shinehe mcp --transport stdio
```

你的 AI 助手现在可以调用 `search` 或 `ask`，并获得完整的引用溯源：

```json
{
  "document": "architecture.md",
  "path": "D:/docs/architecture.md",
  "knowledge_id": "doc_001",
  "block_id": "doc_001_block_07",
  "location": {
    "heading_path": ["架构", "存储层"],
    "paragraph_index": 12
  },
  "score": 0.87,
  "score_breakdown": {
    "vector": 0.82,
    "keyword": 0.64,
    "rrf": 0.031,
    "rerank": 0.87
  },
  "match_channels": ["semantic", "keyword"],
  "reason": "语义 + 关键词匹配；已重排序",
  "text": "SQLite 使用 WAL 模式进行本地索引。"
}
```

## 支持的客户端

- **Claude Desktop** — `mcp_config_templates/claude_desktop.json`
- **Cursor** — `mcp_config_templates/cursor.json`
- **Cline** — `mcp_config_templates/cline.json`
- **Continue** — `mcp_config_templates/continue.json`
- **任何 MCP 兼容客户端** — stdio 或 HTTP/SSE 传输

## 核心特性

### 高精准检索
6 阶段可配置 RAG 管线：查询改写 → 向量 + FTS5 混合搜索 → RRF 融合 → 重排序 → 上下文扩展 → 引用打包。

### 结构化引用
每个搜索结果都包含文档路径、Block ID、位置信息（页码/工作表/幻灯片/标题路径/行号）、按通道的分数组成（向量/关键词/RRF/重排序）、匹配原因和原文。

### 目录增量索引
`shinehe watch D:\docs` 监听你的文档目录，自动重新索引新增、修改或删除的文件，支持防抖和基于哈希的差异检测。

### MCP 工具配置档
默认 `core` 配置档暴露 10 个稳定工具供 AI 助手使用。高级用户可通过 `config.yaml` 切换到 `extended`、`admin`、`full` 或 `legacy` 配置档。

### 本地重排序器（可选）
可插拔的重排序器提供者：API 方式、本地交叉编码器（sentence-transformers）、LLM 降级或禁用。失败时优雅降级。

### 评测与质量门禁
固定测试集、黄金标准答案、基线阈值和 CI 集成，证明检索质量（Recall@5、MRR、nDCG@10、引用完整性）。

## 快速开始

### 安装

```bash
# MCP 核心模式（最小依赖）
pip install -e .

# 包含文档解析器（PDF、DOCX、Excel 等）
pip install -e ".[parsers]"

# 全功能模式（GUI + API + 解析器 + Wiki + 图谱）
pip install -e ".[all]"
```

### 初始化

```bash
# 本地优先配置，使用 Ollama（推荐注重隐私）
shinehe init --local --path D:\docs --client claude-code

# 或使用云端 API 端点（手动编辑 config.yaml）
shinehe init --path D:\docs --client cursor
```

`shinehe init --local` 生成：
- Ollama embedding/LLM 配置（`http://localhost:11434/v1`）
- `mcp.tool_profile=core`（10 个工具）
- `mcp.write_policy=disabled`（默认只读）
- `rag.search_mode=blend`（向量 + 关键词）
- `rag.parent_child.enabled=true`（上下文扩展）

### 索引与监听

```bash
# 索引目录
shinehe index D:\docs

# 监听增量更新（Ctrl+C 停止）
shinehe watch D:\docs

# 诊断配置
shinehe doctor
```

### 启动 MCP 服务器

```bash
# stdio 模式（Claude Desktop / Cursor / Cline）
shinehe mcp --transport stdio

# HTTP 模式（端口 9000）
shinehe mcp --transport streamable-http --port 9000

# 传统入口（仍然可用）
python run_mcp.py
```

## 核心 MCP 工具

默认 `core` 配置档注册 10 个为 AI 助手检索优化的工具：

| 工具 | 用途 | 副作用 |
|------|------|--------|
| `ping` | 连通性检查 | 只读 |
| `kb_capabilities` | 查询当前配置档、能力、限制 | 只读 |
| `search` | 高精准检索，返回结构化引用 | 只读 |
| `ask` | 基于检索结果生成带引用的回答 | 只读 |
| `read` | 读取原文档或 Block 内容 | 只读 |
| `list_knowledge` | 列出已索引文档 | 只读 |
| `index_path` | 索引文件或目录（大输入返回异步任务） | 写 |
| `get_job` | 查询索引任务状态 | 只读 |
| `list_jobs` | 列出索引任务 | 只读 |
| `reindex_all` | 重建所有索引 | 写 |

高级工具（Query DSL、来源图谱、CRUD、Wiki、图谱、Agent Memory）可在 `extended`、`admin`、`full` 和 `legacy` 配置档中使用。参见 [docs/advanced-features.md](docs/advanced-features.md)。

## 检索质量

检索质量通过固定测试集、黄金标准答案和 CI 门禁证明：

- **Recall@5** — 正确答案出现在前 5 个结果中的查询百分比
- **MRR** — 首个正确命中的平均倒数排名
- **nDCG@10** — 归一化折损累积增益
- **引用完整性** — 具有有效路径、Block ID 和位置的引用百分比
- **无答案准确率** — 正确拒绝不可回答查询的准确率

基线阈值在 CI 中强制执行。参见 [docs/retrieval-quality.md](docs/retrieval-quality.md) 和 [evals/baselines/local.json](evals/baselines/local.json)。

## 核心 vs 实验性

**核心（默认）：** MCP 服务器、本地文件索引、混合搜索、RRF、重排序、上下文扩展、结构化引用、目录监听、评测门禁。

**实验性（可选）：** Wiki 工作流、图谱遍历（Neo4j）、Agent Memory、插件系统、Web 管理后台、多用户 RBAC。

高级功能保留在代码库中，但默认对 MCP 工具面隐藏。通过 `config.yaml` 中的 `mcp.experimental_tools_enabled=true` 启用。参见 [docs/advanced-features.md](docs/advanced-features.md)。

## 架构

```
knowledge-base/
├── main.py / run_api.py / run_mcp.py   # GUI/API/MCP 入口 → create_container()
├── config.yaml                          # 主配置
├── src/
│   ├── core/container.py                # 依赖注入容器
│   ├── mcp/tool_registry.py             # 声明式工具注册，支持配置档过滤
│   ├── mcp/tool_profiles.py             # core/extended/admin/full/legacy 工具集
│   ├── mcp_server.py                    # FastMCP 服务器（工具实现、提示、资源）
│   ├── cli.py                           # shinehe init/index/watch/doctor/mcp
│   ├── services/
│   │   ├── path_indexer.py              # 目录增量索引
│   │   ├── file_watcher.py              # 基于 watchdog 的目录监听
│   │   ├── hybrid_search.py             # 向量 + 关键词 + RRF 融合
│   │   ├── search_service.py            # 统一搜索管线（MCP + API）
│   │   ├── rag_pipeline.py              # 6 阶段可配置 RAG
│   │   ├── citation_builder.py          # 带位置元数据的结构化引用
│   │   ├── rerankers/                   # 可插拔重排序器提供者（API/本地/LLM/禁用）
│   │   └── ...
│   ├── repositories/                    # 数据访问层（indexed_files、knowledge_items、blocks）
│   └── models/                          # RetrievalCandidate、Citation、KnowledgeItem、Block
├── evals/                               # 检索质量测试集、数据集、基线
└── tests/                               # 契约、集成和评测测试
```

## 文档

- [快速开始与 Agent 使用](docs/mcp/agent-usage.md)
- [MCP 工具配置档与迁移指南](docs/migration/mcp-tool-profiles.md)
- [高级功能](docs/advanced-features.md)
- [检索质量与评测门禁](docs/retrieval-quality.md)
- [当前优化规格](docs/superpowers/specs/2026-06-13-mcp-local-retrieval-focus-design.md)
- [分模块实施计划](docs/superpowers/plans/2026-06-13-mcp-local-retrieval-focus.md)
- [项目状态](PROGRESS.md)

## 部署

```bash
# Docker（仅 MCP 镜像）
docker build --target mcp -t shinehe-knowledge:mcp .
docker run -v ~/.shinehe/data:/data shinehe-knowledge:mcp

# Windows 安装包
python scripts/build_windows.py

# Windows 服务（开机自启 + 崩溃恢复）
python windows_service.py install
python windows_service.py start
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.10+ / FastAPI / FastMCP |
| 向量 | sqlite-vec / bge-m3（1024 维） |
| 存储 | SQLite + FTS5 / Alembic 迁移 |
| 重排序 | sentence-transformers（可选）/ API / LLM 降级 |
| 前端 | React 19 / Vite / TypeScript（可选 Web 客户端） |
| 构建 | PyInstaller + Inno Setup / Docker / Windows 服务 |

## 许可证

[MIT License](LICENSE)
