# ShineHeKnowledge 分阶段执行计划 (Execution Plan)

> **版本**: v1.0 | **日期**: 2026-06-12 | **配套文档**: SPEC_audit_optimization.md
> **执行原则**: 每个 Phase 完成后 → Review → Fix → Commit → 再进下一 Phase
> **分支策略**: 每个 Phase 从 master 创建独立分支 `feature/phase-N-xxx`，完成后 PR 合并回 master

---

## Phase 1: 基础工程治理 (v1.3)

> **目标**: 让项目安全、可维护、可部署
> **预计工期**: 5-7 工作日
> **风险等级**: 🟢 低

### Step 1.1: 配置安全改造

**分支**: `feature/phase1-config-security`

#### 1.1.1 创建 config.example.yaml

- 从 `config.yaml` 复制
- 将以下字段替换为占位符:
  ```yaml
  graph_backend:
    password: YOUR_NEO4J_PASSWORD    # 原: neo4j123
  llm:
    api_key: YOUR_LLM_API_KEY        # 原: 真实 key
  embedding:
    api_key: YOUR_EMBEDDING_API_KEY   # 原: 真实 key
  ```
- 添加顶部注释说明各字段用途和获取方式

#### 1.1.2 扩展 _SECRET_KEYS

**文件**: `src/utils/config.py`

```python
_SECRET_KEYS = {
    "llm.api_key",
    "embedding.api_key",
    "reranker.api_key",
    "graph_backend.password",   # 新增
    "api.jwt_secret",           # 新增
}
```

同步支持环境变量:
```python
_ENV_KEY_MAP = {
    "llm.api_key": "SHINEHE_LLM_API_KEY",
    "embedding.api_key": "SHINEHE_EMBEDDING_API_KEY",
    "reranker.api_key": "SHINEHE_RERANKER_API_KEY",
    "graph_backend.password": "SHINEHE_NEO4J_PASSWORD",
    "api.jwt_secret": "SHINEHE_JWT_SECRET",
}
```

#### 1.1.3 keyring 降级警告

**文件**: `src/utils/config.py`

在 keyring 不可用的 catch 分支添加:
```python
logger.warning(
    "keyring 不可用，敏感配置将以明文存储在 config.yaml 中。"
    "建议设置环境变量替代: %s", env_key
)
```

#### 1.1.4 修复迁移脚本硬编码凭据

**文件**: `scripts/fast_migrate.py`, `scripts/fast_migrate_edges.py`

```python
# Before:
driver = Neo4jDriver.driver("bolt://localhost:7687", auth=("neo4j", "neo4j123"))

# After:
from src.utils.config import Config
cfg = Config()
driver = Neo4jDriver.driver(
    cfg.get("graph_backend.uri", "bolt://localhost:7687"),
    auth=(cfg.get("graph_backend.user", "neo4j"), cfg.get("graph_backend.password"))
)
```

#### 1.1.5 更新 .gitignore 和 .stignore

**文件**: `.gitignore`
```
# 添加:
config.yaml
```

**文件**: `.stignore`
```
# 添加:
config.yaml
```

#### 1.1.6 修复 MCP 配置模板硬编码路径

**文件**: `mcp_config_templates/claude-code.json` 等

将本地路径替换为占位符:
```json
"command": "<PYTHON_PATH>",
"args": ["<PROJECT_PATH>/run_mcp.py", ...]
```

#### Step 1.1 检查点

- [ ] `config.example.yaml` 创建完成，无真实密码
- [ ] `_SECRET_KEYS` 包含所有 5 个敏感字段
- [ ] keyring 降级时有 warning 日志
- [ ] 迁移脚本从 Config 读取凭据
- [ ] `config.yaml` 在 `.gitignore` 和 `.stignore` 中
- [ ] MCP 模板无硬编码路径
- [ ] 运行 `pytest tests/ -v` 全部通过
- [ ] 手动测试: 启动 API/GUI/MCP 三种模式均正常

**→ Review → Fix → Commit**

---

### Step 1.2: MCP 写操作安全策略

**分支**: `feature/phase1-mcp-security`

#### 1.2.1 添加 MCP 安全配置

**文件**: `config.yaml` (新增 section)

```yaml
mcp:
  write_policy: preview_only    # preview_only | local_confirm | token_required | disabled
  allow_http_write: false
  bind_host: 127.0.0.1
  auth_token: ""                # 当 write_policy=token_required 时使用
```

#### 1.2.2 实现 MCP 写操作守卫

**文件**: `src/mcp_server.py`

新增装饰器或守卫函数:
```python
def _check_write_policy(tool_name: str) -> tuple[bool, str]:
    """检查写操作是否被允许"""
    policy = Config().get("mcp.write_policy", "preview_only")
    transport = os.environ.get("MCP_TRANSPORT", "stdio")

    if policy == "disabled":
        return False, "写操作已被策略禁用"
    if policy == "preview_only":
        return False, "当前策略仅允许 preview (dry_run)，请使用 preview_operation 工具"
    if policy == "token_required" and transport == "streamable-http":
        # 检查请求中的 auth_token
        ...
    return True, ""
```

在所有写操作工具入口调用此守卫。

#### 1.2.3 补齐 MCP 工具 annotations

为缺失 annotations 的 20 个工具补齐:
```python
annotations={"readOnlyHint": False, "destructiveHint": True/False, "idempotentHint": True/False}
```

#### Step 1.2 检查点

- [ ] MCP 安全配置可读
- [ ] write_policy 四级策略生效
- [ ] HTTP 模式默认禁用写操作
- [ ] 所有 41 工具有 annotations
- [ ] stdio 模式不受影响（向后兼容）
- [ ] `pytest tests/ -v` 通过

**→ Review → Fix → Commit**

---

### Step 1.3: Docker Profile 拆分

**分支**: `feature/phase1-docker-split`

#### 1.3.1 创建多阶段 Dockerfile

**文件**: `Dockerfile` (重写)

```dockerfile
# Stage 1: Base
FROM python:3.12-slim AS base
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Stage 2: API
FROM base AS api
RUN pip install --no-cache-dir -e ".[api,parsers,wiki,graph]"
COPY . .
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -f http://localhost:8000/api/health || exit 1
USER nobody
CMD ["python", "run_api.py"]

# Stage 3: MCP
FROM base AS mcp
RUN pip install --no-cache-dir -e "."
COPY . .
USER nobody
CMD ["python", "run_mcp.py"]
```

#### 1.3.2 重写 docker-compose.yml

```yaml
services:
  shinehe-api:
    build:
      context: .
      target: api
    ports: ["8000:8000"]
    volumes: ["./data:/app/data", "./config.yaml:/app/config.yaml"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"]
      interval: 30s
    deploy:
      resources:
        limits:
          memory: 2G

  shinehe-mcp:
    build:
      context: .
      target: mcp
    volumes: ["./data:/app/data", "./config.yaml:/app/config.yaml"]
    profiles: ["mcp"]

  # 可选: Neo4j 图谱后端
  neo4j:
    image: neo4j:5-community
    profiles: ["graph"]
    ports: ["7474:7474", "7687:7687"]
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
    volumes: ["neo4j-data:/data"]
```

#### 1.3.3 更新 .dockerignore

```
client/node_modules
client/dist
dist/
build/
docs/
*.egg-info
.git
.pytest_cache
__pycache__
tests/
scripts/
*.md
```

#### Step 1.3 检查点

- [ ] `docker build --target api .` 成功
- [ ] `docker build --target mcp .` 成功
- [ ] API 镜像不含 PySide6/pytest
- [ ] HEALTHCHECK 正常工作
- [ ] docker-compose up 正常启动 API
- [ ] 非 root 用户运行

**→ Review → Fix → Commit**

---

### Step 1.4: 引入 CI

**分支**: `feature/phase1-ci`

#### 1.4.1 创建 GitHub Actions

**文件**: `.github/workflows/ci.yml`

```yaml
name: CI
on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install ruff mypy
      - run: ruff check .
      - run: mypy src --ignore-missing-imports

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -e ".[dev]"
      - run: pytest tests/ -v --tb=short

  frontend:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: client } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: npm ci
      - run: npm run build

  docker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build --target api .
      - run: docker build --target mcp .
```

#### 1.4.2 配置 pyproject.toml 工具

```toml
[tool.ruff]
target-version = "py312"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "W", "I"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.mypy]
python_version = "3.12"
warn_return_any = true
warn_unused_configs = true
```

#### 1.4.3 扩展 dev 依赖

```toml
[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
    "ruff>=0.4",
    "mypy>=1.10",
    "coverage>=7.0",
]
```

#### Step 1.4 检查点

- [ ] `.github/workflows/ci.yml` 存在且语法正确
- [ ] `ruff check .` 通过（可忽略现有 warning，先设 baseline）
- [ ] `pytest tests/ -v` 通过
- [ ] `cd client && npm ci && npm run build` 通过
- [ ] pyproject.toml 有 ruff/pytest/mypy 配置

**→ Review → Fix → Commit**

---

### Step 1.5: 统一依赖来源

**分支**: `feature/phase1-deps-unify`

#### 1.5.1 补齐 pyproject.toml 缺失包

```toml
[project]
dependencies = [
    # ... 现有依赖 ...
    "charset-normalizer>=3.3",    # 新增: file_parser.py 使用
]

[project.optional-dependencies]
parsers = [
    # ... 现有 parsers ...
    "python-pptx>=1.0",          # 新增: file_parser.py 使用
]
```

#### 1.5.2 删除或自动生成 requirements.txt

方案 A（推荐）: 删除 requirements.txt，Dockerfile 直接用 `pip install -e ".[api,parsers,wiki,graph]"`

方案 B: 添加脚本自动生成:
```bash
pip install -e ".[api,parsers,wiki,graph,dev]"
pip freeze > requirements.txt
```

#### Step 1.5 检查点

- [ ] 所有代码中 import 的包都在 pyproject.toml 中声明
- [ ] `pip install -e ".[all]"` 安装成功
- [ ] requirements.txt 已删除或可自动生成
- [ ] Dockerfile 基于新依赖结构构建成功

**→ Review → Fix → Commit → Phase 1 完成**

---

## Phase 2: 架构内核重构 (v1.4 前半)

> **目标**: 消除隐式全局状态，所有核心服务可独立测试
> **预计工期**: 8-10 工作日
> **风险等级**: 🟡 中

### Step 2.1: Database 去 God Class

**分支**: `feature/phase2-database-refactor`

#### 2.1.1 Database 实例化改造

将 Database 从类方法单例改为实例模式:

```python
class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.local = threading.local()
        self._write_lock = threading.RLock()  # 改为可重入锁
        self._container = None  # 移到实例属性

    def get_conn(self) -> sqlite3.Connection:
        """获取当前线程的连接"""
        ...

    # 保留但标记 deprecated 的类方法
    @classmethod
    @deprecated("使用 container.db.get_conn() 代替")
    def get_conn_compat(cls): ...
```

#### 2.1.2 Repository 完善注入

检查 `src/repositories/` 下所有 Repository，确保:
- 构造函数接收 `Database` 实例（非类）
- 不直接访问 `Database._instance` 或 `Database._conn`
- 不使用 `@classmethod` 访问数据库

#### 2.1.3 DatabaseCompat 过渡层

```python
class DatabaseCompat:
    """向后兼容层，逐步迁移后删除"""
    @classmethod
    def get_conn(cls):
        """旧代码兼容入口"""
        return Container.get_instance().db.get_conn()
```

#### 2.1.4 迁移所有直接 Database 调用

扫描所有文件中的 `Database.xxx` 直接调用，改为通过 container 注入:
- `src/services/rag_pipeline.py`
- `src/services/wiki_*.py`
- `src/mcp_server.py`
- `src/api/routes/*.py`

#### Step 2.1 检查点

- [ ] Database 无 @classmethod 数据库操作方法（仅保留 `__init__` 和实例方法）
- [ ] 所有 Repository 通过构造函数接收 Database 实例
- [ ] `_write_lock` 改为 RLock 防止死锁
- [ ] 旧代码通过 DatabaseCompat 可正常运行
- [ ] `pytest tests/ -v` 全部通过
- [ ] 三种模式 (GUI/API/MCP) 均正常启动

**→ Review → Fix → Commit**

---

### Step 2.2: RAG 管线依赖注入

**分支**: `feature/phase2-rag-di`

#### 2.2.1 PipelineStage 构造器注入

```python
class VectorSearchStage(PipelineStage):
    def __init__(self, searcher: HybridSearcher, block_context: BlockContextService):
        self.searcher = searcher
        self.block_context = block_context

    def execute(self, ctx: RagContext, config: dict) -> RagContext:
        # 不再内联创建服务，直接用 self.searcher
        ...
```

#### 2.2.2 RagPipeline 接收所有阶段实例

```python
class RagPipeline:
    def __init__(
        self,
        rewriter: QueryRewriteStage,
        wiki: WikiRetrievalStage,
        vector: VectorSearchStage,
        reranker: RerankStage,
        generator: GenerateStage,
        postprocessor: PostProcessStage,
    ):
        self.stages = {
            "query_rewrite": rewriter,
            "wiki_retrieval": wiki,
            "vector_search": vector,
            "rerank": reranker,
            "generate": generator,
            "postprocess": postprocessor,
        }
```

#### 2.2.3 Container 中组装 RagPipeline

在 `AppContainer` 中:
```python
@property
def rag_pipeline(self) -> RagPipeline:
    return RagPipeline(
        rewriter=QueryRewriteStage(self.db),
        wiki=WikiRetrievalStage(self.db),
        vector=VectorSearchStage(
            searcher=self.hybrid_searcher,
            block_context=self.block_context_service,
        ),
        reranker=RerankStage(self.llm_service),
        generator=GenerateStage(self.llm_service, self.db),
        postprocessor=PostProcessStage(),
    )
```

#### 2.2.4 删除 RAGService.query_stream() 重复代码

将 RAGService 的内联管线逻辑替换为调用 RagPipeline。

#### 2.2.5 RagContext 类型化

```python
@dataclass
class RagContext:
    query: str
    rewritten_queries: list[str] = field(default_factory=list)
    route: str = "hybrid"
    query_plan: dict | None = None
    wiki_results: list[dict] = field(default_factory=list)
    search_results: list[dict] = field(default_factory=list)
    reranked_results: list[dict] = field(default_factory=list)
    block_contexts: dict[str, dict] = field(default_factory=dict)
    answer: str = ""
    source_graph: dict | None = None
    warnings: list[str] = field(default_factory=list)
    # 不再有 metadata: dict[str, Any]
```

#### Step 2.2 检查点

- [ ] 所有 PipelineStage 通过构造器接收依赖
- [ ] 无 `_get_container_service()` 调用
- [ ] 无 `Database` 全局单例直接引用
- [ ] RagContext 是 dataclass，无 metadata dict
- [ ] RAGService.query_stream() 使用 RagPipeline
- [ ] 各阶段可独立 mock 测试
- [ ] `pytest tests/ -v` 通过

**→ Review → Fix → Commit**

---

### Step 2.3: 统一配置驱动

**分支**: `feature/phase2-config-unify`

#### 2.3.1 合并 SearchService 和 RagPipeline 配置

统一为 `rag:` 配置节:
```yaml
rag:
  enable_query_rewriting: true
  enable_rerank: true
  enable_wiki_retrieval: true
  pipeline:
    stages:
      - name: query_rewrite
        enabled: true
      - name: vector_search
        enabled: true
      ...
```

SearchService 和 RagPipeline 从同一配置源读取。

#### Step 2.3 检查点

- [ ] SearchService 和 RagPipeline 配置来源统一
- [ ] config.yaml 中 rag 配置节结构清晰
- [ ] `pytest tests/ -v` 通过

**→ Review → Fix → Commit → Phase 2 完成**

---

## Phase 3: RAG 质量升级 (v1.4 后半)

> **目标**: 让问答效果可评测、可解释
> **预计工期**: 7-9 工作日
> **风险等级**: 🟡 中

### Step 3.1: RAG Eval 基准集

**分支**: `feature/phase3-rag-eval`

#### 3.1.1 创建 eval 目录结构

```
evals/
  datasets/
    basic_qa.yaml         # 基础问答 (≥10 条)
    table_qa.yaml         # 表格问答 (≥10 条)
    graph_qa.yaml         # 图谱问答 (≥10 条)
    no_answer.yaml        # 无答案测试 (≥10 条)
  run_eval.py             # 评测运行器
  metrics.py              # 指标计算
```

#### 3.1.2 指标体系

```python
# metrics.py
@dataclass
class EvalMetrics:
    recall_at_5: float       # 检索前5是否含正确块
    recall_at_10: float      # 检索前10是否含正确块
    mrr: float               # 正确来源排名倒数均值
    citation_accuracy: float  # 引用是否支持结论
    faithfulness: float      # 是否基于知识库（非编造）
    no_answer_accuracy: float  # 证据不足时能否拒答
    latency_p50: float       # 响应延迟 P50
    latency_p95: float       # 响应延迟 P95
```

#### 3.1.3 评测运行器

```bash
python evals/run_eval.py --dataset evals/datasets/basic_qa.yaml
python evals/run_eval.py --all
python evals/run_eval.py --report markdown  # 输出 markdown 报告
```

#### Step 3.1 检查点

- [ ] evals/ 目录结构完整
- [ ] 4 类评测数据集各 ≥10 条
- [ ] `python evals/run_eval.py --all` 可运行
- [ ] 输出 6 项指标 + 延迟统计
- [ ] 有 baseline 结果记录

**→ Review → Fix → Commit**

---

### Step 3.2: 检索诊断面板 (后端 + 前端)

**分支**: `feature/phase3-retrieval-diagnostics`

#### 3.2.1 后端: RagContext 返回诊断信息

RagPipeline.execute() 已返回 route/query_plan/source_graph/warnings 等字段。增强:
```python
@dataclass
class RetrievalDiagnostics:
    query_rewrite: list[str]
    route: str                    # hybrid | structured | graph
    retrieval: RetrievalStats
    dropped_candidates: list[DroppedCandidate]
    evidence_tokens: int
    generation_tokens: int
```

API 响应中增加 `diagnostics` 字段。

#### 3.2.2 前端: ChatView 增加诊断折叠面板

在 ChatView 的回答下方添加可折叠的「检索诊断」面板:
- 检索路由 (route)
- 各阶段命中数 (vector_hits / fts_hits / wiki_hits / reranked)
- 被丢弃的候选及原因
- Token 用量

#### Step 3.2 检查点

- [ ] API `/api/chat/ask` 响应包含 diagnostics
- [ ] ChatView 展示检索诊断面板
- [ ] 诊断面板可折叠/展开
- [ ] 前端 build 通过

**→ Review → Fix → Commit**

---

### Step 3.3: Evidence Compression 阶段

**分支**: `feature/phase3-evidence-compress`

#### 3.3.1 新增 EvidenceCompressStage

插入在 rerank 和 generate 之间:
```
query_rewrite → wiki_retrieval → vector_search → rerank → evidence_compress → generate → postprocess
```

```python
class EvidenceCompressStage(PipelineStage):
    """压缩证据: 只保留与问题相关的句子、表格行、标题链"""
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    def execute(self, ctx: RagContext, config: dict) -> RagContext:
        compressed = []
        for evidence in ctx.reranked_results:
            relevant = self._extract_relevant(evidence, ctx.query)
            compressed.append(relevant)
        ctx.compressed_evidence = compressed
        return ctx
```

#### 3.3.2 config.yaml 配置

```yaml
rag:
  pipeline:
    stages:
      - name: evidence_compress
        enabled: true
        max_evidence_tokens: 4000
        strategy: "extractive"    # extractive | abstractive
```

#### Step 3.3 检查点

- [ ] evidence_compress 阶段可配置启用/禁用
- [ ] 开启后 context token 减少 ≥30%（用 Eval 基准集验证）
- [ ] 问答质量不下降（Eval 指标对比）
- [ ] `pytest tests/ -v` 通过

**→ Review → Fix → Commit**

---

### Step 3.4: Parent-Child Retrieval 基础实现

**分支**: `feature/phase3-parent-child`

#### 3.4.1 升级 BlockContextService

当前是 post-retrieval 注解。升级为:
1. Embedding 用小块（block 级）
2. 检索后自动返回父块内容（chapter/section 级）
3. Source citation 仍定位到最小 block

#### 3.4.2 按文档类型差异化 block 策略

```python
BLOCK_STRATEGIES = {
    "pdf": {"parent": "page", "child": "paragraph"},
    "docx": {"parent": "section", "child": "paragraph"},
    "xlsx": {"parent": "sheet", "child": "row_range"},
    "pptx": {"parent": "slide", "child": "text_block"},
    "md": {"parent": "heading", "child": "paragraph"},
}
```

#### Step 3.4 检查点

- [ ] 检索返回父块上下文
- [ ] Citation 仍定位到子块
- [ ] 不同文件类型使用不同 block 策略
- [ ] Eval 基准集: 长/PDF 文档问答效果提升
- [ ] `pytest tests/ -v` 通过

**→ Review → Fix → Commit → Phase 3 完成**

---

## Phase 4: MCP 产品化升级 (v1.5)

> **目标**: 成为 Claude/Cursor/Cline 的本地知识中枢
> **预计工期**: 5-7 工作日
> **风险等级**: 🟢 低

### Step 4.1: MCP 工具 Schema 标准化

**分支**: `feature/phase4-mcp-schema`

#### 4.1.1 统一工具元数据

每个工具添加:
```python
{
    "input_schema": {...},       # 已有（FastMCP 自动生成）
    "output_schema": {...},      # 新增: 标准化输出结构
    "side_effect": "read | write | destructive",  # 新增
    "requires_confirmation": bool,  # 新增
}
```

#### 4.1.2 工具分组命名

将工具按命名空间分组:
- `kb.search`, `kb.ask`, `kb.create`, `kb.update`, `kb.delete`, `kb.preview`, `kb.undo`
- `wiki.*`
- `graph.*`
- `ops.*`
- `memory.*` (新增)

保留旧名称作为 alias，确保向后兼容。

#### Step 4.1 检查点

- [ ] 所有工具有 side_effect 标注
- [ ] 工具分组命名生效
- [ ] 旧名称仍可用（alias）
- [ ] MCP Server 启动正常

**→ Review → Fix → Commit**

---

### Step 4.2: Agent Memory 工具

**分支**: `feature/phase4-agent-memory`

#### 4.2.1 新增记忆存储

```python
# 新增 table: agent_memory
# id, key, value, category (fact | decision | context | task), created_at, updated_at
```

#### 4.2.2 新增 MCP 工具

```python
@mcp.tool()
def remember_fact(key: str, value: str, category: str = "fact") -> dict:
    """记住一个事实/决策/上下文，持久化到知识库"""

@mcp.tool()
def recall_facts(query: str, category: str | None = None, limit: int = 5) -> dict:
    """搜索已记住的事实/决策"""

@mcp.tool()
def update_project_context(summary: str) -> dict:
    """更新项目整体上下文描述"""

@mcp.tool()
def search_decisions(query: str, limit: int = 5) -> dict:
    """搜索架构/技术决策记录"""

@mcp.tool()
def summarize_recent_changes(since_hours: int = 24) -> dict:
    """总结近期知识库变更"""

@mcp.tool()
def extract_tasks_from_doc(content: str) -> dict:
    """从文档内容中提取待办任务"""
```

#### Step 4.2 检查点

- [ ] agent_memory 表创建（Alembic 迁移）
- [ ] 6 个 Agent Memory 工具可用
- [ ] Claude/Cursor 可通过 MCP 调用这些工具
- [ ] `pytest tests/ -v` 通过

**→ Review → Fix → Commit → Phase 4 完成**

---

## Phase 5: 前端体验升级 (v2.0)

> **目标**: 从基础工作台升级为完整 Web 应用
> **预计工期**: 15-20 工作日
> **风险等级**: 🟡 中

### Step 5.1: 前端基础架构升级

**分支**: `feature/phase5-frontend-arch`

#### 5.1.1 引入 React Router

```bash
cd client && npm install react-router-dom
```

路由结构:
```
/                → Dashboard
/knowledge       → 知识库列表
/knowledge/:id   → 知识详情
/import          → 导入中心
/chat            → 智能问答
/wiki            → Wiki 管理
/wiki/:id        → Wiki 详情/编辑
/graph           → 图谱
/settings        → 设置
```

#### 5.1.2 Token 安全升级

```typescript
// api.ts: 升级为 HttpOnly Cookie 模式
// 后端: auth.py 返回 Set-Cookie 而非 body token
// 提供 useAuth() hook 和 AuthContext

export const AuthContext = createContext<{...}>(...)
export function useAuth() { return useContext(AuthContext) }
```

后端:
```python
# auth.py: 添加 cookie-based auth 选项
@router.post("/login")
async def login(response: Response, ...):
    response.set_cookie(
        "access_token", token,
        httponly=True, max_age=..., samesite="lax"
    )
```

#### 5.1.3 布局组件

```
src/
  components/
    Layout.tsx          # 侧边栏 + 内容区
    Sidebar.tsx         # 导航菜单
    PageHeader.tsx      # 页面标题 + 操作按钮
    DataTable.tsx       # 通用表格 (分页/排序/过滤)
    Toast.tsx           # 全局通知
    ErrorBoundary.tsx   # 错误边界
  hooks/
    useAuth.ts
    usePagination.ts
    useApi.ts
  contexts/
    AuthContext.tsx
  views/
    DashboardView.tsx
    KnowledgeView.tsx
    KnowledgeDetail.tsx
    ImportView.tsx
    ChatView.tsx
    WikiView.tsx
    WikiDetail.tsx
    GraphView.tsx
    SettingsView.tsx
```

#### Step 5.1 检查点

- [ ] React Router 路由生效
- [ ] URL 刷新不丢失页面状态
- [ ] HttpOnly Cookie 模式可用
- [ ] 布局组件和 hooks 复用
- [ ] ErrorBoundary 捕获渲染错误
- [ ] 前端 build 通过

**→ Review → Fix → Commit**

---

### Step 5.2: Dashboard 首页

**分支**: `feature/phase5-dashboard`

- 知识总数 / Block 数 / 向量索引数 / Wiki 页数
- 最近导入任务
- 知识健康分 (需后端 API)
- MCP 服务状态

#### Step 5.2 检查点

- [ ] Dashboard 展示 4 个统计卡片
- [ ] 最近导入任务列表
- [ ] `npm run build` 通过

**→ Review → Fix → Commit**

---

### Step 5.3: 导入中心 + 知识 CRUD

**分支**: `feature/phase5-import-crud`

- 拖拽文件上传
- URL 导入
- 批量任务进度
- 知识项创建/编辑/删除
- 知识详情页 (内容 + blocks + 来源)

#### Step 5.3 检查点

- [ ] 文件拖拽上传可用
- [ ] URL 导入可用
- [ ] 知识项 CRUD 完整
- [ ] 知识详情页展示 blocks

**→ Review → Fix → Commit**

---

### Step 5.4: Wiki 编辑器 + 图谱可视化

**分支**: `feature/phase5-wiki-graph`

- Wiki 页面创建/编辑 (Markdown 编辑器)
- Wiki 工作流操作 (submit_review/approve/reject/deprecate)
- 文件大纲图 (D3.js force-directed)
- 知识关系图

#### Step 5.4 检查点

- [ ] Wiki 页面可创建和编辑
- [ ] 工作流状态转换可用
- [ ] 图谱基本可视化

**→ Review → Fix → Commit**

---

### Step 5.5: 设置持久化 + 安全模式

**分支**: `feature/phase5-settings`

- 设置读取/保存 API
- 模型配置 (LLM/Embedding/Reranker)
- MCP 安全策略配置
- 数据备份
- 本地模式/远程模式安全策略切换

#### Step 5.5 检查点

- [ ] 设置页面可保存和加载
- [ ] 模型配置修改后立即生效
- [ ] 安全模式可切换

**→ Review → Fix → Commit → Phase 5 完成**

---

## 附录 A: 每个 Phase 的标准 Review 清单

### 代码 Review

- [ ] 新代码符合项目风格（无 ruff 报错）
- [ ] 无硬编码凭据或路径
- [ ] 类型标注完整
- [ ] 错误处理完善（无 bare except）
- [ ] 日志级别合理（info/warning/error）

### 测试 Review

- [ ] `pytest tests/ -v` 全部通过
- [ ] 新增代码有对应测试
- [ ] 手动测试三种模式 (GUI/API/MCP) 启动正常
- [ ] 数据库迁移无破坏性

### 安全 Review

- [ ] 无明文密码/密钥
- [ ] 写操作有权限检查
- [ ] 输入验证完善
- [ ] 无 SQL 注入风险

### 文档 Review

- [ ] CLAUDE.md 更新（如有架构变化）
- [ ] config.example.yaml 更新（如有新配置项）
- [ ] CHANGELOG 记录变更

---

## 附录 B: 分支与合并策略

```
master
  ├── feature/phase1-config-security     → PR → merge → tag v1.3.0-rc1
  ├── feature/phase1-mcp-security        → PR → merge
  ├── feature/phase1-docker-split        → PR → merge
  ├── feature/phase1-ci                  → PR → merge
  ├── feature/phase1-deps-unify          → PR → merge → tag v1.3.0
  │
  ├── feature/phase2-database-refactor   → PR → merge
  ├── feature/phase2-rag-di              → PR → merge
  ├── feature/phase2-config-unify        → PR → merge
  │
  ├── feature/phase3-rag-eval            → PR → merge
  ├── feature/phase3-retrieval-diagnostics → PR → merge
  ├── feature/phase3-evidence-compress   → PR → merge
  ├── feature/phase3-parent-child        → PR → merge → tag v1.4.0
  │
  ├── feature/phase4-mcp-schema          → PR → merge
  ├── feature/phase4-agent-memory        → PR → merge → tag v1.5.0
  │
  ├── feature/phase5-frontend-arch       → PR → merge
  ├── feature/phase5-dashboard           → PR → merge
  ├── feature/phase5-import-crud         → PR → merge
  ├── feature/phase5-wiki-graph          → PR → merge
  ├── feature/phase5-settings            → PR → merge → tag v2.0.0
```

**合并规则**:
1. 每个 Step 完成后创建 PR
2. PR 需通过 CI (lint + test + build)
3. 至少 1 人 Review 后合并
4. 合并后删除分支
5. Phase 完成后打 tag

---

## 附录 C: 工作量估算

| Phase | Steps | 工作日 | 风险 |
|-------|-------|--------|------|
| Phase 1 | 5 | 5-7d | 🟢 低 |
| Phase 2 | 3 | 8-10d | 🟡 中 |
| Phase 3 | 4 | 7-9d | 🟡 中 |
| Phase 4 | 2 | 5-7d | 🟢 低 |
| Phase 5 | 5 | 15-20d | 🟡 中 |
| **总计** | **19** | **40-53d** | — |
