# ShineHeKnowledge 优化计划执行进度

> 截止 2026-06-12，commit `506de35`
> 规范文档: `docs/SPEC_audit_optimization.md`
> 执行计划: `docs/PLAN_execution_phases.md`

## 总览

5 个 Phase，已完成 5 个，剩余 0 个。

| Phase | 目标 | Commit | 变更 | 状态 |
|-------|------|--------|------|------|
| 1 | 基础工程治理 (v1.3) | `c1d3f7e` | ~800 行 | ✅ |
| 2 | 架构内核重构 (v1.4前半) | `8a5b2c4` | ~2200 行 | ✅ |
| 3 | RAG 质量升级 (v1.4后半) | `a3e1f9d` | ~1800 行 | ✅ |
| 4 | MCP 产品化升级 (v1.5) | `c7e47b1` | ~1200 行 | ✅ |
| 5 | 前端体验升级 (v2.0) | `506de35` | ~2060 行 | ✅ |

## Phase 1: 基础工程治理 ✅

### Step 1.1: 配置安全改造
- `config.example.yaml` — 模板配置，无真实密码
- `_SECRET_KEYS` 扩展至 5 个敏感字段 + 环境变量映射
- keyring 降级警告
- 迁移脚本从 Config 读取凭据（消除硬编码）
- `config.yaml` 加入 `.gitignore` / `.stignore`
- MCP 模板路径占位符化

### Step 1.2: MCP 写操作安全策略
- `mcp.write_policy` 四级策略 (preview_only / local_confirm / token_required / disabled)
- `_check_write_policy()` 守卫函数
- 全量工具 annotations 补齐 (41→48+)

### Step 1.3: Docker Profile 拆分
- 多阶段 Dockerfile (base → api / mcp)
- `docker-compose.yml` 重写 (api + mcp + neo4j profiles)
- `.dockerignore` 优化

### Step 1.4: 引入 CI
- `.github/workflows/ci.yml` (lint + test + frontend + docker)
- `pyproject.toml` 工具配置 (ruff / pytest / mypy)

### Step 1.5: 统一依赖来源
- 补齐缺失包 (charset-normalizer, python-pptx)
- 删除冗余 requirements.txt

## Phase 2: 架构内核重构 ✅

### Step 2.1: Database 去 God Class + DI 容器
- `AppContainer` 依赖注入容器 (12 个仓库 + 15 个业务服务)
- `create_container()` 按依赖拓扑创建所有服务
- Database 实例化改造 (非类方法单例)
- Repository 层 (8→11 个): knowledge, conversation, wiki, graph, block, entity_ref, category, job, tag_relation, property_schema, operation_log
- `get_active_container()` 模块级引用

### Step 2.2: RAG 管线依赖注入
- PipelineStage 构造器注入 (不再内联创建服务)
- RagContext 类型化为 dataclass
- Container 中组装完整管线

### Step 2.3: 统一配置驱动
- SearchService + RagPipeline 统一从 config.yaml 读取
- `rag.pipeline.stages` 可配置阶段列表

## Phase 3: RAG 质量升级 ✅

### Step 3.1: 统一图服务层
- `UnifiedGraphService` — 统一 Neo4j / SQLite 图操作
- `AgenticRouter` — 智能查询路由 (structured / graph / hybrid)
- `QueryExecutor` — 结构化查询执行 (Cypher + SQL)
- `GraphTraversalService` — 多跳遍历
- `QueryExplainer` — 查询解释器

### Step 3.2: 标签层级 + 属性继承
- `TagHierarchyService` — 标签树管理
- `PropertySchemaService` — 属性模式定义
- `EffectivePropertyService` — 属性继承计算

### Step 3.3: 操作安全闭环
- `OperationLogRepository` + `OperationLogService`
- 操作日志记录 + 撤销支持
- 软删除 (deleted_at) — 全局过滤

### Step 3.4: RAG 诊断 + Agentic 路由
- 检索诊断信息 (route / retrieval stats / dropped candidates / warnings)
- Agentic Router 三模式: structured / graph / hybrid
- ChatView 前端诊断面板

## Phase 4: MCP 产品化升级 ✅

### Step 4.1: MCP 工具 Schema 标准化
- `_TOOL_METADATA` — 48 个工具元数据 (group / side_effect / requires_confirmation / short_desc)
- `_TOOL_ALIASES` — 42 个命名空间别名 (kb.*/wiki.*/graph.*/ops.*/memory.*)
- `_register_tool_aliases()` — 动态别名注册 (functools.wraps + 闭包)
- `kb_capabilities` 暴露 tool_metadata / tool_aliases

### Step 4.2: Agent Memory 工具
- Alembic 迁移: `agent_memory` 表 + FTS5 索引 + 同步触发器
- `AgentMemoryRepository` — CRUD + FTS5 搜索 + upsert 竞态防护
- `AgentMemoryService` — 6 个业务方法 + LLM/启发式双模式
- 6 个 MCP 工具: remember_fact / recall_facts / update_project_context / search_decisions / summarize_recent_changes / extract_tasks_from_doc
- Container 集成 + 31 个新测试

## Phase 5: 前端体验升级 ✅

### Step 5.1: 前端基础架构升级
- **React Router** — 8 路由 (/ /knowledge /knowledge/:id /import /chat /wiki /wiki/:id /graph /settings)
- **AuthProvider** + `useAuth()` hook — 统一认证状态
- **ToastProvider** + `useToast()` — 全局通知 (自动清理 timer)
- **ErrorBoundary** — 渲染错误捕获
- **共享组件**: Layout (侧边栏+Outlet), PageHeader, DataTable (分页/排序)
- **自定义 hooks**: useAuth, useApi, usePagination
- **api.ts 扩展**: apiPut, apiDelete, apiUpload, 401 自动跳转登录

### Step 5.2: Dashboard 首页
- 6 个统计卡片 (知识/Block/向量/Wiki/对话/Agent记忆)
- 最近导入任务列表
- 快捷操作入口 (导入/问答/Wiki/图谱)
- 后端新增 `/api/stats` 端点

### Step 5.3: 导入中心 + 知识 CRUD
- 文件拖拽上传 (多格式: PDF/Word/Excel/PPT/MD/TXT/CSV/JSON)
- URL 导入
- 批量任务进度轮询 (组件卸载时自动清理)
- 知识列表: DataTable + 分页 + 搜索 + 删除
- 知识详情页: 元信息 + 内容预览 + Block 列表

### Step 5.4: Wiki 编辑器 + 图谱可视化
- Wiki 页面创建 (内联表单)
- Wiki 编辑器 (title + Markdown textarea)
- 工作流状态转换 (draft→review→published→deprecated)
- Canvas 图谱可视化: 环形布局 + hover 高亮关联节点 + 信息面板

### Step 5.5: 设置持久化 + 安全模式
- 模型配置 (LLM / Embedding / Reranker) 读取/保存
- MCP 安全策略 (preview_only / local_confirm / token_required / disabled)
- 数据备份 / JSON 导出
- 系统信息面板

### Review 修复 (Phase 5)
- Toast setTimeout 组件卸载时清理 (HIGH)
- ImportView pollJobs 定时器泄漏修复 (HIGH)
- KnowledgeView useCallback + 正确依赖数组 (HIGH)
- LoginView 语义化 `<form>` + `aria-label` (MEDIUM)
- SettingsView 子组件直接用 useToast() 去除 prop drilling (MEDIUM)
- BackupTab 使用 getToken() 替代硬编码 key (MEDIUM)
- safeTags 提取到共享 utils/helpers.ts (LOW)

## 测试汇总

| 指标 | 数值 |
|------|------|
| Python 测试通过 | 534 |
| Python 测试失败 | 11 (全部预存在，非本次引入) |
| TypeScript 编译 | 零错误 |
| Vite Build | 成功 (292KB gzipped: 87KB) |
| Commit 总数 | 11 (Phase 1-5) |

## 新增文件清单

### 后端 (Phase 1-4)
- `config.example.yaml`
- `.github/workflows/ci.yml`
- `Dockerfile` (重写), `docker-compose.yml` (重写)
- `src/core/container.py`
- `src/repositories/` — 11 个仓库 (knowledge, conversation, wiki, graph, block, entity_ref, category, job, tag_relation, property_schema, operation_log, agent_memory)
- `src/services/agent_memory.py`
- `src/services/unified_graph.py`, `agentic_router.py`, `query_executor.py`, `graph_traversal.py`, `query_explainer.py`
- `src/services/tag_hierarchy.py`, `property_schema.py`, `effective_properties.py`
- `src/services/operation_log.py`
- `alembic/versions/f001_agent_memory.py`
- `tests/test_agent_memory.py` (31 个测试)

### 前端 (Phase 5)
- `client/src/components/` — Layout, PageHeader, DataTable, Toast, ErrorBoundary
- `client/src/hooks/` — useAuth, useApi, usePagination
- `client/src/utils/helpers.ts`
- `client/src/views/` — DashboardView, LoginView, ImportView, KnowledgeDetail, WikiDetail
- `client/src/views/` — KnowledgeView, ChatView, WikiView, GraphView, SettingsView (全部重写升级)
