---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '8d975fd6-664d-40d5-967a-b9b35ea5da28'
  PropagateID: '8d975fd6-664d-40d5-967a-b9b35ea5da28'
  ReservedCode1: '6e6d593a-80a7-4d6c-81d8-e6023fbce3bd'
  ReservedCode2: '6e6d593a-80a7-4d6c-81d8-e6023fbce3bd'
---

<div align="center">

# ShineHe Knowledge

**本地优先的 AI 知识库系统 — RAG 智能问答 + MCP 工具链 + 知识图谱**

[\[English\]](README.md)

[![Version](https://img.shields.io/badge/version-1.2.0-blue.svg)](https://github.com/anjingdtl/knowledge-base)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.10-3776AB.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-45%20tools-orange.svg)](src/mcp_server.py)

</div>

---

## 它是什么

ShineHe Knowledge 是一个**本地运行、隐私优先**的知识库系统：

- 把你的文档喂进去，用自然语言问问题，AI 自动检索+生成回答
- 原生 MCP Server，51 个核心工具 + 51 个命名空间别名，可直接被 Claude / Cursor / Cline 等 AI 工具调用
- 内建知识图谱（SQLite + Neo4j 双后端）、Wiki 工作流、混合搜索引擎
- 插件式架构，支持钩子扩展和图数据库后端切换

数据全程留在本地（SQLite + sqlite-vec），不依赖任何云端存储。

## 核心特性

### RAG 智能问答
6 阶段可配置管线：查询改写 → Wiki 检索 → 混合搜索 → 重排序 → 生成 → 后处理  
支持 Agentic Router 智能路由和 DSL 精确查询两种模式

### 混合搜索引擎
向量搜索（bge-m3 1024维）+ 关键词搜索（FTS5）+ RRF 融合，中文分词优化（jieba）

### 知识图谱（双后端）
- **SQLite 后端**（默认）：零依赖，直接查询知识库表
- **Neo4j 后端**（可选）：Cypher 查询、批量 UNWIND、高效多跳遍历
- 插件式后端接口 + 数据迁移 + 增量同步 + 事件驱动同步钩子
- 文件大纲图谱、多跳图遍历、结构化 DSL 查询、Agentic Router 智能路由

### MCP Server
51 个核心工具 + 51 个命名空间别名 + 2 个资源 + 1 个资源模板 + 5 个 Prompt，覆盖搜索、问答、CRUD、导入、Wiki、图谱、查询、运维和 Agent Memory 全场景
支持 `preview_operation` 预览 + `undo_operation` 撤销，写操作安全闭环

### Wiki 系统
完整工作流（draft → review → published → deprecated）、版本快照与恢复、LLM 死链修复、知识体检

### 操作安全闭环
写操作前可预览（dry_run）、操作审计日志、任意操作可撤销（undo）——Agent 再也不会"手滑"

### 多模态文档解析
PDF / DOCX / TXT / Markdown / HTML / Excel / 图片 / 代码文件，大文件自动异步处理

### 插件系统
基于钩子的事件驱动架构——知识创建/删除/更新时自动触发插件回调，一行注册即可扩展

### 四种运行模式
桌面 GUI（PySide6）/ REST API（FastAPI）/ MCP Server（stdio + HTTP）/ Windows 服务，共享同一服务层

## 快速开始

### 安装

```bash
# MCP 核心模式（最小依赖）
pip install -e .

# 全功能模式（GUI + API + 解析器 + Wiki + 图谱）
pip install -e ".[all]"

# 仅 Neo4j 图谱后端
pip install -e ".[graph]"
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

# 可选：Neo4j 图谱后端
graph_backend:
  provider: neo4j          # 默认 sqlite
  uri: bolt://localhost:7687
  user: neo4j
  password: your_password
  database: neo4j
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

# Windows 服务模式（开机自启 + 崩溃自动重启）
python windows_service.py install
python windows_service.py start
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
| **问答** | `ask` / `ask_with_query` | RAG 智能问答 / 指定 QuerySpec 的可控问答 |
| **CRUD** | `create` / `read` / `update` / `delete` / `restore_knowledge` | 知识条目全生命周期（含软删除恢复） |
| **导入** | `ingest_file` / `ingest_url` | 文件 / 网页导入，大文件自动异步 |
| **异步** | `create_ingest_job` / `get_job` / `list_jobs` / `cancel_job` | 导入异步任务管理 |
| **通用异步** | `create_async_job` / `get_async_job` / `list_async_jobs` / `cancel_async_job` | 通用异步任务框架 |
| **索引** | `reindex_all` | 全量索引重建 |
| **标签** | `tags` / `list_knowledge` | 标签和知识列表查询 |
| **结构化查询** | `structured_query` / `explain_query` | DSL 条件查询 / 执行计划解释 |
| **图谱** | `graph_traverse` / `get_source_graph` | 多跳图遍历 / RAG 证据链追溯 |
| **智能路由** | `route_query` / `execute_query` | Agentic 路由分析 / 显式 QuerySpec 执行 |
| **Wiki** | `wiki_lint` / `fix_dead_references` / `wiki_submit_review` / `wiki_approve` / `wiki_reject` / `wiki_deprecate` / `wiki_workflow_history` / `wiki_list_versions` / `wiki_restore_version` / `save_to_wiki` | Wiki 全工作流 + 死链修复 + 版本管理 |
| **运维** | `kb_capabilities` / `query_operation_logs` / `get_operation_log` / `undo_operation` / `preview_operation` / `list_recent_operations` | 能力查询 / 审计日志 / 撤销 / 预览 |

## 架构

```
knowledge-base/
├── main.py / run_api.py / run_mcp.py   # 四种入口 → create_container() 初始化
├── config.yaml                          # 主配置文件
├── windows_service.py                   # Windows 服务入口（自启 + 崩溃重启）
├── client/                              # React 19 + Vite + TypeScript 前端
├── src/
│   ├── core/container.py                # 依赖注入容器
│   ├── api/                             # FastAPI REST API（JWT 认证）
│   ├── services/                        # 核心服务层
│   │   ├── rag_pipeline.py              # 6 阶段 RAG 管线
│   │   ├── hybrid_search.py             # 混合搜索（向量 + 关键词 + RRF）
│   │   ├── vectorstore.py               # sqlite-vec 向量存储
│   │   ├── block_store.py               # Block 级向量存储
│   │   ├── unified_graph.py             # 统一知识图谱（后端无关）
│   │   ├── graph_backend/               # 🔌 图数据库后端插件
│   │   │   ├── base.py                  #   抽象接口 + 数据类
│   │   │   ├── factory.py               #   后端工厂
│   │   │   ├── sqlite_backend.py        #   SQLite 后端（默认）
│   │   │   ├── neo4j_backend.py         #   Neo4j 后端（Cypher 查询）
│   │   │   ├── migration.py             #   SQLite → Neo4j 数据迁移
│   │   │   └── sync_hooks.py            #   事件驱动增量同步
│   │   ├── neo4j_manager.py             # Neo4j 进程管理（自动启停）
│   │   ├── wiki_*.py                    # Wiki 工作流系统
│   │   └── ...                          # 更多服务
│   ├── mcp_server.py                    # FastMCP Server（45 工具）
│   ├── plugins/                         # 🔌 插件钩子系统
│   ├── gui/                             # PySide6 桌面界面
│   │   ├── wiki_view.py                 #   Wiki 管理（体检 / 死链修复 / 工作流）
│   │   ├── graph_view.py               #   图谱可视化（力导向布局 / 双后端）
│   │   ├── settings_dialog.py           #   设置（7 个标签页含服务管理）
│   │   └── ...                          #   更多界面
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

# Windows 服务（开机自启 + 崩溃自动重启）
python windows_service.py install
sc failure ShineHeMCP reset= 86400 actions= restart/5000/restart/10000/restart/30000
python windows_service.py start
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.10+ / FastAPI / FastMCP / PySide6 |
| 向量 | sqlite-vec / bge-m3 (1024维) |
| 存储 | SQLite + FTS5 / Alembic 迁移 |
| 图谱 | SQLite（默认）/ Neo4j（可选，Cypher 查询） |
| 前端 | React 19 / Vite / TypeScript / Tailwind CSS |
| 构建 | PyInstaller + Inno Setup / Docker / Windows Service |

## 许可证

[MIT License](LICENSE)
