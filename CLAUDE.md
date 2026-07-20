# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目速查: ShineHeKnowledge

本地优先 MCP 知识检索引擎（**v1.11.0** Golden Time UI 重设计 + Verified Hybrid），面向 AI 助手提供可验证、可解释的私有知识检索服务。桌面 GUI、REST API、MCP Server、Windows 服务四种运行模式共享同一服务层，通过 AppContainer 依赖注入。

**核心定位：** 将本地文档索引为可供 Claude、Cursor、Cline 等 AI Agent 稳定调用的 MCP 知识检索引擎，默认暴露 10 个核心工具，返回带完整溯源的结构化引用。

**架构要点（v1.11.0）：**

- 检索：`SearchService` Facade → `RetrievalOrchestrator` **unified only** → Policy + `RawRetriever` / `VerifiedFusion` → `SearchExecution`
- 问答：`AnswerService`（`src/answering/`）→ `AnswerExecution`；MCP 仅协议适配
- MCP 实现：`src/mcp/server.py` 薄壳；工具在 `src/mcp/tools/*`；`src/mcp_server.py` 为兼容入口
- Container：`groups.core|verified|authoring|experimental` 为真实 Provider（构造/生命周期）
- 配置：`retrieval.orchestrator` / `answer.orchestrator` 均为 **unified**（legacy/shadow 为弃用别名）
- 新 Schema 只允许 Alembic；写模式启动校验 migration head（`src/storage/startup_gate.py`）

当前权威方向、实施计划和历史归档入口见 `docs/README.md` 与根目录 `PROGRESS.md`。不要从 `docs/archive/` 中恢复旧待办。

## 运行命令

```bash
# 安装
pip install -e .                # MCP 核心
pip install -e ".[all]"        # GUI + API + 解析器 + Wiki

# 运行
python main.py                  # 桌面 GUI
python run_api.py               # REST API (端口 8000)
python run_mcp.py               # MCP Server (stdio 模式)
shinehe-mcp -t streamable-http --port 9000  # MCP HTTP 模式

# Web 客户端 (client/)
cd client && npm install && npm run dev    # Vite dev server (端口 5173)
cd client && npm run build                # 生产构建 → client/dist/

# 测试
pytest tests/ -v                            # 全部测试
pytest tests/test_db.py -v                  # 单文件测试
pytest tests/test_search.py -k "test_hybrid" -v  # 匹配测试名

# 数据库迁移
alembic revision --autogenerate -m "描述"   # 生成迁移
alembic upgrade head                        # 执行迁移

# 打包
python scripts/build_windows.py             # Windows 安装包
python scripts/build_docker.py              # Docker 镜像
python scripts/build_docs.py                # 用户说明文档
```

## 架构概述

```
knowledge-base/
├── main.py / run_api.py / run_mcp.py   # GUI/API/MCP 入口 → create_container() 初始化
├── config.yaml                          # 驱动所有模式的主配置
├── alembic/                             # 数据库迁移
├── client/                              # React 19 + Vite + TypeScript Web 前端
├── mcp_config_templates/                # 一键 MCP 配置 JSON（Claude/Cursor/Cline 等）
├── scripts/                             # 构建、迁移、MCP 配置脚本
└── src/
    ├── core/
    │   ├── container.py                 # AppContainer + groups 能力视图
    │   └── service_groups.py            # Core / Verified / Authoring / Experimental
    ├── retrieval/                       # Phase-2：Orchestrator / Policy / Provider
    ├── answering/                       # Phase-3：AnswerService / AnswerExecution
    ├── mcp/
    │   ├── server.py                    # MCP 实现（工具/prompt/resource/lifespan）
    │   ├── runtime.py / auth.py / envelopes.py / policies.py
    │   ├── tool_registry.py             # 声明式工具注册，支持配置档过滤
    │   ├── tool_profiles.py             # core/extended/admin/full/legacy
    │   ├── tools/                       # 分域清单（实现过渡期仍在 server.py）
    │   └── aliases.py                   # 旧命名空间别名（仅 legacy 启用）
    ├── api/                             # FastAPI REST
    ├── repositories/                    # 数据访问层，逐步替代 db.py 直接操作
    ├── services/                        # 核心服务层（db / hybrid / wiki / search…）
    ├── mcp_server.py                    # 兼容入口 → src.mcp.server
    ├── cli.py                           # shinehe init/index/watch/doctor/mcp CLI
    ├── gui/                             # PySide6 桌面界面
    ├── models/                          # SearchExecution / Citation / Wiki V2 …
    └── utils/config.py                  # Config 单例 + keyring 密钥管理
```

## 核心设计模式

### 依赖注入容器

所有入口点通过 `create_container()` → `AppContainer` 初始化。依赖拓扑：

```
Config → Database → VectorStore → BlockStore → Embedding/LLM → Repositories → 业务服务(lazy)
```

- API 模式：`lifespan()` 中创建 Container，存入 `app.state.container`，路由通过 `get_container()` 获取
- MCP 模式：`_get_container()` 延迟创建（lifespan 未触发时的 fallback）
- GUI 模式：`main.py` 手动创建

### 双认证模型

- **REST API**：JWT Bearer Token（`auth.py`），用户注册/登录，密钥自动生成存 `data/.jwt_secret`
- **MCP Server**：stdio 使用本地信任模型；HTTP/SSE 写操作受 `write_policy`、`allow_http_write` 和可选 Bearer Token 约束
- 测试中通过 `api_client` fixture 自动注册用户并注入 token

### 安全防护

- **SSRF 防护**：`parse_url()` 在 HTTP 请求前做 DNS 解析 + IP 检查，阻止内网/回环/链路本地地址
- **安全响应头**：API 层自动添加 `X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`、`Permissions-Policy`
- **CORS 安全**：`allow_origins=["*"]` 时自动禁用 `allow_credentials`，防止 token 泄露
- **密码存储**：bcrypt 哈希，不存储明文
- **SQL 安全**：所有动态 SQL 变量均为内部硬编码或已白名单验证

### Repository 层过渡

`src/repositories/` 正逐步替代 `db.py` 中的直接 SQL 操作。新增数据访问应优先写 Repository，而非直接调用 `Database` 方法。

### 图谱存储

图谱存储统一使用 `SQLiteGraphBackend`；项目不再依赖外部图数据库服务。Page、Block、Tag、`entity_refs`、`tag_relations` 和 `knowledge_graph_relations` 共同构成 SQLite 图视图。

## 环境变量

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `SHINEHE_HOME` | 项目根目录覆盖 | 脚本所在目录 |
| `SHINEHE_API_HOST` | API 监听地址 | `0.0.0.0` |
| `SHINEHE_API_PORT` | API 监听端口 | `8000` |

## 测试约定

- `conftest.py`：`setup_db` (autouse) 每个测试创建临时 SQLite 并重置 Database/VectorStore/BlockStore 单例
- `api_client` fixture：创建 FastAPI TestClient，mock 掉 embedding 调用，自动注入 Bearer token
- `pyproject.toml` 配置 ruff、mypy、pytest；`pyproject.toml` 是唯一依赖声明
- `.github/workflows/ci.yml` 运行 lint、Python tests、前端 build 和 Docker build

## RAG 管线

可配置阶段（config.yaml → rag.pipeline.stages）：

1. **query_rewrite** — 查询改写（LLM 生成多版本）
2. **wiki_retrieval** — Wiki 知识检索（FTS5）
3. **vector_search** — 向量搜索（HybridSearcher: 向量 + 关键词 + RRF 融合）
4. **rerank** — LLM 打分重排序
5. **generate** — LLM 生成回答
6. **postprocess** — 后处理（去重、截断）

支持自定义阶段（custom_stages 配置）。

## Wiki 工作流

状态机 (`wiki_workflow.py`): draft → review → published → deprecated

支持状态转换验证、版本快照、工作流历史记录。可通过配置 `wiki.auto_publish` 跳过审核直接发布。

## MCP Server

实现位于 `src/mcp/server.py`（`src/mcp_server.py` 为兼容别名）。基于 FastMCP，通过 `src/mcp/tool_registry.py` 按配置档注册工具。

**默认 extended 配置档(20 个工具:核心检索 + 高级查询):**

10 个始终暴露的核心检索工具:
- `ping`, `kb_capabilities`, `search`, `ask`, `read`
- `list_knowledge`, `index_path`, `get_job`, `list_jobs`, `reindex_all`

extended 在 core 基础上额外提供 Query DSL、来源图谱、异步导入等高级查询能力(共 20 个)。如需 CRUD、操作审计请切到 `admin`;如需所有非 experimental 工具请切到 `full`。Wiki/图谱/Agent Memory 需 `experimental_tools_enabled=true`。

**配置档:** core / extended(默认) / admin / full / legacy

**资源：** 3 个（`kb://knowledge/{id}`、`kb://tags`、`kb://stats`）

**Prompt：** 5 个（kb_agent_research, kb_safe_update, kb_import_and_verify, kb_query_with_sources, kb_qa）

`mcp_config_templates/` 提供主流 AI 编码工具的一键配置 JSON。

## 版本发布

1. 修改 `src/version.py` 中的 VERSION
2. `python scripts/build_docs.py` — 生成用户说明文档
3. `python scripts/build_windows.py` — PyInstaller + Inno Setup
4. `python scripts/build_docker.py` — Docker 镜像 `shinehe/knowledge-base:<version>`
