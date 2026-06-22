# Remove 外部图数据库 and Consolidate SQLite Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完全移除 外部图数据库 作为可选图后端、部署入口和运行依赖，把个人知识库的图能力收束到现有 SQLite 图模型与 `SQLiteGraphBackend`。

**Architecture:** 保留 `GraphBackend` 抽象和 `SQLiteGraphBackend` 作为唯一后端，避免大面积重写 `UnifiedGraphService`、`GraphTraversalService`、`SourceGraph` 和 MCP/API 调用面。删除 外部图数据库 驱动、服务管理、外部同步、迁移到外部图库的路径；将 `blocks`、`entity_refs`、`tag_relations`、`knowledge_graph_relations` 作为唯一图数据来源。

**Tech Stack:** Python 3.10+、SQLite/FTS5/sqlite-vec、FastMCP、FastAPI、PySide6 GUI、pytest、PowerShell。

---

## 当前事实

- `src/services/graph_backend/sqlite_backend.py` 已经实现 Page、Block、Tag、`entity_refs`、`tag_relations`、`knowledge_graph_relations` 的统一图视图、邻居查询、BFS 遍历、子图加载和统计。
- `src/core/container.py` 通过 `create_graph_backend(config, db=db)` 创建图后端，并注入 `GraphRepository`、`FileGraphService`、`UnifiedGraphService`、`GraphTraversalService`、`RAGService`。
- 外部图数据库 依赖面分散在 `pyproject.toml` 的 `graph` extra、`docker-compose.yml`、`config.example.yaml`、README、GUI 设置页、GUI 主窗口自动启动、`外部图数据库_manager.py`、`外部图数据库_backend.py`、`GraphMigration`、历史快速迁移脚本、测试与评测样例。
- SQLite 图数据本身已经在 `src/services/db.py` 中：`knowledge_graphs`、`knowledge_graph_nodes`、`knowledge_graph_relations`、`blocks`、`block_refs`、`entity_refs`、`block_property_index`、`tag_relations`。
- 这次不应删除业务图谱功能；删除的是外部 外部图数据库 服务和双后端复杂度。

## 目标边界

### 必须删除

- 外部图数据库 Python 依赖、Docker 服务、自动部署、启停检测、Bolt 配置、图查询 后端实现。
- “SQLite / 外部图数据库 可切换”的用户配置和 UI 文案。
- SQLite 到 外部图数据库 的迁移 API、迁移服务、历史脚本入口和测试。
- MCP/REST/GUI 中任何试图连接或拉起 外部图数据库 的逻辑。

### 必须保留

- `GraphBackend` 接口，除非后续重构明确证明不需要；本阶段保留能降低风险。
- `SQLiteGraphBackend` 作为唯一实现。
- `UnifiedGraphService.build()`、`GraphTraversalService.traverse()`、`build_source_graph()`、MCP `get_source_graph` 的对外契约。
- `knowledge_graphs`、`knowledge_graph_nodes`、`knowledge_graph_relations`：这是本地 SQLite 图谱业务数据，不是 外部图数据库 残留。
- `blocks`、`entity_refs`、`tag_relations`、`block_property_index` 和 sqlite-vec。

## 文件地图

### 删除文件

- `src/services/graph_backend/外部图数据库_backend.py`
- `src/services/外部图数据库_manager.py`
- `tests/test_外部图数据库_manager.py`
- `scripts/fast_migrate.py`
- `scripts/fast_migrate_edges.py`

### 大幅收束

- `src/services/graph_backend/factory.py`：只返回 `SQLiteGraphBackend`；非 sqlite 配置直接记录 warning 并使用 sqlite，或在配置迁移后抛出清晰错误。推荐第一版采用兼容降级。
- `src/services/graph_backend/__init__.py`：只导出 `GraphBackend`、`SQLiteGraphBackend`、`create_graph_backend`。
- `src/services/graph_backend/base.py`：文案改为 SQLite-only；接口保留。
- `src/services/graph_backend/sync_hooks.py`：改为兼容 no-op shim，或删除调用后再删除文件。推荐第一版保留 no-op shim，第二版再删。
- `src/services/graph_backend/migration.py`：删除或替换为 `sqlite_graph_repair.py`。本计划推荐删除外部后端迁移服务，避免误导。
- `src/api/routes/graph.py`：移除 `/backend/migrate`、`/backend/sync`，保留 `/backend/status` 但返回 SQLite-only 状态。
- `src/gui/settings_dialog.py`：删除 外部图数据库 选择、连接配置、服务管理、自动部署按钮；保留“图谱存储：SQLite 内置”只读说明。
- `src/gui/graph_view.py`：删除 外部图数据库 状态判断和 外部图数据库 标签文案，统一显示 SQLite。
- `src/gui/main_window.py`：删除 `_auto_start_外部图数据库` 计时器和方法。
- `src/repositories/graph_repo.py`、`src/services/file_graph.py`、`src/services/graph_builder.py`：移除外部图后端同步调用或让其走 SQLite 内部写入路径。
- `pyproject.toml`：删除 `graph = ["外部图数据库>=5.0"]` extra，并从 `all` 中移除 `graph`。
- `docker-compose.yml`：删除 `外部图数据库` service 和 `外部图数据库-data` volume。
- `config.example.yaml`：删除 `graph_backend.uri/user/password/database/max_connection_pool_size`，保留 `provider: sqlite` 或改为只读兼容项。
- `src/utils/config.py`：删除 `graph_backend.password` 的 keyring/env 映射。
- `README.md`、`README_zh.md`、`docs/advanced-features.md`、`evals/datasets/basic_qa.yaml`、`evals/datasets/graph_qa.yaml`、`scripts/README.md`、`PROGRESS.md`：移除 外部图数据库 配置和可选部署叙事，改为 SQLite 图能力说明。
- `client/src/views/SettingsView.tsx`：删除 外部图数据库 / SQLite 可选文案，如 Web 设置页仍暴露该项。

### 测试改造

- `tests/test_graph_backend.py`：保留 SQLite 后端、ID 工具、服务集成测试；删除 mock 外部图数据库、迁移工具、非 SQLite hook 测试。
- `tests/test_mcp_stability.py`：删除 “外部图数据库 health check fallback” 测试，新增 “legacy config provider=外部图数据库 still starts as sqlite with warning” 或 “unknown provider rejected” 测试。
- `tests/test_api.py` 或新增 `tests/test_graph_sqlite_only.py`：覆盖 `/api/graph/backend/status`、`/api/graph/unified`、`/api/graph/traverse`。
- `tests/test_mcp_e2e_file_graph.py`、`tests/test_mcp_first_completion_gaps.py`、`tests/test_mcp_rag_full.py`：补强 MCP `get_source_graph` 在 SQLite-only 下的端到端证明。

---

## 模块 M0：冻结基线与保护本地数据

**Files:**
- Modify: none
- Test: current targeted graph/MCP tests

- [ ] **Step 1: 记录当前数据库表与图数据统计**

运行：

```powershell
python - <<'PY'
from src.utils.config import Config
from src.services.db import Database
Config.load()
db = Database(str(Config.get_db_path()))
conn = db.get_conn()
for table in ["knowledge_items", "blocks", "entity_refs", "tag_relations", "knowledge_graphs", "knowledge_graph_nodes", "knowledge_graph_relations"]:
    try:
        print(f"{table}: {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}")
    except Exception as exc:
        print(f"{table}: ERROR {exc}")
db.close()
PY
```

预期：命令只读，不修改 `data/kb.db`；输出作为施工前快照。

- [ ] **Step 2: 跑现有 SQLite 图后端基线测试**

运行：

```powershell
python -m pytest tests/test_graph_backend.py tests/test_api.py::TestPhase2GraphAPI tests/test_mcp_e2e_file_graph.py -q
```

预期：当前失败项先记录，不在同一提交里混入无关修复。

- [ ] **Step 3: 创建数据备份**

运行：

```powershell
Copy-Item -LiteralPath data\kb.db -Destination ("data\backups\kb-before-remove-外部图数据库-{0:yyyyMMdd-HHmmss}.db" -f (Get-Date))
```

预期：只复制 SQLite DB，不删除 `data/`、`chroma/`、`graph/`。

---

## 模块 M1：收束后端工厂为 SQLite-only

**Files:**
- Modify: `src/services/graph_backend/factory.py`
- Modify: `src/services/graph_backend/base.py`
- Modify: `src/services/graph_backend/__init__.py`
- Delete: `src/services/graph_backend/外部图数据库_backend.py`
- Delete: `src/services/graph_backend/migration.py`
- Test: `tests/test_graph_backend.py`
- Test: `tests/test_mcp_stability.py`

- [ ] **Step 1: 先改测试，固定 SQLite-only 行为**

`tests/test_graph_backend.py` 中保留 `TestFactory.test_default_sqlite`，新增：

```python
def test_legacy_外部图数据库_provider_uses_sqlite():
    class LegacyConfig:
        def get(self, key, default=None):
            if key == "graph_backend.provider":
                return "外部图数据库"
            return default

    backend = create_graph_backend(LegacyConfig(), db=Database)

    assert backend.name == "sqlite"
```

删除 mock target 为 `"外部图数据库"` 的 `TestMigration` 和 `TestSyncHooks.test_non_sqlite_backend_enabled` 等外部后端用例。

- [ ] **Step 2: 运行测试确认当前会失败**

```powershell
python -m pytest tests/test_graph_backend.py tests/test_mcp_stability.py -q
```

预期：失败点集中在工厂仍尝试导入 外部图数据库、迁移类仍存在、旧测试仍期望 外部图数据库 fallback。

- [ ] **Step 3: 实现 SQLite-only factory**

`create_graph_backend()` 目标逻辑：

```python
def create_graph_backend(config, db=None) -> GraphBackend:
    from src.services.db import Database
    from src.services.graph_backend.sqlite_backend import SQLiteGraphBackend

    db = db or Database
    provider = config.get("graph_backend.provider", "sqlite")
    if provider != "sqlite":
        logger.warning(
            "Graph backend provider %r is no longer supported; using SQLite",
            provider,
        )
    return SQLiteGraphBackend(db=db)
```

`base.py` 文案改为“当前仅 SQLite 实现”，保留接口类型。

- [ ] **Step 4: 删除 外部图数据库 后端和迁移服务**

删除：

```text
src/services/graph_backend/外部图数据库_backend.py
src/services/graph_backend/migration.py
```

同时删除所有 `from src.services.graph_backend.migration import GraphMigration` 调用。

- [ ] **Step 5: 运行后端测试**

```powershell
python -m pytest tests/test_graph_backend.py tests/test_mcp_stability.py -q
```

预期：通过；无 `外部图数据库_backend` 导入。

- [ ] **Step 6: 提交**

```powershell
git add src/services/graph_backend tests/test_graph_backend.py tests/test_mcp_stability.py
git commit -m "refactor(graph): consolidate backend on sqlite"
```

---

## 模块 M2：删除同步钩子和外部迁移 API

**Files:**
- Modify: `src/api/routes/graph.py`
- Modify: `src/repositories/graph_repo.py`
- Modify: `src/services/file_graph.py`
- Modify: `src/services/graph_builder.py`
- Modify: `src/services/graph_backend/sync_hooks.py`
- Test: `tests/test_api.py`
- Test: `tests/test_graph_backend.py`
- Test: `tests/test_logseq_graph_phase2.py`

- [ ] **Step 1: API 测试先固定新契约**

新增或修改测试断言：

```python
def test_graph_backend_status_is_sqlite(api_client):
    resp = api_client.get("/api/graph/backend/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "sqlite"
    assert data["healthy"] is True
    assert data["stats"]["backend"] == "sqlite"
```

对 `/api/graph/backend/migrate` 和 `/api/graph/backend/sync` 的期望改为 404，或从 OpenAPI 中不存在。

- [ ] **Step 2: 移除外部迁移路由**

从 `src/api/routes/graph.py` 删除：

```text
POST /graph/backend/migrate
POST /graph/backend/sync
```

保留：

```text
GET /graph/backend/status
GET /graph/unified
POST /graph/traverse
```

- [ ] **Step 3: 同步钩子改为 no-op 兼容层**

第一版保留 `GraphSyncHook` 类，但固定：

```python
self._enabled = False
```

这样 `FileGraphService` 和 `GraphRepository` 可以小步清理，不会因为一次删除触发大量调用点连锁修改。

- [ ] **Step 4: 删除业务层外部后端同步**

从 `GraphBuilder._sync_relations_to_backend()`、`FileGraphService`、`GraphRepository` 中移除“同步到非 SQLite 后端”的副作用；语义关系只写入 SQLite 的 `knowledge_graph_relations` 和 `entity_refs`。

- [ ] **Step 5: 运行图 API 与 Logseq 图测试**

```powershell
python -m pytest tests/test_api.py::TestPhase2GraphAPI tests/test_logseq_graph_phase2.py tests/test_graph_backend.py -q
```

预期：统一图、遍历和 SQLite 后端统计均通过。

- [ ] **Step 6: 提交**

```powershell
git add src/api/routes/graph.py src/repositories/graph_repo.py src/services/file_graph.py src/services/graph_builder.py src/services/graph_backend/sync_hooks.py tests
git commit -m "refactor(graph): remove external graph sync paths"
```

---

## 模块 M3：删除 GUI 和配置中的 外部图数据库 管理

**Files:**
- Modify: `src/gui/settings_dialog.py`
- Modify: `src/gui/graph_view.py`
- Modify: `src/gui/main_window.py`
- Delete: `src/services/外部图数据库_manager.py`
- Delete: `tests/test_外部图数据库_manager.py`
- Test: `tests/test_graph_progress.py`
- Test: `tests/test_graph_view_lifecycle.py`
- Test: GUI smoke if available

- [ ] **Step 1: 更新 GUI 预期**

设置页不再提供后端下拉选择。目标文案：

```text
图谱存储：SQLite 内置图索引
Page、Block、Tag、引用关系和语义关系均保存在本地 data/kb.db 中，无需外部图数据库服务。
```

- [ ] **Step 2: 删除主窗口自动启动 外部图数据库**

从 `src/gui/main_window.py` 删除：

```python
QTimer.singleShot(1000, self._auto_start_外部图数据库)
```

以及 `_auto_start_外部图数据库()` 方法。

- [ ] **Step 3: 删除设置页 外部图数据库 连接和服务管理控件**

删除 `_外部图数据库_group`、`_外部图数据库_svc_group`、`_refresh_外部图数据库_status()`、`_on_外部图数据库_start()`、`_on_外部图数据库_stop()`、`_on_外部图数据库_auto_deploy()`、部署进度控件，以及保存 `graph_backend.uri/user/password/database` 的逻辑。

- [ ] **Step 4: 图谱视图统一显示 SQLite**

`graph_view.py` 中删除 `外部图数据库Manager().is_running()` 检测；状态显示只根据 `container.graph_backend.health_check()` 或 SQLite DB 连通性。

- [ ] **Step 5: 删除 外部图数据库Manager**

删除：

```text
src/services/外部图数据库_manager.py
tests/test_外部图数据库_manager.py
```

- [ ] **Step 6: 运行 GUI 相关测试**

```powershell
python -m pytest tests/test_graph_progress.py tests/test_graph_view_lifecycle.py tests/test_mcp_gui_status.py -q
```

预期：不再导入 `src.services.外部图数据库_manager`。

- [ ] **Step 7: 提交**

```powershell
git add src/gui src/services tests
git commit -m "refactor(gui): remove 外部图数据库 management UI"
```

---

## 模块 M4：依赖、配置、Docker、脚本清理

**Files:**
- Modify: `pyproject.toml`
- Modify: `docker-compose.yml`
- Modify: `config.example.yaml`
- Modify: `src/utils/config.py`
- Modify: `scripts/README.md`
- Delete: `scripts/fast_migrate.py`
- Delete: `scripts/fast_migrate_edges.py`
- Test: package metadata and import smoke

- [ ] **Step 1: 删除 Python extra**

`pyproject.toml` 删除：

```toml
graph = [
    "外部图数据库>=5.0",
]
```

并将：

```toml
all = ["shinehe-knowledge[gui,api,parsers,wiki,graph,local-rerank,watch]"]
```

改为：

```toml
all = ["shinehe-knowledge[gui,api,parsers,wiki,local-rerank,watch]"]
```

- [ ] **Step 2: 删除 Docker 外部图数据库 profile**

`docker-compose.yml` 删除 `外部图数据库` service 和 `外部图数据库-data` volume。保留 api/mcp 的 `./data:/app/data` 挂载。

- [ ] **Step 3: 配置文件只保留 SQLite**

`config.example.yaml` 改为：

```yaml
graph_backend:
  provider: sqlite
```

删除 `uri`、`user`、`password`、`database`、`max_connection_pool_size`。

- [ ] **Step 4: 删除 外部图数据库 密钥映射**

`src/utils/config.py` 删除：

```python
"graph_backend.password": "SHINEHE_外部图数据库_PASSWORD"
```

- [ ] **Step 5: 删除历史迁移脚本**

删除：

```text
scripts/fast_migrate.py
scripts/fast_migrate_edges.py
```

`scripts/README.md` 删除 “SQLite -> 外部图数据库 快速迁移” 行。

- [ ] **Step 6: 运行导入和依赖 smoke**

```powershell
python - <<'PY'
from src.core.container import create_container, shutdown_container
c = create_container()
print(c.graph_backend.name)
shutdown_container(c)
PY
python -m pytest tests/test_core.py tests/test_graph_backend.py -q
```

预期：输出 `sqlite`；不需要安装 `外部图数据库` 包。

- [ ] **Step 7: 提交**

```powershell
git add pyproject.toml docker-compose.yml config.example.yaml src/utils/config.py scripts
git commit -m "chore(graph): drop 外部图数据库 dependencies and deployment files"
```

---

## 模块 M5：文档、评测样例和客户端文案收口

**Files:**
- Modify: `README.md`
- Modify: `README_zh.md`
- Modify: `CLAUDE.md`
- Modify: `docs/advanced-features.md`
- Modify: `docs/mcp/agent-usage.md`
- Modify: `evals/datasets/basic_qa.yaml`
- Modify: `evals/datasets/graph_qa.yaml`
- Modify: `client/src/views/SettingsView.tsx`
- Modify: `PROGRESS.md`
- Test: docs prompt tests and frontend build

- [ ] **Step 1: README 改写图谱说明**

替换 “Graph Backend (SQLite / 外部图数据库)” 为 “SQLite Graph Storage”。核心表述：

```text
Graph data is stored in the local SQLite database through Page, Block, Tag, entity reference, and semantic relation tables. No external graph database is required.
```

中文 README 对应：

```text
图谱数据保存在本地 SQLite：Page、Block、Tag、实体引用和语义关系共用 data/kb.db，无需部署 外部图数据库。
```

- [ ] **Step 2: 删除高级功能中的 外部图数据库 可选项**

`docs/advanced-features.md` 不再列 “外部图数据库 backend”。保留 “图谱遍历 / 来源图谱 / 语义关系分析”，但说明其基于 SQLite。

- [ ] **Step 3: 更新评测问题**

`evals/datasets/basic_qa.yaml` 将 “如何配置 外部图数据库 作为图后端？” 改为 “图谱数据默认保存在哪里？”；期望答案包含 `SQLite`、`data/kb.db`、`blocks/entity_refs`。

- [ ] **Step 4: Web 设置页删除 外部图数据库 文案**

`client/src/views/SettingsView.tsx` 中 “外部图数据库 / SQLite (可选)” 改为 “SQLite 图谱存储”。

- [ ] **Step 5: 更新 CLAUDE.md**

架构说明增加：

```text
图谱存储统一使用 SQLiteGraphBackend；项目不再依赖 外部图数据库 或外部图数据库服务。
```

- [ ] **Step 6: 运行文档和前端验证**

```powershell
python -m pytest tests/test_mcp_docs_prompts.py -q
npm --prefix client run build
```

预期：文档推荐流程不再出现 外部图数据库；前端构建通过。

- [ ] **Step 7: 提交**

```powershell
git add README.md README_zh.md CLAUDE.md docs evals client/src/views/SettingsView.tsx PROGRESS.md
git commit -m "docs(graph): document sqlite-only graph storage"
```

---

## 模块 M6：MCP-first 端到端验收

**Files:**
- Test only unless failures reveal regressions

- [ ] **Step 1: 跑 SQLite 图核心回归**

```powershell
python -m pytest tests/test_graph_backend.py tests/test_logseq_graph_phase2.py tests/test_api.py::TestPhase2GraphAPI -q
```

预期：SQLite 图后端、统一图 API、图遍历均通过。

- [ ] **Step 2: 跑 MCP 检索和来源图端到端**

```powershell
python -m pytest tests/test_mcp_e2e_file_graph.py tests/test_mcp_first_completion_gaps.py tests/test_mcp_rag_full.py -q
```

预期：`ingest/read/search/structured_query/get_source_graph` 链路仍能返回来源图。

- [ ] **Step 3: 搜索残留引用**

```powershell
rg -n "外部图数据库|外部图数据库|ExternalGraphDriver|external://|外部图数据库|external_graph_lib|图查询|图查询" src tests scripts docs README.md README_zh.md pyproject.toml docker-compose.yml config.example.yaml client
```

预期：只允许 `docs/archive/` 中保留历史归档；非归档路径无 外部图数据库 运行路径和配置指导。

- [ ] **Step 4: 跑全量测试**

```powershell
python -m pytest tests -q
```

预期：通过；如果全量耗时过长，至少记录 targeted 通过和未跑全量的原因。

- [ ] **Step 5: 运行格式和构建**

```powershell
ruff check src tests
npm --prefix client run build
```

预期：通过。

- [ ] **Step 6: 最终提交**

```powershell
git status --short
git commit -m "refactor(graph): remove 外部图数据库 and use sqlite graph storage"
```

---

## 风险与回滚

- **风险：旧用户配置仍写着 `graph_backend.provider: 外部图数据库`。** 第一版 factory 对非 sqlite provider warning 后降级 SQLite，避免启动失败；文档提示删除旧配置字段。
- **风险：删除迁移 API 影响前端按钮或外部调用。** 先在 API 测试固定 404 或隐藏入口，再清理 UI。
- **风险：SourceGraph 输出 ID 前缀不一致。** `SQLiteGraphBackend` 内部用 `page:`/`block:`，`GraphTraversalService` 对外剥离 public id；MCP `get_source_graph` 必须用端到端测试锁住。
- **风险：外部图数据库 关键词仍留在历史归档。** `docs/archive/` 可以保留历史事实；验收 grep 只拦截非归档路径。
- **回滚点：** 每个模块单独提交。若 GUI 清理出问题，可回滚 M3，不影响 M1/M2 的 SQLite-only 后端收束。

## 最终验收标准

- 干净环境安装 `pip install -e .` 不安装 `外部图数据库` 依赖。
- 启动 GUI/API/MCP 时不会检测、下载、启动或连接 外部图数据库。
- 配置示例和 README 不再教用户配置 外部图数据库。
- `GET /api/graph/backend/status` 返回 `provider=sqlite` 且 stats 正常。
- MCP 用户流 `ingest_file -> read -> search_fulltext/structured_query -> get_source_graph -> undo_operation` 仍通过。
- `rg` 在非归档路径找不到 外部图数据库 运行代码、配置项、测试期望或部署入口。


