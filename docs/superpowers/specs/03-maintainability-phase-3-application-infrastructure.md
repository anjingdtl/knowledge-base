# Spec 3：Answer、MCP、Container 与存储治理收束

> **建议路径：** `docs/superpowers/specs/2026-07-14-maintainability-phase-3-application-infrastructure.md`  
> **建议版本：** `v1.9.0`  
> **工期定位：** 应用层和基础设施边界收束。本期以第二工期稳定的 Retrieval Orchestrator 为基础，不再修改检索与 Wiki Serving 语义。

## 1. 前置条件

本 Spec 依赖前两期全部完成。

必须已经存在并稳定：

```text
SearchExecution
RetrievalOrchestrator
EvidenceOnlyPolicy
VerifiedPolicy
VerifiedProvider
统一 SearchService Facade
Search/Ask/Wiki 契约测试
```

如果 Retrieval 仍处于 Shadow 验证阶段，本期只能进行文档和测试准备，不得切换 Answer 主流程。

## 2. 目标

本期分四个连续子阶段执行：

```text
3A 统一 Answer 编排
→ 3B 拆分 MCP Server
→ 3C 收束 Container 依赖
→ 3D 治理数据库迁移与 Legacy 入口
```

四个子阶段必须顺序执行，每个子阶段独立 PR、独立验收、独立回滚。

本期最终目标：

- Ask 只有一个应用层编排入口；
- MCP 只负责协议适配；
- Container 不再成为所有功能的集中服务定位器；
- Core Evidence、Verified Serving、Authoring 和 Experimental 有清晰边界；
- Alembic 成为新增 Schema 的唯一权威；
- 全局兼容入口进入明确弃用流程。

## 3. 非目标

本期明确不做：

- 不修改 Retrieval 排序；
- 不修改 Wiki Serving Gate；
- 不修改 Claim 语义；
- 不修改 Canonical Wiki 权威关系；
- 不新增 MCP 工具；
- 不更换 FastMCP；
- 不更换 SQLite；
- 不更换向量库；
- 不一次性重写 `db.py`；
- 不立即删除全部 Legacy；
- 不把 Graph 或 Memory 纳入 Core；
- 不开启 Auto Publish。

# 子阶段 3A：统一 Answer 编排

## 4. 目标架构

创建：

```text
src/answering/models.py
src/answering/service.py
src/answering/context_builder.py
src/answering/generation.py
tests/answering/
```

目标流程：

```text
Question
→ RetrievalOrchestrator
→ SearchExecution
→ ContextBuilder
→ Generation
→ AnswerExecution
```

定义：

```python
@dataclass(frozen=True)
class AnswerExecution:
    answer: str
    answer_mode: str
    sources: tuple[dict, ...]
    claims_used: tuple[dict, ...]
    raw_evidence_used: tuple[dict, ...]
    conflicts: tuple[dict, ...]
    fallbacks: tuple[dict, ...]
    warnings: tuple[str, ...]
    trace_id: str
```

## 5. AnswerService 职责

```text
调用 RetrievalOrchestrator
构造有限上下文
调用 LLM
处理 LLM 失败和超时
构造 Evidence Summary fallback
输出 AnswerExecution
```

不得负责：

- 直接执行数据库搜索；
- 直接查询 Wiki Repository；
- 重新应用 Serving Gate；
- 重新执行 Retrieval；
- 构造 MCP Envelope；
- 注册 MCP Tool。

## 6. Answer Shadow 验证

提出配置：

```yaml
answer:
  orchestrator: legacy
```

支持：

```text
legacy
shadow
unified
```

对比内容：

- `answer_mode`；
- source ID；
- Claim ID；
- Raw Evidence ID；
- Conflict；
- Fallback；
- Citation 完整性；
- no-answer 判断；
- timeout 判断。

不要求 LLM 文本逐字一致。

切换门槛：

```text
answer_mode 一致率 = 100%
Claim 集合一致率 = 100%
Raw Evidence 集合一致率 = 100%
Conflict 一致率 = 100%
Fallback 一致率 = 100%
Citation Completeness = 100%
no-answer 决策一致
```

# 子阶段 3B：拆分 MCP Server

## 7. 目标目录

```text
src/mcp/
├── server.py
├── runtime.py
├── auth.py
├── policies.py
├── envelopes.py
├── tool_registry.py
├── tool_profiles.py
└── tools/
    ├── retrieval.py
    ├── ingest.py
    ├── administration.py
    ├── wiki.py
    ├── graph.py
    └── memory.py
```

现有声明式 Tool Registry 和 Profile 机制继续使用，不重写注册模型。

## 8. 工具分组

### retrieval.py

```text
ping
kb_capabilities
search
ask
read
list_knowledge
```

### ingest.py

```text
index_path
create_ingest_job
get_job
list_jobs
reindex_all
cancel_job
```

### administration.py

```text
create
update
delete
restore
operation logs
undo
```

### wiki.py

所有 Wiki 管理和 Authoring 工具。

### graph.py / memory.py

实验能力，继续受 Tool Profile 控制。

## 9. MCP 边界

MCP 工具只能：

```text
参数校验
权限与写策略检查
调用 Application Service
将结果转换为 MCP Envelope
```

不得：

- 编排 Retrieval；
- 查询 Wiki Repository；
- 执行数据库 CRUD 细节；
- 调用 Reranker；
- 直接拼接 Answer Context。

最终 `src/mcp_server.py` 仅保留兼容入口：

```python
from src.mcp.server import main, mcp

__all__ = ["main", "mcp"]
```

## 10. MCP 验收

必须保持：

- 工具名不变；
- 参数 Schema 不变；
- Tool Profile 不变；
- annotations 不变；
- Write Policy 不变；
- stdio 启动方式不变；
- HTTP 启动方式不变；
- timeout envelope 不变；
- Windows Smoke 通过；
- Docker Health 通过。

# 子阶段 3C：收束 Container 依赖

## 11. 服务分组

将 Container 服务按能力分组：

```text
CoreEvidenceServices
VerifiedServingServices
AuthoringServices
ExperimentalServices
```

### CoreEvidenceServices

```text
database infrastructure
knowledge/block repositories
embedding
raw retriever
retrieval orchestrator
answer service
citation services
path indexer
```

### VerifiedServingServices

```text
wiki read repository
serving gate
verified provider
freshness resolver
conflict resolver
```

### AuthoringServices

```text
claim extraction
canonical writes
projection writes
maintenance
review
rebuild
```

### ExperimentalServices

```text
graph
agent memory
plugins
experimental UI services
```

重要约束：

```text
Verified Serving 是正式产品能力
Authoring 是受控写能力
Graph 和 Memory 才属于实验能力
```

## 12. 禁止反向依赖

新增架构测试，禁止：

```text
retrieval → mcp
answering → mcp
repositories → container
storage → services
core evidence → graph
core evidence → memory
verified serving → authoring
```

服务构造依赖必须显式注入。

新代码不得调用：

```text
get_active_container()
_get_container()
Database._instance
```

兼容模块可以暂时保留。

# 子阶段 3D：数据库和 Legacy 治理

## 13. 冻结运行时 `_migrate()`

本期第一步不是删除 `_migrate()`，而是冻结。

创建：

```text
docs/architecture/database-migration-policy.md
tests/test_database_migration_policy.py
```

规则：

```text
现有 _migrate() 只用于历史兼容
不得新增字段
不得新增表
不得增加表重建
所有新 Schema 变化必须通过 Alembic revision
```

## 14. Alembic 权威化

分步执行：

1. 建立当前 Schema 基线；
2. 验证空数据库升级；
3. 验证上一正式版本数据库升级；
4. 增加 migration version 检查；
5. 写模式启动前检查迁移状态；
6. 迁移失败时允许进入只读诊断模式；
7. 不在同一 PR 中搬迁大量 CRUD。

## 15. Repository 渐进抽取

按以下顺序，每个领域独立 PR：

```text
Knowledge Repository
→ Block Repository
→ Job Repository
→ Wiki Read Repository
→ Wiki Write Repository
→ Maintenance Repository
```

原则：

- 一次只迁移一个领域；
- `Database` Facade 暂时保留；
- 新代码使用 Repository；
- 旧代码可通过 Facade 转发；
- 不一次性拆空 `db.py`；
- 不改变事务语义；
- 不改变 Canonical/Projection 权威关系。

## 16. Legacy 弃用登记

创建：

```text
docs/migration/deprecation-register.md
```

至少登记：

| 入口 | 替代方案 | 弃用版本 | 最早删除版本 |
| --- | --- | --- | --- |
| `Database._instance` | Repository 注入 | v1.9.0 | v2.0 |
| `get_active_container()` | 构造注入 | v1.9.0 | v2.0 |
| Legacy Retrieval | RetrievalOrchestrator | v1.8.x | v2.0 |
| Legacy Answer | AnswerService | v1.9.0 | v2.0 |
| Legacy MCP aliases | 标准工具名 | 已弃用 | v2.0 |

删除任何 Legacy 前必须满足：

```text
生产调用数 = 0
仅 compatibility 模块仍引用
至少经过一个正式版本
迁移文档已发布
回滚路径已验证
```

## 17. Agent 执行顺序

### 3A

- [ ] 核验二期准入条件；
- [ ] 定义 `AnswerExecution`；
- [ ] 建立 AnswerService；
- [ ] 建立 ContextBuilder 和 Generation 适配层；
- [ ] 运行 Answer Shadow；
- [ ] 达到门槛后切换 unified；
- [ ] 保留旧 Answer 一个正式版本。

### 3B

- [ ] 抽取 MCP Runtime、Auth、Policies、Envelopes；
- [ ] 按领域拆分工具模块；
- [ ] 保持 Tool Registry 和 Profile；
- [ ] 将 `mcp_server.py` 收束为兼容入口；
- [ ] 运行 MCP、Windows、Docker 验收。

### 3C

- [ ] 建立四类 Service Provider；
- [ ] 提供旧 Container 属性代理；
- [ ] 迁移新代码到显式依赖；
- [ ] 建立 Import Boundary 门禁；
- [ ] 移除业务服务中的全局 Container 回查。

### 3D

- [ ] 冻结 `_migrate()`；
- [ ] 建立 Alembic 当前基线；
- [ ] 验证旧数据库升级；
- [ ] 按领域抽取 Repository；
- [ ] 建立弃用登记；
- [ ] 将全局入口限制在 compatibility 模块；
- [ ] 生成 v1.9.0 验收与迁移报告。

## 18. 本期总体验收标准

### Answer

- 只有一个正式 Ask Orchestrator；
- `AnswerExecution` 成为应用层统一契约；
- no-answer、timeout、conflict 和 fallback 行为不变。

### MCP

- `mcp_server.py` 不含业务编排；
- Tool Contract 和 Profile 完全兼容；
- stdio、HTTP、Windows、Docker 验证通过。

### Container

- Core、Verified、Authoring、Experimental 边界清楚；
- Core Retrieval 不依赖 Graph、Memory；
- Verified Serving 不依赖 Authoring；
- 新业务服务不反查全局 Container。

### 数据库

- 新 Schema 只允许使用 Alembic；
- `_migrate()` 不再增长；
- 旧数据库升级测试通过；
- Repository 按领域渐进迁移；
- Canonical Wiki 仍是权威，Projection 仍可重建。

### 质量

- 全量测试通过；
- Ruff 通过；
- MyPy 通过；
- Retrieval Eval 不下降；
- Hybrid Eval 不下降；
- Wiki Serving Contract 通过；
- MCP Contract 通过；
- 数据库升级与恢复测试通过。

## 19. 回滚策略

本期每个子阶段独立回滚。

### 3A Answer

```yaml
answer:
  orchestrator: legacy
```

### 3B MCP

保留 `mcp_server.py` 兼容入口，可恢复旧注册导入。

### 3C Container

保留旧属性代理：

```python
@property
def search_service(self):
    return self.core.search_service
```

### 3D 数据库

- 不删除旧 Schema；
- 不在首次切换时删除 `_migrate()`；
- Database Facade 保留；
- Alembic revision 必须提供备份和升级验证；
- Repository 迁移可按领域逐个回滚。

## 20. 与第二工期的承接关系

本期只通过以下接口调用 Retrieval：

```text
RetrievalOrchestrator.search()
SearchExecution
```

本期不得直接依赖：

- RawRetriever 私有实现；
- VerifiedProvider 内部实现；
- Wiki Repository 查询细节；
- Legacy Search 私有方法。

这样第二工期可以继续优化 Retrieval，而不要求 MCP 或 Answer 同步修改。

## 21. 三个工期完成后的最终关系

```text
第一工期
冻结行为
消除请求状态
        │
        ▼
第二工期
统一 Retrieval
隔离 Wiki Serving
        │
        ▼
第三工期
统一 Answer
拆分 MCP
收束 Container
治理数据库和 Legacy
```

核心接口承接：

```text
第一工期输出：
SearchExecution + 行为契约
        │
        ▼
第二工期输出：
RetrievalOrchestrator + Policies
        │
        ▼
第三工期输出：
AnswerService + MCP Adapters + 清晰依赖边界
```

三个工期之间不存在数据库格式上的强制连续迁移。任何一期完成后都可以暂停，项目仍应保持完整可用。

## 22. 推荐提交拆分

```text
feat(answer): introduce unified AnswerExecution contract
feat(answer): add shadow comparison and cutover mode
refactor(mcp): extract runtime auth policies and envelopes
refactor(mcp): split tools by domain
refactor(mcp): reduce legacy server to compatibility entrypoint
refactor(container): group core verified authoring and experimental services
test(architecture): enforce dependency boundaries
docs(database): freeze runtime migration policy
feat(database): establish Alembic schema baseline
refactor(storage): extract repositories one domain at a time
docs(migration): register legacy deprecations
docs(release): publish v1.9.0 maintainability acceptance report
```
