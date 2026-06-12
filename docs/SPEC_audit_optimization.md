# ShineHeKnowledge 审计优化 Spec 方案

> **版本**: v1.0 | **日期**: 2026-06-12 | **状态**: Draft
> **目标版本路线**: v1.3 → v1.4 → v1.5 → v2.0
> **核心理念**: 产品化、稳定化、工程化 — 不盲目加功能，而是让已有能力可靠运行

---

## 一、项目现状评估

### 1.1 项目概览

ShineHeKnowledge v1.2.0 是一个本地优先的知识库系统，支持多模态文档管理、RAG 智能问答。三种运行模式（GUI/API/MCP）共享同一服务层，通过 AppContainer 依赖注入。

**技术栈**:
- 后端: Python 3.12+, FastAPI, PySide6, FastMCP, SQLite + FTS5 + sqlite-vec
- 前端: React 19 + Vite 8 + TypeScript 6 + Tailwind CSS 4
- AI: OpenAI 兼容客户端 (Embedding/LLM), bge-m3 向量模型

### 1.2 审计发现总览

| 维度 | 严重 | 高 | 中 | 低 | 总计 |
|------|------|------|------|------|------|
| 配置安全 | 1 | 4 | 3 | 2 | 10 |
| 数据层 | 0 | 3 | 2 | 0 | 5 |
| RAG 管线 | 0 | 2 | 3 | 1 | 6 |
| MCP Server | 0 | 1 | 1 | 0 | 2 |
| 前端 | 0 | 2 | 3 | 0 | 5 |
| Docker/CI/依赖 | 0 | 3 | 3 | 2 | 8 |
| **总计** | **1** | **15** | **15** | **5** | **36** |

---

## 二、分维度审计详情

### 2.1 配置安全 (Security)

#### 2.1.1 [CRITICAL] config.yaml 明文密码被 Git 追踪

- **文件**: `config.yaml:118`
- **现状**: Neo4j 密码 `neo4j123` 以明文存储在 config.yaml 中，且该文件被 git 追踪
- **风险**: 凭据泄露，任何有仓库访问权限的人都能看到密码
- **修复方案**:
  1. 创建 `config.example.yaml`，密码替换为占位符 `YOUR_NEO4J_PASSWORD`
  2. 将 `config.yaml` 加入 `.gitignore`
  3. 将 `graph_backend.password` 加入 `_SECRET_KEYS`，纳入 keyring 管理
  4. 脚本 `fast_migrate.py` 和 `fast_migrate_edges.py` 中硬编码的凭据改为从 Config 读取

#### 2.1.2 [HIGH] _SECRET_KEYS 未覆盖 graph_backend.password

- **文件**: `src/utils/config.py:23`
- **现状**: `_SECRET_KEYS` 仅包含 `llm.api_key`, `embedding.api_key`, `reranker.api_key`
- **风险**: Neo4j 密码不在 keyring 保护范围，save() 时会原样写入明文
- **修复方案**:
  ```python
  _SECRET_KEYS = {
      "llm.api_key",
      "embedding.api_key",
      "reranker.api_key",
      "graph_backend.password",
      "api.jwt_secret",
  }
  ```

#### 2.1.3 [HIGH] 缺少 config.example.yaml

- **现状**: 不存在示例配置文件，新用户无法了解需要哪些配置项
- **修复方案**: 从 config.yaml 生成，敏感值替换为占位符，添加注释说明

#### 2.1.4 [HIGH] .gitignore 未排除 config.yaml

- **现状**: `.gitignore` 排除了 `.env` 和 `/data/`，但未排除 config.yaml
- **修复方案**: 添加 `config.yaml` 到 `.gitignore` 和 `.stignore`

#### 2.1.5 [HIGH] 迁移脚本硬编码凭据

- **文件**: `scripts/fast_migrate.py:15`, `scripts/fast_migrate_edges.py:14`
- **现状**: `auth=("neo4j", "neo4j123")` 硬编码在脚本中
- **修复方案**: 从 Config 对象读取: `auth=(Config.get('graph_backend.user'), Config.get('graph_backend.password'))`

#### 2.1.6 [MEDIUM] keyring 降级路径无警告

- **文件**: `src/utils/config.py:188-191`
- **现状**: keyring 不可用时 API Key 会照旧写入 config.yaml 明文文件，无任何警告
- **修复方案**: 降级路径添加 `logger.warning()`；支持环境变量替代（如 `SHINEHE_LLM_API_KEY`）

#### 2.1.7 [MEDIUM] JWT Token 有效期 24h 偏长

- **文件**: `src/api/auth.py:17`
- **现状**: `ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24`
- **修复方案**: 设为可配置项 `config.yaml → api.token_expire_minutes`，默认缩短到 4h，加 refresh token

#### 2.1.8 [MEDIUM] 速率限制仅覆盖登录

- **文件**: `src/api/routes/rate_limiter.py:22`
- **现状**: 仅 login 接口有限速，注册接口无限制
- **修复方案**: 对注册接口也添加速率限制，参数可配置化

### 2.2 数据层 (Data Layer)

#### 2.2.1 [HIGH] Database 2128 行 God Class

- **文件**: `src/services/db.py`
- **现状**: ~80 个 @classmethod 方法，全局单例 + 类级 `_conn` + `threading.local` 每线程连接
- **风险**: 所有服务直接依赖全局状态，无法独立测试、无法多实例、无法多租户
- **修复方案**: 逐步将类方法迁移到 Repository 实例方法，保留 DatabaseCompat 过渡层

**关键发现**:
- `Database.__new__` 双模式: 无参 → 全局单例；有 db_path → 实例化
- `_write_lock` 是 threading.Lock（非可重入），嵌套调用会死锁
- `_container` 类属性是 DI 反向引用，形成双向耦合
- Container 中 `container.db` 实际是 Database 类本身，不是实例

#### 2.2.2 [HIGH] VectorStore 双模式单例

- **文件**: `src/services/vectorstore.py`
- **现状**: `__new__` 分岔 — 有 db 参数 → DI 实例；无参 → 全局单例（回退到 Database._instance）
- **风险**: search() 方法懒加载 EmbeddingService() 隐式单例，无法 mock
- **修复方案**: 统一为纯 DI 实例模式，删除全局单例路径

#### 2.2.3 [HIGH] RAG 管线依赖全局状态

- **文件**: `src/services/rag_pipeline.py`
- **现状**: 多处通过 `_get_container_service()` 或直接引用 Database 全局对象
- **风险**: 无法隔离测试、无法多实例
- **修复方案**: 构造函数注入所有依赖

**RAG 管线依赖注入分析**:

| 阶段 | 可配置 | DI 质量 | 可独立测试 | 说明 |
|------|--------|---------|------------|------|
| QueryRewriteStage | ✅ | 糟糕 (全局 Database) | ❌ | 直接访问 Database 全局 |
| WikiRetrievalStage | ✅ | 糟糕 (全局 Database) | ❌ | 直接访问 Database 全局 |
| VectorSearchStage | ✅ | 糟糕 (内联创建 5 个服务) | ❌ | 内联构造 HybridSearcher 等 |
| RerankStage | ✅ | 糟糕 (_get_container_service) | ❌ | 通过 Database._container 反向引用 |
| GenerateStage | ✅ | 糟糕 (全局 Database + _get_container_service) | ❌ | 双重全局依赖 |
| PostProcessStage | ✅ | 优秀 (无外部依赖) | ✅ | 唯一干净的阶段 |

**缺失特性**:
- ❌ Evidence compression（证据压缩）: 完全未实现
- ⚠️ Parent-Child Retrieval: 仅部分实现（上下文注解，非真正的 Small-to-Big 检索）
- ❌ 阶段级单元测试: 无独立测试文件
- ❌ RAGService.query_stream() 重复了约 90 行管线逻辑，与 RagPipeline 分叉

#### 2.2.4 [MEDIUM] RagContext 无类型安全

- **文件**: `src/services/rag_pipeline.py`
- **现状**: `RagContext.metadata` 是 `dict[str, Any]`，各阶段随意读写任意 key
- **修复方案**: 定义显式字段或 TypedDict，禁止任意 key 访问

#### 2.2.5 [MEDIUM] 配置驱动不一致

- SearchService 读 `rag.enable_query_rewriting` 和 `rag.enable_rerank`
- RagPipeline 各阶段读各自 `enabled` 字段
- 两套独立配置路径控制同一逻辑功能

### 2.3 MCP Server

#### 2.3.1 [HIGH] MCP 写操作零认证

- **文件**: `src/mcp_server.py`
- **现状**: 41 个工具全部无认证。文件头注释明确说明依赖传输层信任模型
- **工具分类**: 读 25 / 写 14 / 破坏性 4
- **风险**: HTTP 模式下本地端口暴露，任何可达的客户端都能执行写/删操作
- **修复方案**:
  ```yaml
  mcp:
    write_policy: preview_only | local_confirm | token_required | disabled
    allow_http_write: false
    bind_host: 127.0.0.1
  ```

**MCP 写操作现状**:

| 工具 | 认证 | dry_run | 操作日志 | undo |
|------|------|---------|----------|------|
| create | ❌ | ✅ | ✅ | ✅ |
| update | ❌ | ✅ | ✅ | ✅ |
| delete | ❌ | ✅ | ✅ | ✅ (软删除) |
| ingest_file | 路径白名单 | ✅ | ✅ | ✅ |
| ingest_url | ❌ | ✅ | ✅ | ✅ |
| save_to_wiki | ❌ | ❌ | ✅ | ❌ |
| wiki_* (6个) | ❌ | ❌ | ✅ | ❌ |
| undo_operation | ❌ | ❌ | ✅ | N/A |
| cancel_* (2个) | ❌ | ❌ | ❌ | ❌ |
| reindex_all | ❌ | ✅ | ✅ | ❌ |

#### 2.3.2 [MEDIUM] 工具 Schema 标注不完整

- **现状**: 41 工具中 21 个有 MCP annotations，20 个（主要是写/wiki 工具）缺少标注
- **修复方案**: 为所有工具补齐 `readOnlyHint`, `destructiveHint`, `idempotentHint`

#### 2.3.3 缺失的 Agent Memory 工具

当前无任何 Agent 记忆/决策持久化工具:
- `remember_fact` — 跨会话持久化事实知识
- `search_decisions` — 搜索架构/技术决策
- `summarize_recent_changes` — 总结近期变更
- `update_project_context` — 更新项目上下文

### 2.4 前端 (Frontend)

#### 2.4.1 [HIGH] JWT 存储在 localStorage

- **文件**: `client/src/api.ts`
- **现状**: JWT 存于 `localStorage['shinehe_api_token']`，XSS 可窃取
- **附属问题**: 无 Token 刷新、无 401 拦截重定向、LoginView 绕过 api.ts 用原始 fetch
- **修复方案**: 升级为 HttpOnly Cookie + CSRF Token，至少提供"本地/远程"双安全策略

#### 2.4.2 [HIGH] 前端仅覆盖后端 15% API

- **现状**: 后端 10 个路由组，前端仅使用 auth、knowledge（只读）、chat（仅 ask）、wiki（pages/lint/repair）
- **完全未使用的路由**: tags、properties、query、graph、jobs（部分）
- **修复方案**: 按 Phase 5 信息架构逐步补齐

**前端缺失功能清单** (20 项):
1. Dashboard 首页 — 无概览统计
2. 导入中心 — 无文件上传/拖拽/批量导入
3. 知识 CRUD — KnowledgeView 只读，无创建/编辑/删除
4. 知识详情 — 无法查看完整内容和 blocks
5. 图谱可视化 — GraphView 是占位符
6. 设置持久化 — SettingsView 无保存/加载逻辑
7. 高级搜索 — 无标签/类型过滤、排序
8. 对话历史 — ChatView 无历史加载
9. 任务管理 — 无异步任务 UI
10. 分页 — 硬编码 page=1&page_size=50
11. 流式响应 — ChatView 等待完整响应
12. 标签管理 — 无标签 CRUD UI
13. 属性管理 — 无属性 schema UI
14. Wiki 编辑器 — 无创建/编辑 Wiki 页面
15. Wiki 详情 — 无全文、版本历史、反向链接
16. Query DSL — 无结构化查询构建器
17. 通知系统 — 无全局 Toast
18. 响应式布局 — 无移动端适配
19. 暗色模式 — CSS 变量支持但无切换
20. 错误边界 — 无 React ErrorBoundary

### 2.5 Docker / CI / 依赖管理

#### 2.5.1 [HIGH] 零 CI/CD 配置

- **现状**: 无 `.github/workflows`，无任何 CI 配置
- **修复方案**: 引入最小 CI: ruff check + pytest + frontend build + docker build

#### 2.5.2 [HIGH] requirements.txt 与 pyproject.toml 不同步

- **现状**: 4 个包在代码中使用但缺失于配置文件:
  - `charset-normalizer` — 仅在 requirements.txt，不在 pyproject.toml
  - `python-pptx` — 仅在 requirements.txt，不在 pyproject.toml
  - `pikepdf` — 仅在 pyproject.toml，不在 requirements.txt
  - `pycryptodome` — 仅在 pyproject.toml，不在 requirements.txt
- **修复方案**: 以 pyproject.toml 为唯一依赖声明，删除 requirements.txt 或自动生成

#### 2.5.3 [HIGH] Dockerfile 问题

- 安装 requirements.txt 包含 GUI（PySide6）、dev（pytest）等不必要依赖
- 以 root 运行，无 USER 指令
- 无多阶段构建，无 HEALTHCHECK
- docker-compose.yml 硬编码版本 1.2.0，无 healthcheck，无资源限制

---

## 三、优化目标与优先级矩阵

### 3.1 优先级排序 (P0 → P2)

| 优先级 | 任务 | 收益 | 工作量 | 风险 |
|--------|------|------|--------|------|
| **P0** | 配置安全改造 | 立即降低安全风险 | 2d | 低 |
| **P0** | MCP HTTP 写操作默认禁用 | 避免端口暴露误写 | 1d | 低 |
| **P0** | Docker 拆分 api/mcp/desktop profile | 减小镜像和部署复杂度 | 2d | 低 |
| **P1** | 引入 CI (ruff + pytest + build) | 防止功能越多越难维护 | 1d | 低 |
| **P1** | 统一依赖来源 (pyproject.toml) | 消除版本漂移 | 1d | 低 |
| **P1** | Database 去 God Class | 提升可测性和架构质量 | 5d | 中 |
| **P1** | RAG 管线构造器注入 | 多库/多租户/测试 mock | 3d | 中 |
| **P1** | 建立 RAG Eval 基准集 | 后续优化有量化依据 | 2d | 低 |
| **P1** | 前端检索诊断面板 | RAG 结果可解释 | 3d | 低 |
| **P2** | evidence_compress 阶段 | 提升回答质量 | 2d | 低 |
| **P2** | Parent-Child Retrieval | 长/PDF/表格问答效果 | 5d | 高 |
| **P2** | MCP Agent Memory 工具 | Agent 本地长期记忆 | 3d | 低 |
| **P2** | MCP 工具 Schema 标准化 | 产品化基础 | 2d | 低 |
| **P2** | 前端信息架构重构 | 用户体验跃升 | 10d | 中 |
| **P2** | Windows Installer + Auto Update | 产品化分发 | 3d | 低 |

### 3.2 版本路线图

```
v1.3 (稳定性与安全版)
  ├── Phase 1: 基础工程治理 (P0 任务)
  │     ├── 配置安全改造
  │     ├── MCP 写操作策略
  │     ├── Docker profile 拆分
  │     ├── 引入 CI
  │     └── 统一依赖来源
  └── 基础测试补齐 + 安全部署说明

v1.4 (RAG 质量版)
  ├── Phase 2: 架构内核重构 (P1 架构任务)
  │     ├── Database 去 God Class
  │     ├── RAG 管线依赖注入
  │     └── 服务层目录重组 (可选)
  ├── Phase 3: RAG 质量升级
  │     ├── RAG Eval 基准集
  │     ├── 检索诊断面板
  │     ├── evidence compression
  │     └── Parent-Child Retrieval
  └── 前端 source graph + block context 展示

v1.5 (Agent 友好版)
  ├── Phase 4: MCP 产品化升级
  │     ├── MCP 工具 Schema 标准化
  │     ├── Agent Memory 工具
  │     ├── preview/undo 细粒度
  │     └── 操作权限分级
  └── 项目上下文自动沉淀到 Wiki

v2.0 (产品化版)
  ├── Phase 5: 前端体验升级
  │     ├── Dashboard 首页
  │     ├── 导入中心
  │     ├── 知识 CRUD + 详情
  │     ├── Wiki 编辑器
  │     ├── 图谱可视化
  │     └── 设置持久化
  ├── 多用户/多知识库
  ├── 权限与空间隔离
  ├── 数据备份/迁移
  └── 可观测性 (日志/指标/追踪)
```

---

## 四、非功能性要求

### 4.1 安全要求

| ID | 要求 | 验收标准 |
|----|------|----------|
| SEC-01 | 配置文件不含明文密码 | config.yaml 被 gitignore，config.example.yaml 无真实密码 |
| SEC-02 | 所有 secret 通过 keyring 管理 | _SECRET_KEYS 覆盖所有敏感字段，降级时有警告 |
| SEC-03 | MCP 写操作可管控 | write_policy 四级策略生效，HTTP 模式默认禁用写 |
| SEC-04 | JWT 安全存储 | 至少提供 HttpOnly Cookie 选项，本地模式可 localStorage |
| SEC-05 | Docker 不以 root 运行 | Dockerfile 添加非 root USER |

### 4.2 质量要求

| ID | 要求 | 验收标准 |
|----|------|----------|
| QUA-01 | CI 绿色 | ruff check + pytest + frontend build + docker build 全通过 |
| QUA-02 | 依赖单一来源 | pyproject.toml 为唯一声明，requirements.txt 可自动生成 |
| QUA-03 | 核心服务可独立测试 | Database/VectorStore/RAG Pipeline 可通过构造器注入 mock |
| QUA-04 | RAG 效果可评测 | Eval 基准集覆盖 ≥4 类查询，指标可量化追踪 |
| QUA-05 | 代码覆盖率 | 核心 services/ 覆盖率 ≥60% |

### 4.3 性能要求

| ID | 要求 | 验收标准 |
|----|------|----------|
| PER-01 | RAG 响应延迟 | P50 < 3s, P95 < 10s (本地) |
| PER-02 | Docker 镜像大小 | API 镜像 < 500MB (不含 GUI 依赖) |
| PER-03 | 前端首屏加载 | < 2s (本地) |

---

## 五、约束与风险

### 5.1 约束

1. **本地优先**: 所有功能必须支持纯离线运行，不强制依赖外部服务
2. **向后兼容**: 数据库迁移必须通过 Alembic，不破坏现有用户数据
3. **渐进式改造**: 每个 Phase 完成后可独立部署，不阻塞其他 Phase
4. **三种模式共存**: GUI/API/MCP 三种入口模式共享同一服务层不变

### 5.2 风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Database 重构引入回归 | 高 | 高 | 保留 DatabaseCompat 过渡层，渐进迁移 |
| RAG 管线重构影响问答效果 | 中 | 高 | Eval 基准集先于重构建立，每次变更前后对比 |
| 前端重构工作量大 | 高 | 中 | 按功能模块分批，优先补齐高价值功能 |
| Docker 拆分影响现有部署 | 低 | 中 | 保留 docker-compose.yml 向后兼容 |
| MCP 认证增加使用复杂度 | 中 | 低 | 默认 stdio 无认证，HTTP 模式才启用 |

---

## 六、验收标准

### Phase 1 验收
- [ ] `config.yaml` 被 gitignore，`config.example.yaml` 存在且无真实密码
- [ ] 所有 secret 通过 keyring 管理，降级时有环境变量替代 + 警告日志
- [ ] MCP 配置 `write_policy` 可配且 HTTP 模式默认 token_required
- [ ] Docker 三镜像 profile 构建成功
- [ ] CI pipeline 绿色 (ruff + pytest + build)
- [ ] pyproject.toml 为唯一依赖声明

### Phase 2 验收
- [ ] Database 不再有 @classmethod 全局单例入口
- [ ] 所有 Repository 通过 Container 注入 Database 实例
- [ ] RAG 管线各阶段通过构造器接收依赖
- [ ] 核心服务有独立单元测试（不依赖真实数据库）

### Phase 3 验收
- [ ] RAG Eval 基准集 ≥4 类查询，每类 ≥10 条
- [ ] 检索诊断面板展示 route/hits/dropped_candidates
- [ ] evidence_compress 阶段生效，context token 减少 ≥30%
- [ ] Parent-Child Retrieval 基本可用

### Phase 4 验收
- [ ] MCP 工具全部有 input/output schema + side_effect 标注
- [ ] Agent Memory 工具 (remember_fact, search_decisions 等) 可用
- [ ] 写操作支持 preview_only / local_confirm / token_required / disabled 四级策略

### Phase 5 验收
- [ ] Dashboard 首页展示知识库统计和健康分
- [ ] 导入中心支持拖拽上传 + URL 导入
- [ ] 知识库 CRUD 完整（创建/编辑/删除/详情）
- [ ] 图谱可视化基本可用
- [ ] 设置持久化正常工作
