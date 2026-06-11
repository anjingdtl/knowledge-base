<div align="center">

# ShineHe Knowledge

**本地优先的 AI 知识库系统 — RAG 智能问答 + MCP 工具链 + 知识图谱**

[![Version](https://img.shields.io/badge/version-1.2.0-blue.svg)](https://github.com/anjingdtl/knowledge-base)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.10-3776AB.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-30%2B%20tools-orange.svg)](src/mcp_server.py)

</div>

---

## 它是什么

ShineHe Knowledge 是一个**本地运行、隐私优先**的知识库系统，核心能力：

- 把你的文档喂进去，用自然语言问问题，AI 自动检索+生成回答
- 原生 MCP Server，30+ 工具直接被 Claude / Cursor / Cline 等 AI 工具调用
- 内建知识图谱、Wiki 工作流、混合搜索引擎

数据全程留在本地（SQLite + sqlite-vec），不依赖任何云端存储。

## 核心特性

### RAG 智能问答
6 阶段可配置管线：查询改写 → Wiki 检索 → 混合搜索 → 重排序 → 生成 → 后处理

### 混合搜索引擎
向量搜索（bge-m3 1024维）+ 关键词搜索（FTS5）+ RRF 融合，中文分词优化（jieba）

### 知识图谱
文件优先大纲图谱、多跳图遍历、DSL 查询、Agentic Router 智能路由

### MCP Server
30+ 工具 + 3 资源 + prompt 模板，一键配置 Claude / Cursor / Cline 等主流 AI 编码工具

### Wiki 系统
完整工作流（draft → review → published → deprecated）、版本快照、SEO 优化、LLM 死链修复

### 多模态文档解析
PDF / DOCX / TXT / Markdown / HTML / Excel / 图片 / 代码文件，大文件自动异步处理

### 三种运行模式
桌面 GUI（PySide6）/ REST API（FastAPI）/ MCP Server（stdio + HTTP），共享同一服务层

## 快速开始

### 安装

```bash
# MCP 核心模式（最小依赖）
pip install -e .

# 全功能模式（GUI + API + 解析器 + Wiki）
pip install -e ".[all]"
```

### 配置

编辑 `config.yaml`，填入你的 LLM / Embedding API 配置（兼容 OpenAI 接口即可）：

```yaml
embedding:
  base_url: https://api.siliconflow.cn/v1
  model: BAAI/bge-m3

llm:
  base_url: https://api.minimaxi.com/v1
  model: MiniMax-M3
```

### 启动

```bash
# 桌面 GUI
python main.py

# REST API（端口 8000）
python run_api.py

# MCP Server（stdio 模式）
python run_mcp.py

# MCP Server（HTTP 模式，端口 9000）
shinehe-mcp -t streamable-http --port 9000
```

### Web 客户端

```bash
cd client
npm install
npm run dev      # 开发服务器（端口 5173）
npm run build    # 生产构建
```

## MCP 工具一览

| 类别 | 工具 | 说明 |
|------|------|------|
| **连接** | `ping` | 连通性检测，<10ms 响应 |
| **搜索** | `search` / `search_fulltext` | 语义搜索 / 全文搜索（FTS5） |
| **问答** | `ask` | RAG 智能问答，返回 7 字段结构化结果 |
| **CRUD** | `create` / `read` / `update` / `delete` | 知识条目全生命周期管理 |
| **导入** | `ingest_file` / `ingest_url` | 文件 / 网页导入，大文件自动异步 |
| **异步** | `create_ingest_job` / `get_job` / `list_jobs` | 异步任务管理 |
| **索引** | `reindex_all` | 全量索引重建 |
| **标签** | `tags` / `list_knowledge` | 标签和知识列表查询 |
| **图谱** | `get_knowledge_graph` / `traverse_graph` | 知识图谱构建与遍历 |
| **Wiki** | `wiki_*` 系列 | Wiki 编译 / 发布 / 死链修复等 |
| **运维** | `get_stats` / `get_operation_log` / `undo_operation` | 统计 / 操作日志 / 撤销 |

## 架构

```
knowledge-base/
├── main.py / run_api.py / run_mcp.py   # 三种入口 → create_container() 初始化
├── config.yaml                          # 主配置文件
├── client/                              # React 19 + Vite + TypeScript 前端
├── src/
│   ├── core/container.py                # 依赖注入容器
│   ├── api/                             # FastAPI REST API（JWT 认证）
│   ├── services/                        # 核心服务层
│   │   ├── rag_pipeline.py              # 6 阶段 RAG 管线
│   │   ├── hybrid_search.py             # 混合搜索（向量 + 关键词 + RRF）
│   │   ├── vectorstore.py               # sqlite-vec 向量存储
│   │   ├── block_store.py               # Block 级向量存储
│   │   ├── file_graph.py                # 文件大纲图谱
│   │   ├── unified_graph.py             # 统一知识图谱
│   │   ├── wiki_*.py                    # Wiki 工作流系统
│   │   └── ...                          # 更多服务
│   ├── mcp_server.py                    # FastMCP Server（30+ 工具）
│   ├── gui/                             # PySide6 桌面界面
│   └── repositories/                    # 数据访问层
└── tests/                               # 测试套件
```

## 一键接入 AI 工具

`mcp_config_templates/` 提供主流 AI 编码工具的一键配置 JSON：

- Claude Desktop
- Cursor
- Cline
- Continue
- 其他 MCP 兼容客户端

## 部署

```bash
# Docker
docker compose up -d

# Windows 安装包
python scripts/build_windows.py
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.10+ / FastAPI / FastMCP / PySide6 |
| 向量 | sqlite-vec / bge-m3 (1024维) |
| 存储 | SQLite + FTS5 / Alembic 迁移 |
| 前端 | React 19 / Vite / TypeScript / Tailwind CSS |
| 构建 | PyInstaller + Inno Setup / Docker |

## 许可证

[MIT License](LICENSE)