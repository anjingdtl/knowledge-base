# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目速查: ShineHeKnowledge

本地优先知识检索系统（v1.2.0），支持多模态文档管理、RAG 智能问答和 MCP Agent 接入。桌面 GUI、REST API、MCP Server、Windows 服务四种运行模式共享同一服务层，通过 AppContainer 依赖注入。

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
    │   └── container.py                 # AppContainer — DI 容器，按依赖拓扑创建所有服务
    ├── api/
    │   ├── __init__.py                  # create_app() FastAPI 工厂，lifespan 中创建 Container
    │   ├── auth.py                      # JWT 认证（python-jose + bcrypt）
    │   ├── deps.py                      # FastAPI DI: get_container() 从 app.state 提取
    │   └── routes/                      # auth/chat/graph/jobs/knowledge/settings 等路由
    ├── repositories/                    # 数据访问层，逐步替代 db.py 直接操作
    ├── services/                        # 核心服务层
    │   ├── db.py                        # SQLite + FTS5 存储（单例，兼容旧代码）
    │   ├── vectorstore.py               # sqlite-vec 向量存储（1024 维 bge-m3）
    │   ├── block_store.py               # Block 级向量存储
    │   ├── embedding.py / llm.py        # OpenAI 兼容客户端
    │   ├── hybrid_search.py             # 向量 + 关键词混合搜索（RRF 融合）
    │   ├── search_service.py            # 统一搜索管线（MCP + API 共享）
    │   ├── rag_pipeline.py              # 可配置 RAG 管线（6 阶段）
    │   ├── file_graph.py                # 文件优先大纲图谱
    │   └── wiki_*.py                    # Wiki 系统（compiler/workflow/site/seo/lint）
    ├── mcp_server.py                    # 51 工具 + 51 别名 + 3 资源 + 5 Prompt
    ├── gui/                             # PySide6 桌面界面（暗色科幻主题）
    ├── models/                          # 数据模型（KnowledgeItem/Conversation/Block）
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

### Repository 层过渡

`src/repositories/` 正逐步替代 `db.py` 中的直接 SQL 操作。新增数据访问应优先写 Repository，而非直接调用 `Database` 方法。

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

`src/mcp_server.py` 基于 FastMCP，当前注册 51 个原始工具、51 个命名空间别名、3 个资源（`kb://knowledge/{id}`、`kb://tags`、`kb://stats`）和 5 个 Prompt。`mcp_config_templates/` 提供主流 AI 编码工具的一键配置 JSON。

## 版本发布

1. 修改 `src/version.py` 中的 VERSION
2. `python scripts/build_docs.py` — 生成用户说明文档
3. `python scripts/build_windows.py` — PyInstaller + Inno Setup
4. `python scripts/build_docker.py` — Docker 镜像 `shinehe/knowledge-base:<version>`
