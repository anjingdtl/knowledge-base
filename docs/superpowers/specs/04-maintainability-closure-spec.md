# ShineHe Knowledge 可维护性收尾改造 Spec

> **建议仓库路径：** `docs/superpowers/specs/04-maintainability-closure.md`  
> **适用基线：** v1.9.0 / `master`  
> **目标版本：** v1.10.0（允许经过 v1.9.1、v1.9.2 两个过渡版本）  
> **执行对象：** 可操作 Git、代码、测试、CI 的开发 Agent  
> **状态：** Proposed

---

## 1. 背景与复核结论

v1.8.1 至 v1.9.0 三期改造已经完成安全骨架：

- `SearchExecution` 消除了 `last_search_trace` / `last_disclose_claims` 请求共享状态；
- Search、Ask、Wiki Serving 行为契约和并发隔离测试已经建立；
- `RetrievalOrchestrator`、Policy、`RawRetriever`、`VerifiedProvider` 已形成结构边界；
- `AnswerService` / `AnswerExecution` 已成为 Ask 的应用层入口；
- MCP runtime、auth、envelope、policy 已从旧入口拆出；
- Container 已增加 Core / Verified / Authoring / Experimental 分组视图；
- `Database._migrate()` 已冻结，新 Schema 原则上要求使用 Alembic。

复核同时确认仍有以下未闭环事项：

1. `retrieval.orchestrator` 默认仍为 `legacy`；
2. Unified Policy 仍回调 `SearchService.execute_evidence_only()` / `execute_verified()`，最终执行旧私有管线；
3. `RawRetriever` 仍是适配器，不是 Raw Retrieval 算法权威；
4. Answer 的核心 assemble 逻辑仍位于 `src/services/verified_answer.py`；
5. MCP 工具实现仍集中在 `src/mcp/server.py`，`src/mcp/tools/*` 只是工具名登记；
6. MCP 工具中仍存在直接 SQL、`Database._instance` 和业务编排；
7. Container 分组只是只读视图，不拥有构造和生命周期；
8. `get_active_container()` 和全局 Container 兼容入口仍被保留；
9. Alembic 测试设置的临时 URL 未被 `alembic/env.py` 使用，失败场景可能被 skip；
10. 启动时尚未强制检查数据库是否位于 Alembic head；
11. Legacy Retrieval、Legacy Answer 和部分 Database Facade 尚未进入实际删除阶段。

本 Spec 的目的不是开启第四轮大重构，而是完成上述明确欠账，使 v1.9.0 的“安全过渡架构”真正收束为可长期维护的正式架构。

---

## 2. 总体目标

完成后必须达到：

1. Retrieval 的正式路径由 `RetrievalOrchestrator + Policy + RawRetriever + VerifiedProvider` 直接组成；
2. `SearchService` 退化为稳定 Facade，不再持有 Raw/Verified 主管线实现；
3. Answer 领域逻辑全部归属 `src/answering/`；
4. `src/mcp/server.py` 只负责 FastMCP 初始化、注册和生命周期，不包含具体工具业务实现；
5. MCP 工具不得直接访问 `Database._instance`、执行 SQL 或实现领域工作流；
6. Container 能力组开始拥有服务构造和生命周期，而不仅是只读视图；
7. 除 MCP runtime/compatibility 之外，生产代码不得依赖全局 Container；
8. Alembic 测试必须在显式临时数据库上真实执行，普通执行失败不得 skip；
9. 写模式启动时必须验证数据库 migration revision；
10. Legacy Retrieval 在经过至少一个正式版本的 Unified 默认运行后删除；
11. Search、Ask、Wiki、MCP 公共契约和检索质量不得退化。

---

## 3. 非目标

本轮禁止顺带开发：

- 新 MCP 工具；
- 新 Wiki Authoring 流程；
- 新 Graph 或 Memory 能力；
- 修改 RRF、Rerank、Citation、Serving Gate 算法；
- 更换 SQLite、sqlite-vec、FastMCP；
- 重写全部 Repository；
- 一次性清空 `db.py`；
- 修改 Canonical Wiki 与 Projection 的权威关系；
- 开启 Auto Publish；
- 以重构为名改变 Search/Ask 的公开字段；
- 在缺少真实 Shadow 证据时删除 Legacy。

---

## 4. 核心不变量

### 4.1 检索不变量

- Raw Evidence 始终可独立工作；
- Wiki 故障不得阻断 Raw；
- Claim 必须经过 Serving Gate；
- stale、unsupported、retracted Claim 不得进入可靠主结论；
- conflict 必须披露；
- Claim 进入答案必须携带可解析 Evidence；
- `SearchExecution` 字段语义保持不变。

### 4.2 MCP 不变量

- 工具名、参数 Schema、annotations、Tool Profile 不变；
- stdio、SSE、Streamable HTTP 启动路径不变；
- envelope 结构不变；
- Write Policy 不变；
- HTTP 写操作默认保护不变。

### 4.3 数据不变量

- Raw Source / Block 是最终证据底座；
- Canonical Wiki 是 Claim 权威层；
- SQLite Projection 不是事实源，必须可重建；
- 任何迁移不得静默丢失已有知识、Block、Claim、Evidence、Job 或操作日志。

---

## 5. 执行顺序与上下承接

必须按以下顺序执行：

```text
WP0 重新建立可执行验收基线
  ↓
WP1 Retrieval 真正收束并切换 Unified 默认
  ↓
WP2 Answer 归位与 MCP 工具实际分域
  ↓
WP3 Container / 全局状态收束
  ↓
WP4 Alembic 严格化与最小 Repository 补齐
  ↓
WP5 Legacy 删除与 v1.10.0 最终验收
```

承接规则：

- WP2 只能依赖 WP1 已稳定的 `RetrievalOrchestrator.search()` 和 `SearchExecution`；
- WP3 只能在 MCP 工具不再直接访问数据库后进行，否则无法安全收束 Container；
- WP4 可以提前准备测试，但写模式 migration gate 应在 WP3 的生命周期边界稳定后接入；
- WP5 必须在 Unified Retrieval 作为默认正式路径运行至少一个发布版本后执行；
- 任一工作包失败时必须停止，不得跳过门禁继续后续工作。

---

# WP0：重新建立可执行验收基线

## 6. 目标

在修改生产代码前，确认当前 `master` 的真实状态，而不是只引用历史验收报告。

## 6.1 必做任务

### WP0-T1：运行全量本地门禁

```bash
python -m pytest tests/ -q
ruff check src tests evals tools scripts
python -m mypy src tools --ignore-missing-imports
python evals/run_retrieval_eval.py --all --fake-embedding \
  --baseline evals/baselines/local.json --max-regression 0.05
python evals/run_hybrid_eval.py --strict
```

保存结果到：

```text
docs/superpowers/reviews/maintainability-closure-baseline.md
```

报告必须记录：

- Git SHA；
- Python 版本；
- 操作系统；
- passed / failed / skipped；
- Retrieval 和 Hybrid 指标；
- 所有 skip 原因；
- 当前配置中 Retrieval/Answer orchestrator 的有效值。

### WP0-T2：建立架构欠账快照

创建：

```text
tools/report_closure_debt.py
tests/architecture/test_closure_debt_baseline.py
```

至少统计：

- `src/mcp/server.py` 行数和其中定义的工具函数数量；
- `src/mcp/tools/*.py` 中真实工具实现数量；
- 生产代码中的 `Database._instance` 引用；
- 生产代码中的 `get_active_container()` 引用；
- `SearchService` 中 `_search_legacy_pipeline`、`_search_verified_hybrid` 的存在；
- `RawRetriever` 对 `SearchService` 私有/适配方法的调用；
- `src/answering` 对 `src.services.verified_answer` 的依赖；
- `alembic/env.py` 是否读取测试 URL；
- migration 测试中的 `pytest.skip` 路径。

初始测试可以只报告，不要求所有债务立即为 0。

## 6.2 验收

- 当前全量测试和质量指标形成可重复基线；
- 所有 skip 有明确原因；
- Agent 能用一条命令输出当前架构欠账清单；
- 若当前基线已失败，停止后续改造，先修复基线。

## 6.3 提交

```text
test(closure): capture executable maintainability baseline
```

---

# WP1：Retrieval 真正收束并切换 Unified 默认

## 7. 目标

让 Unified Retrieval 不再通过 Policy 回调 SearchService 旧主管线，而是由独立组件直接完成：

```text
RetrievalOrchestrator
├── EvidenceOnlyPolicy
│   └── RawRetriever
└── VerifiedPolicy
    ├── RawRetriever
    ├── VerifiedProvider
    └── VerifiedFusion
```

`SearchService` 最终只保留公共 Facade 和 Legacy 临时回滚入口。

## 7.1 目标目录

```text
src/retrieval/
├── models.py
├── orchestrator.py
├── raw_retriever.py
├── verified_provider.py
├── fusion.py
├── packaging.py
├── deadlines.py
└── policies/
    ├── base.py
    ├── evidence_only.py
    └── verified.py
```

## 7.2 必做任务

### WP1-T1：把 Raw 算法迁入 RawRetriever

迁移以下职责：

- query rewrite；
- hybrid search；
- BlockStore fallback；
- Knowledge FTS fallback；
- rerank；
- diversity；
- raw candidate packaging；
- stage trace 和 warning/fallback 记录。

迁移要求：

- 算法逐行等价迁移，不在同一 PR 优化算法；
- `RawRetriever` 通过显式构造参数接收依赖；
- 不接收整个 `SearchService`；
- 不调用 `run_raw_retrieval_adapter()`；
- 不访问 MCP、Graph、Memory、Wiki Authoring。

目标构造：

```python
RawRetriever(
    config=config,
    db=db,
    block_store=block_store,
    hybrid_searcher=hybrid_searcher,
    query_rewriter=query_rewriter,
    reranker=reranker,
    citation_builder_factory=...,
)
```

### WP1-T2：抽取 VerifiedFusion 与 Packaging

将以下逻辑移出 `SearchService._search_verified_hybrid()`：

- Claim candidate normalize；
- Raw candidate normalize；
- weighted fusion；
- Claim/Raw packaging；
- Evidence 必填检查；
- stale filter；
- conflict scan；
- Raw fallback；
- `SearchExecution` 组装。

创建：

```text
src/retrieval/fusion.py
src/retrieval/packaging.py
```

禁止修改现有评分公式。

### WP1-T3：Policy 直接组合组件

`EvidenceOnlyPolicy`：

```python
raw = self.raw_retriever.retrieve(...)
return build_evidence_only_execution(raw)
```

`VerifiedPolicy`：

```python
raw = self.raw_retriever.retrieve(...)
verified = self.verified_provider.serve(...)
return self.fusion.fuse(raw, verified, ...)
```

禁止：

```python
self._search_service.execute_evidence_only(...)
self._search_service.execute_verified(...)
```

### WP1-T4：SearchService 退化为 Facade

目标职责：

- 创建/持有 Orchestrator；
- `execute()`；
- `search()` 兼容；
- QuerySpec 入口可以暂时保留，但应通过独立 QueryExecutor；
- Legacy 实现只保留在明确标注的 compatibility 模块。

建议创建：

```text
src/compatibility/legacy_retrieval.py
```

将旧主管线移入 compatibility，而不是继续留在 `SearchService`。

### WP1-T5：建立真实 Shadow 验证

新增可聚合的 Shadow 报告，不只写单条日志：

```text
tools/run_retrieval_shadow_eval.py
evals/reports/retrieval-shadow-<date>.json
```

必须覆盖：

- Evidence-only；
- Verified Hybrid；
- Wiki 空；
- Wiki 异常；
- stale；
- conflict；
- query rewrite timeout；
- rerank timeout；
- QuerySpec 空结果回落。

门槛：

| 指标 | 要求 |
|---|---:|
| Top-5 source overlap | >= 95% |
| Eligible Claim 一致率 | 100% |
| Conflict 一致率 | 100% |
| Fallback 一致率 | 100% |
| Citation key 一致率 | 100% |
| Unsupported/Stale serving | 0 |
| 新增异常类型 | 0 |
| P95 延迟增幅 | <= 10% 或有明确解释 |

### WP1-T6：切换默认值

在 Shadow 门槛通过后：

```yaml
retrieval:
  orchestrator: unified
```

要求同步：

- `config.example.yaml`；
- 默认 Config；
- README；
- migration guide；
- release notes；
- tests 中默认模式断言。

Legacy 在本工作包结束时仍保留回滚能力。

## 7.3 验收

- Policy 不再依赖 `SearchService` 私有/适配主管线；
- `RawRetriever` 是 Raw 算法唯一权威实现；
- `VerifiedProvider` 是 Claim Serving 唯一入口；
- `SearchService` 不包含 `_search_legacy_pipeline` / `_search_verified_hybrid` 的正式实现；
- Unified 成为默认；
- Legacy 仍可通过配置回滚；
- Search/Ask/Wiki 契约全部通过；
- Retrieval/Hybrid Eval 不退化。

## 7.4 回滚

- 配置切回 `retrieval.orchestrator: legacy`；
- Legacy 实现位于 `src/compatibility/legacy_retrieval.py`；
- 不涉及数据库迁移；
- Unified 默认切换与 Legacy 删除不得发生在同一版本。

## 7.5 向 WP2 交付

WP2 只能依赖：

```text
RetrievalOrchestrator.search()
SearchExecution
AnswerService.execute()/ask()
```

不得依赖 RawRetriever 私有方法或 Legacy Retrieval。

## 7.6 提交建议

```text
refactor(retrieval): move raw pipeline into RawRetriever
refactor(retrieval): extract verified fusion and packaging
refactor(retrieval): make policies compose retrieval components directly
test(retrieval): add aggregate shadow cutover report
feat(config): make unified retrieval the default
```

---

# WP2：Answer 归位与 MCP 工具实际分域

## 8. 目标

1. Answer 领域逻辑全部归属 `src/answering/`；
2. MCP Server 只做协议与注册；
3. 每个 MCP 工具只调用 Application Service，不直接执行 SQL、访问全局数据库或编排领域逻辑。

## 8.1 Answer 归位

### WP2-T1：迁移 Answer Assembler

创建：

```text
src/answering/assembler.py
src/answering/citations.py
src/answering/fallbacks.py
```

迁移：

- `assemble_answer_payload`；
- Claim citation；
- Raw evidence used；
- conflict answer；
- no-answer；
- generation context；
- fallback hybrid/raw text；
- source packaging。

`src/services/verified_answer.py` 最终只允许：

```python
from src.answering.service import AnswerService
from src.answering.assembler import assemble_answer_payload  # 兼容导出
```

不得再持有主要业务逻辑。

### WP2-T2：清理无意义 Answer 模式

当前 legacy 与 unified 使用同一 assemble 路径。Agent 必须二选一：

- 若确无行为差异：删除 `answer.orchestrator` 的 legacy/shadow 复杂度，仅保留 unified；
- 若必须保留兼容：将 legacy 明确放入 `src/compatibility/legacy_answer.py`，并证明存在真实行为差异。

禁止保留“名称不同但代码相同”的伪双路径。

## 8.2 MCP 工具实拆

### WP2-T3：按领域迁移实际工具函数

目标目录：

```text
src/mcp/tools/
├── retrieval.py
├── ingest.py
├── administration.py
├── wiki.py
├── graph.py
├── memory.py
└── operations.py
```

每个文件必须包含真实工具实现和工具定义，而不是只有名称常量。

`src/mcp/server.py` 最终只保留：

- FastMCP 实例；
- lifespan；
- auth；
- 读取 ToolDefinition；
- 注册工具；
- prompt/resource 注册；
- `main`。

建议预算：

```text
src/mcp/server.py <= 500 行
单个 tools/*.py 原则上 <= 800 行
```

超过预算必须说明原因，不能为满足数字机械拆函数。

### WP2-T4：引入 Application Services

对目前在 MCP 中直接完成的业务建立服务，例如：

```text
src/application/knowledge_commands.py
src/application/ingest_commands.py
src/application/operation_commands.py
src/application/tagging_service.py
src/application/wiki_commands.py
```

MCP 工具只做：

```text
参数校验
→ 权限/Write Policy
→ 调用 Application Service
→ 转换 Envelope
```

### WP2-T5：消除 MCP 中直接数据库访问

必须清零：

- `Database._instance`；
- 直接 SQL；
- `db.get_conn()`；
- 在工具函数内构建复杂领域流程；
- 在工具函数内直接操作 Canonical Wiki 文件。

`auto_tag` 必须改为：

```text
MCP auto_tag
→ TaggingService
→ KnowledgeRepository/Tag Repository
→ LLMService
```

### WP2-T6：MCP Contract 全量快照

验证：

- 工具名；
- 参数；
- descriptions；
- annotations；
- profile membership；
- envelope；
- write policy；
- legacy aliases。

## 8.3 验收

- `src/mcp/tools/*.py` 包含真实实现；
- `src/mcp/server.py` 不包含领域工具主体；
- MCP 生产代码中 `Database._instance`、SQL、`get_conn()` 为 0；
- Answering 不再依赖 `src.services.verified_answer` 的业务实现；
- Tool Contract 完全一致；
- stdio、HTTP、Windows、Docker 冒烟通过；
- Answer 契约与真实 LLM E2E 不退化。

## 8.4 回滚

- 每个工具领域独立 PR；
- 注册表允许在迁移期从旧模块或新模块加载，但同一工具只能有一个实现来源；
- 不允许复制两份实现长期并存；
- 任一领域迁移失败，只回滚该领域。

## 8.5 向 WP3 交付

WP3 假设 MCP 已不直接访问 Database/global Container 细节。否则不得进入 Container 清理。

## 8.6 提交建议

```text
refactor(answer): move answer assembly into answering package
refactor(mcp): move retrieval tools into domain module
refactor(mcp): move ingest and administration tools into domain modules
refactor(mcp): move wiki graph and memory tools into domain modules
refactor(application): move direct MCP business logic into command services
test(mcp): freeze domain tool contracts after extraction
```

---

# WP3：Container 与全局状态收束

## 9. 目标

让能力组拥有实际构造和生命周期，逐步结束 AppContainer 作为巨大 Service Locator 的状态。

## 9.1 目标结构

```text
AppContainer
├── core: CoreEvidenceProvider
├── verified: VerifiedServingProvider
├── authoring: AuthoringProvider | None
└── experimental: ExperimentalProvider | None
```

Provider 负责：

- lazy construction；
- dependency ownership；
- close/shutdown；
- feature enablement；
- 不可用时的明确错误或降级。

## 9.2 必做任务

### WP3-T1：把 ServiceGroups 从 View 改为 Provider

当前 ServiceGroups 只是访问扁平属性。改造后：

- Core Provider 自己构造 Search/Answer/Indexing；
- Verified Provider 自己构造 Repository Read Port、Gate、VerifiedProvider；
- Authoring Provider 只在 Authoring 开启时创建；
- Experimental Provider 只在相关配置启用时创建；
- Core 启动不得触发 Graph、Memory、Authoring 的构造。

### WP3-T2：扁平属性退化为兼容代理

允许暂时保留：

```python
@property
def search_service(self):
    return self.core.search_service
```

但新代码必须使用：

```python
container.core.search_service
container.verified.wiki_serving_gate
```

增加架构测试，禁止新生产代码直接依赖扁平属性；compatibility 和旧测试可白名单。

### WP3-T3：收束全局 Container

允许存在全局 Container 的唯一位置：

```text
src/mcp/runtime.py
src/compatibility/container_access.py
```

其他生产代码不得：

- 调用 `get_active_container()`；
- 修改 `_active_container`；
- 调用 MCP `_get_container()`；
- 通过模块全局变量获取服务。

API、CLI、GUI、Worker 必须显式接收 AppContainer 或所需 Provider。

### WP3-T4：生命周期测试

覆盖：

- 只启用 Core 时不构造 Authoring/Experimental；
- Verified 失败时 Raw 可运行；
- Authoring 关闭时写服务不可访问，但 Serving 可用；
- shutdown 只关闭已初始化服务；
- 重复 close 幂等；
- 测试之间无 Container 泄漏；
- 多个 Container 实例使用不同数据库互不污染。

## 9.3 验收

- Service Groups 拥有实际构造和生命周期；
- Core 不触发 Graph/Memory/Authoring；
- 新生产代码扁平 Container 属性调用为 0；
- `get_active_container()` 只存在于 runtime/compatibility 白名单；
- 多 Container 隔离测试通过；
- MCP/API/CLI/GUI 启动不变。

## 9.4 回滚

- 扁平属性代理保留一个正式版本；
- Provider 内部可临时委托旧属性，但必须有清零测试；
- 不允许删除兼容代理和改变构造机制在同一 PR 完成。

## 9.5 向 WP4 交付

WP4 通过 Core Provider 的 storage/repository 接口接入 migration head 检查，不允许重新引入全局 Database。

## 9.6 提交建议

```text
refactor(container): make capability groups own service construction
refactor(container): route flat properties through capability providers
test(container): prove feature isolation and lifecycle safety
refactor(core): restrict global container access to runtime compatibility
```

---

# WP4：Alembic 严格化与最小 Repository 补齐

## 10. 目标

不要求一次性拆完 `db.py`，但必须让迁移门禁真实可信，并消除本轮触及路径中的 Database 单例和直接 SQL。

## 10.1 必做任务

### WP4-T1：让 Alembic 使用显式 URL

修改 `alembic/env.py`，优先级建议：

```text
命令行/测试显式 URL
→ SHINEHE_TEST_ALEMBIC_URL
→ Alembic config sqlalchemy.url
→ ShineHe Config db path
```

测试必须验证实际连接的是临时文件，而不是用户默认数据库。

### WP4-T2：严格 Migration 测试

修改 `tests/test_alembic_baseline.py`：

- CI 安装 Alembic，不允许因未安装而 skip；
- 普通 upgrade 失败必须 fail；
- 仅明确的环境不支持条件可 skip，CI 中不得出现；
- 验证临时数据库生成 `alembic_version`；
- 验证 revision 等于 head；
- 验证关键表存在。

新增：

```text
tests/migrations/test_empty_to_head.py
tests/migrations/test_v1_9_to_head.py
tests/migrations/test_upgrade_idempotent.py
tests/migrations/test_interrupted_upgrade_recovery.py
```

### WP4-T3：启动 Migration Head Gate

写模式启动时：

- 检查当前 revision；
- 落后于 head 时拒绝写服务启动；
- 给出明确升级命令；
- 允许显式只读诊断模式；
- 不应在普通启动中静默自动修改 Schema。

建议创建：

```text
src/storage/migration_status.py
src/storage/startup_gate.py
```

### WP4-T4：最小 Repository 补齐

针对 WP2 中发现的直接 SQL，创建必要 Repository，而不是继续写到 `db.py`：

例如：

```text
KnowledgeTagRepository
KnowledgeMaintenanceRepository
```

目标不是全仓库 Repository 重构，而是保证：

- MCP/Application Service 不执行 SQL；
- 新代码不依赖 `Database._instance`；
- 本轮迁移过的业务有清晰数据访问边界。

### WP4-T5：冻结测试升级

现有 `_migrate()` 正则冻结测试保留，但增加：

- 新代码中不得调用 `_migrate()` 添加 Schema；
- Alembic revision 是新增 Schema 唯一入口；
- 文档与 CI 同步；
- 迁移测试必须在 CI 的独立 Job 中运行。

## 10.2 验收

- Alembic 确实使用临时测试数据库；
- empty → head、v1.9 → head、重复 upgrade 均成功；
- migration 失败不再被普通 skip 掩盖；
- 写模式会拒绝落后数据库；
- 只读诊断模式可启动；
- MCP/Application 新路径没有 SQL 和 Database 单例；
- `_migrate()` 冻结清单未增长。

## 10.3 回滚

- Startup Gate 提供显式配置回滚，但默认必须安全失败；
- 不删除 `_migrate()` 的历史兼容逻辑；
- 不删除旧 Schema；
- 所有 migration 测试使用副本或临时数据库；
- 任何真实数据迁移前必须自动备份。

## 10.4 向 WP5 交付

WP5 只有在 Unified 默认稳定、MCP/Container/DB 门禁全部达标后才能删除 Legacy。

## 10.5 提交建议

```text
fix(alembic): honor explicit test database URL
 test(migrations): make empty and previous-version upgrades strict
feat(storage): block write startup when schema is behind head
refactor(repository): remove direct SQL from migrated application services
ci(migrations): run strict alembic upgrade gates
```

---

# WP5：Legacy 删除与 v1.10.0 最终验收

## 11. 前置条件

必须同时满足：

- Unified Retrieval 已作为默认正式路径发布至少一个版本；
- Shadow 报告达到门槛；
- 无已知回滚到 Legacy 才能解决的生产问题；
- Search/Ask/Wiki/MCP 契约稳定；
- Retrieval/Hybrid/真实 LLM E2E 无退化；
- MCP 工具实拆完成；
- Container 全局访问已收束；
- Migration Head Gate 已上线并验证。

## 11.1 删除项

允许删除：

- `execute_primary_legacy`；
- `_search_legacy_pipeline`；
- `_search_verified_hybrid` 的旧副本；
- `run_raw_retrieval_adapter`；
- `retrieval.orchestrator=legacy|shadow`，若迁移期结束；
- 无行为差异的 `answer.orchestrator=legacy|shadow`；
- 旧 `src/services/verified_answer.py` 业务实现，只保留必要导入兼容；
- MCP 工具旧实现来源；
- 非白名单 `get_active_container()`；
- 已无调用的 Database 单例兼容入口。

不能删除：

- Search/Ask 公共 Facade；
- MCP legacy alias，除非满足其单独弃用周期；
- `_migrate()` 历史兼容逻辑，除非另有数据库迁移 Spec；
- Canonical/Projection 恢复能力。

## 11.2 最终架构门禁

新增强制测试：

```text
SearchService 中正式检索算法实现 = 0
RawRetriever 对 SearchService 的依赖 = 0
src/mcp/server.py 中具体工具函数 = 0 或仅注册辅助函数
src/mcp/tools 中真实工具实现覆盖率 = 100%
MCP 中 Database._instance / SQL / get_conn = 0
answering → services.verified_answer 业务依赖 = 0
非白名单 get_active_container = 0
新 Schema runtime mutation = 0
```

## 11.3 最终验收命令

```bash
python -m pytest tests/ -q
ruff check src tests evals tools scripts
python -m mypy src tools --ignore-missing-imports
python evals/run_retrieval_eval.py --all --fake-embedding \
  --baseline evals/baselines/local.json --max-regression 0.05
python evals/run_hybrid_eval.py --strict
python evals/run_ask_e2e_eval.py --engine real-llm
python tools/report_closure_debt.py --strict
```

GitHub Actions 必须全绿：

- Python 3.10 / 3.11 / 3.12；
- Lint / MyPy；
- Retrieval Eval；
- Hybrid Eval；
- Migration Gate；
- Docker API/MCP；
- Windows Smoke；
- Frontend Build。

## 11.4 最终文档

更新：

```text
README.md
README_zh.md
PROGRESS.md
CLAUDE.md
docs/architecture/core-request-flow.md
docs/architecture/module-boundaries.md
docs/architecture/database-migration-policy.md
docs/migration/deprecation-register.md
docs/migration/v1.9-to-v1.10-maintainability-closure.md
docs/release/v1.10.0-release-notes.md
```

文档不得再声称存在已删除的 legacy/shadow 开关。

---

## 12. CI 增强要求

新增 Jobs：

### architecture-closure

```bash
python tools/report_closure_debt.py --strict
pytest tests/architecture/ -q
```

### migration-gate

```bash
pytest tests/migrations/ -q
```

### contract-gate

```bash
pytest \
  tests/test_public_search_contract.py \
  tests/test_public_ask_contract.py \
  tests/test_wiki_serving_contract.py \
  tests/test_mcp_contract.py -q
```

要求：任何一个 Job 失败都阻止合并。

---

## 13. Agent 执行规则

### 13.1 单任务规则

Agent 每次只执行一个 Task：

1. 搜索全部调用方；
2. 先补测试；
3. 运行测试确认基线；
4. 最小改动；
5. Targeted tests；
6. 全量测试；
7. Ruff / MyPy；
8. Eval；
9. 更新执行报告；
10. 单一职责提交。

### 13.2 禁止行为

- 一次提交跨两个工作包；
- 复制旧实现后长期保留两份；
- 删除失败测试；
- 通过扩大 skip 掩盖迁移问题；
- 新增 `Database._instance`；
- 新增 `get_active_container()` 调用；
- MCP 工具中新增 SQL；
- 在迁移算法时同时调整评分；
- 未经 Shadow 验证删除 Legacy；
- 在真实用户数据库上运行破坏性迁移测试。

### 13.3 停止条件

出现以下情况必须停止当前 Task：

- Search/Ask/Wiki/MCP 契约变化；
- Hybrid Eval 下降；
- Claim/Evidence 关系变化；
- 需要修改超过 15 个无直接关系的生产文件；
- migration 可能导致数据丢失；
- Unified 与 Legacy 结果差异无法解释；
- 需要在同一版本切换默认并删除回滚路径；
- CI 环境与本地结果不一致且原因未查明。

---

## 14. Release Train

### v1.9.1：Unified Retrieval 默认

包含：

- WP0；
- WP1；
- Unified 默认；
- Legacy 保留；
- Shadow 报告和迁移说明。

### v1.9.2：应用与基础设施收束

包含：

- WP2；
- WP3；
- WP4；
- Legacy Retrieval 仍可作为紧急回滚，除非已满足完整观察周期。

### v1.10.0：Legacy 清理稳定版

包含：

- WP5；
- 删除已完成弃用周期的双路径和兼容债务；
- 最终架构报告；
- 全门禁通过。

若实际开发节奏更慢，可以增加 RC，不得压缩安全观察周期。

---

## 15. Definition of Done

只有以下全部成立，才可宣布“可维护性改造最终完成”：

### Retrieval

- Unified 是唯一正式实现；
- RawRetriever 不依赖 SearchService；
- Policy 直接组合组件；
- Legacy 主管线已删除；
- 质量和延迟门禁通过。

### Answer

- Answer 业务逻辑位于 `src/answering/`；
- 不存在伪 legacy/unified 双路径；
- Ask 契约和真实 LLM E2E 通过。

### MCP

- 工具真实分域；
- Server 只负责初始化与注册；
- MCP 不执行 SQL，不访问 Database 单例；
- Tool Contract 完全兼容。

### Container

- 能力组拥有构造和生命周期；
- Core 不构造 Authoring/Experimental；
- 全局 Container 仅在 runtime/compatibility 白名单；
- 多实例隔离通过。

### Database

- Alembic 临时库测试真实执行；
- empty/previous/idempotent upgrade 通过；
- 写模式检查 head；
- 新 Schema runtime mutation 为 0；
- 本轮业务数据访问通过 Repository。

### 质量

- 全部测试、静态检查、Eval 和 CI 全绿；
- 架构欠账报告 strict 模式为 0；
- 文档与实际默认配置一致；
- 回滚和升级手册完整。

---

## 16. Agent 完成报告模板

```markdown
# Task 完成报告

## Task
- ID：
- 基线 SHA：
- 完成 SHA：

## 修改范围
- 生产文件：
- 测试文件：
- 文档：

## 明确未修改
- Retrieval 算法：
- Wiki Gate：
- MCP Contract：
- DB Schema：

## 验证
- Targeted pytest：
- Full pytest：
- Ruff：
- MyPy：
- Retrieval Eval：
- Hybrid Eval：
- Migration Gate：
- GitHub Actions：

## 行为对比
- Search：
- Ask：
- Wiki：
- MCP：

## 风险与回滚
- 已知风险：
- 回滚配置/提交：

## 架构欠账变化
- 修复前：
- 修复后：

## 是否允许进入下一 Task
- YES / NO
- 原因：
```

---

## 17. 首个 Agent 执行批次

Agent 现在只应执行：

```text
WP0-T1 全量验收基线
WP0-T2 架构欠账快照
WP1-T1 RawRetriever 算法迁移
```

完成后停止，提交阶段报告。未确认 Raw Retrieval 等价前，不得继续 Fusion 迁移和 Unified 默认切换。
