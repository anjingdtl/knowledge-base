# MCP Local Retrieval Focus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **执行状态（2026-06-13）:** 已在 v1.3.1 完成实现与全仓库健康审查。下方复选框保留为原始执行清单，不再作为实时进度源；最新状态以根目录 `PROGRESS.md` 为准。验证结果：全量测试 `828 passed, 2 skipped`，扩展 Ruff、mypy、前端构建、CI 同款检索评测和端到端 Demo 均通过。本机无 Docker CLI，镜像构建由远端 CI `docker` job 验证。

**Goal:** 将 ShineHeKnowledge 收束为默认工具面精简、可一键本地初始化、可持续索引目录、引用可解释且质量可量化的 MCP 本地知识检索引擎。

**Architecture:** 保留现有服务层与高级功能，通过 MCP tool profile 控制默认暴露面；新增无 GUI 依赖的初始化/CLI、PathIndexService 与 watcher；在现有 HybridSearcher/RAG Pipeline 上统一候选分数和 Citation；使用固定 fixture 和阈值文件建立离线质量门禁。

**Tech Stack:** Python 3.10+、FastMCP、SQLite/FTS5/sqlite-vec、Alembic、OpenAI-compatible API、watchdog（optional）、pytest、GitHub Actions。

---

## 执行总览

| 模块 | 目标 | 主要交付 | 前置依赖 |
| --- | --- | --- | --- |
| M0 | 冻结当前基线 | schema、配置、检索回归基线 | 无 |
| M1 | 精简 MCP 工具面 | core/extended/admin/full/legacy profiles | M0、M3 |
| M2 | 本地初始化与 CLI | `shinehe init/index/watch/doctor/mcp` | M0 |
| M3 | 目录增量索引 | indexed_files、PathIndexService、watcher | M2 |
| M4 | 检索与引用统一 | RetrievalCandidate、Citation、score/reason | M0 |
| M5 | 本地 reranker provider | API/local/LLM/disabled | M4 |
| M6 | Eval 质量门禁 | fixture、golden source、baseline、CI | M4 |
| M7 | 文档、Demo、发布 | README、迁移指南、端到端 Demo | M1-M6 |

建议执行顺序：

```text
M0
  -> M2 + M4
  -> M3 + M5
  -> M1 + M6
  -> M7
```

M2、M4 可以并行开发，但必须在 M0 的基线测试合入后开始。M1 的 registry 代码可以提前开发，但 core profile 只有在 M3 提供 `index_path` 后才能完成验收和合入。

## 模块 M0：基线与契约冻结

### Task 1: 固定 MCP 工具和配置基线

**Files:**
- Create: `tests/snapshots/mcp_tools_legacy.json`
- Create: `tests/test_mcp_tool_profiles.py`
- Modify: `tests/snapshots/mcp_tools.json`
- Modify: `tests/test_mcp_contract.py`

- [ ] **Step 1: 将当前 51 个原始工具和全部别名导出为 legacy snapshot**

Snapshot 每项至少包含：

```json
{
  "name": "search",
  "side_effect": "read",
  "group": "kb",
  "is_alias": false
}
```

运行：

```powershell
python -m pytest tests/test_mcp_contract.py -q
```

预期：现有契约测试通过，并生成或人工核对 legacy 清单。

- [ ] **Step 2: 编写尚未实现的 core profile 测试**

测试必须断言：

```python
CORE_TOOLS = {
    "ping",
    "kb_capabilities",
    "search",
    "ask",
    "read",
    "list_knowledge",
    "index_path",
    "get_job",
    "list_jobs",
    "reindex_all",
}
```

同时断言 core 中不存在 `wiki_*`、`graph_traverse`、`remember_fact`、`create`、`delete` 和任何 `kb.*` 别名。

- [ ] **Step 3: 运行测试确认先失败**

```powershell
python -m pytest tests/test_mcp_tool_profiles.py -q
```

预期：FAIL，原因是 profile registry 和 `index_path` 尚不存在。

- [ ] **Step 4: 记录配置兼容测试**

覆盖：

- 新配置缺省值为 `core`。
- 已有配置未出现 `mcp.tool_profile` 时解析为 `legacy`。
- `enable_legacy_aliases=false` 时不注册别名。
- `experimental_tools_enabled=false` 时隐藏 Wiki/Graph/Memory。

- [ ] **Step 5: 提交基线**

```powershell
git add tests/snapshots tests/test_mcp_contract.py tests/test_mcp_tool_profiles.py
git commit -m "test(mcp): freeze tool profile baselines"
```

### Task 2: 固定检索回归基线

**Files:**
- Create: `tests/test_retrieval_candidate_contract.py`
- Modify: `tests/test_search_service.py`
- Modify: `tests/test_rag_sources.py`

- [ ] **Step 1: 增加当前已知问题的失败测试**

覆盖：

1. reranker 返回 `rerank_score` 时不能再按缺失的 `score` 字段过滤为空。
2. 同一文档的两个不同 `block_id` 可以同时进入 sources。
3. FTS 命中必须保留 `keyword` channel。
4. reranker 异常时保留 RRF 排序并记录 warning。

- [ ] **Step 2: 运行测试确认问题可复现**

```powershell
python -m pytest tests/test_retrieval_candidate_contract.py tests/test_rag_sources.py -q
```

预期：新增测试至少有一项失败，且失败原因对应分数或去重语义。

- [ ] **Step 3: 提交测试基线**

```powershell
git add tests/test_retrieval_candidate_contract.py tests/test_search_service.py tests/test_rag_sources.py
git commit -m "test(rag): capture retrieval scoring and citation gaps"
```

## 模块 M1：MCP 工具配置档

### Task 3: 建立声明式 Tool Registry

**Files:**
- Create: `src/mcp/__init__.py`
- Create: `src/mcp/tool_registry.py`
- Create: `src/mcp/tool_profiles.py`
- Create: `src/mcp/aliases.py`
- Modify: `src/mcp_server.py`
- Modify: `src/utils/config.py`
- Modify: `config.example.yaml`
- Test: `tests/test_mcp_tool_profiles.py`

- [ ] **Step 1: 定义 ToolDefinition**

目标接口：

```python
@dataclass(frozen=True)
class ToolDefinition:
    name: str
    function: Callable[..., Any]
    description: str
    annotations: dict[str, Any]
    group: str
    side_effect: str
    profiles: frozenset[str]
    experimental: bool = False
```

Registry 必须提供：

```python
_DEFINITIONS: dict[str, ToolDefinition] = {}

def tool_definition(
    *,
    name: str,
    description: str,
    annotations: dict[str, Any],
    group: str,
    side_effect: str,
    profiles: frozenset[str],
    experimental: bool = False,
):
    def decorator(function: Callable[..., Any]):
        _DEFINITIONS[name] = ToolDefinition(
            name=name,
            function=function,
            description=description,
            annotations=annotations,
            group=group,
            side_effect=side_effect,
            profiles=profiles,
            experimental=experimental,
        )
        return function
    return decorator

def select_tools(profile: str, experimental_enabled: bool) -> list[ToolDefinition]:
    return [
        definition
        for definition in _DEFINITIONS.values()
        if profile in definition.profiles
        and (experimental_enabled or not definition.experimental)
    ]

def register_tools(server: FastMCP, definitions: Iterable[ToolDefinition]) -> None:
    for definition in definitions:
        server.tool(
            definition.function,
            name=definition.name,
            description=definition.description,
            annotations=definition.annotations,
        )
```

- [ ] **Step 2: 定义 profile 清单**

`src/mcp/tool_profiles.py` 固定：

```python
CORE_TOOLS = frozenset({
    "ping", "kb_capabilities", "search", "ask", "read",
    "list_knowledge", "index_path", "get_job", "list_jobs", "reindex_all",
})
EXTENDED_TOOLS = CORE_TOOLS | frozenset({
    "search_fulltext", "tags", "route_query", "execute_query",
    "structured_query", "explain_query", "ask_with_query",
    "get_source_graph", "create_ingest_job", "cancel_job",
})
ADMIN_TOOLS = EXTENDED_TOOLS | frozenset({
    "create", "update", "delete", "restore_knowledge", "ingest_url",
    "preview_operation", "get_operation_log", "undo_operation",
    "list_recent_operations", "query_operation_logs",
})
```

`full` 选择所有非 experimental 原始工具；`legacy` 选择所有原始工具并允许别名。

- [ ] **Step 3: 将直接注册改为声明后统一注册**

保留工具函数位置和业务逻辑，只替换注册方式。模块底部执行：

```python
profile = resolve_tool_profile(Config)
register_tools(mcp, select_tools(profile.name, profile.experimental_enabled))
if profile.legacy_aliases:
    register_aliases(mcp, profile.visible_tool_names)
```

禁止通过删除 FastMCP 私有 `_tools` 字典实现过滤。

- [ ] **Step 4: 更新 kb_capabilities**

新增返回字段：

```json
{
  "tool_profile": "core",
  "write_policy": "disabled",
  "experimental_tools_enabled": false,
  "visible_tools": [],
  "hidden_groups": ["wiki", "graph", "memory"],
  "legacy_aliases_enabled": false
}
```

推荐流程只能引用当前可见工具。

- [ ] **Step 5: 运行 profile 契约测试**

```powershell
python -m pytest tests/test_mcp_tool_profiles.py tests/test_mcp_contract.py tests/test_mcp_docs_prompts.py -q
```

预期：core 恰好 10 个工具；legacy 与冻结 snapshot 一致。

- [ ] **Step 6: 提交 registry**

```powershell
git add src/mcp src/mcp_server.py src/utils/config.py config.example.yaml tests
git commit -m "feat(mcp): add configurable tool profiles"
```

### Task 4: 增加统一 index_path MCP 工具

**Files:**
- Modify: `src/mcp_server.py`
- Modify: `src/mcp/tool_profiles.py`
- Test: `tests/test_mcp_index_path.py`

- [ ] **Step 1: 先定义工具契约测试**

签名：

```python
def index_path(
    path: str,
    recursive: bool = True,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    guard = _check_write_policy("index_path", dry_run=dry_run)
    if guard:
        return guard
    result = _get_container().path_index_service.index_path(
        Path(path),
        recursive=recursive,
        dry_run=dry_run,
        force=force,
    )
    return ok(asdict(result), dry_run=dry_run)
```

返回必须使用现有 envelope，数据包含：

```json
{
  "path": "D:/docs",
  "mode": "sync|async",
  "job_id": null,
  "created": 0,
  "updated": 0,
  "skipped": 0,
  "deleted": 0,
  "failed": []
}
```

- [ ] **Step 2: 测试 write policy**

覆盖：

- `dry_run=true` 在只读策略下允许预览。
- `write_policy=disabled` 拒绝实际索引。
- HTTP 且 `allow_http_write=false` 拒绝实际索引。
- 越权路径返回 `PERMISSION_DENIED`。

- [ ] **Step 3: 使用 M3 的 PathIndexService 实现工具**

在 M3 完成前可让测试以 fake service 验证契约；不得复制文件扫描逻辑到 MCP 层。

- [ ] **Step 4: 运行测试**

```powershell
python -m pytest tests/test_mcp_index_path.py tests/test_operation_safety.py -q
```

- [ ] **Step 5: 提交工具**

```powershell
git add src/mcp_server.py src/mcp/tool_profiles.py tests/test_mcp_index_path.py
git commit -m "feat(mcp): add unified path indexing tool"
```

## 模块 M2：CLI 与本地初始化

### Task 5: 抽离 Provider Presets

**Files:**
- Create: `src/core/provider_presets.py`
- Modify: `src/gui/setup_wizard.py`
- Modify: `tests/test_setup_wizard.py`
- Create: `tests/test_provider_presets.py`

- [ ] **Step 1: 为无 GUI 导入编写测试**

测试必须能在未安装 PySide6 的环境中执行：

```python
from src.core.provider_presets import get_provider_preset

preset = get_provider_preset("ollama")
assert preset.embedding.base_url == "http://localhost:11434/v1"
assert preset.requires_api_key is False
```

- [ ] **Step 2: 定义不可变 preset 模型**

包含：

- canonical name
- display name
- embedding base URL/model
- LLM base URL/model
- reranker provider/base URL/model
- requires API key

- [ ] **Step 3: GUI 改为读取共用 preset**

`setup_wizard.py` 不再维护第二份 provider 字典。UI 文案和现有行为保持不变。

- [ ] **Step 4: 运行测试**

```powershell
python -m pytest tests/test_provider_presets.py tests/test_setup_wizard.py -q
```

- [ ] **Step 5: 提交**

```powershell
git add src/core/provider_presets.py src/gui/setup_wizard.py tests
git commit -m "refactor(config): share provider presets across interfaces"
```

### Task 6: 实现 shinehe 主 CLI

**Files:**
- Create: `src/cli.py`
- Create: `src/services/project_setup.py`
- Create: `src/services/doctor.py`
- Modify: `src/mcp_cli.py`
- Modify: `pyproject.toml`
- Modify: `scripts/setup_mcp.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_project_setup.py`
- Test: `tests/test_doctor.py`

- [ ] **Step 1: 添加脚本入口失败测试**

`pyproject.toml` 目标：

```toml
[project.scripts]
shinehe = "src.cli:main"
shinehe-mcp = "src.mcp_cli:main"
```

测试解析以下命令：

```text
shinehe init
shinehe init --local --path D:\docs --client claude-code,cursor
shinehe index D:\docs
shinehe watch D:\docs
shinehe doctor
shinehe mcp --transport stdio
```

- [ ] **Step 2: 实现 ProjectSetupService**

职责：

```python
class ProjectSetupService:
    def build_config(self, request: InitRequest) -> dict:
        preset = get_provider_preset(request.provider)
        return build_initial_config(request, preset)

    def write_config(self, target: Path, config: dict, force: bool = False) -> Path:
        if target.exists() and not force:
            raise FileExistsError(target)
        write_yaml_atomic(target, config)
        return target

    def configure_clients(self, clients: list[str], server_config: dict) -> list[Path]:
        return [
            merge_agent_config(client, server_config)
            for client in clients
        ]
```

复用 `scripts/setup_mcp.py` 的 JSON 合并规则，但将可复用逻辑移入 `src/services/project_setup.py`。

- [ ] **Step 3: 实现 init**

`--local` 必须生成：

- Ollama embedding/LLM 配置。
- `mcp.tool_profile=core`。
- `mcp.write_policy=disabled`。
- `rag.search_mode=blend`。
- `rag.parent_child.enabled=true`。
- 用户选择的知识目录。

已有配置存在时，不带 `--force` 必须拒绝覆盖。

- [ ] **Step 4: 实现 doctor**

检查项：

- config 可读。
- data 目录可写。
- SQLite、FTS5、sqlite-vec 可用。
- embedding/LLM endpoint 可达。
- reranker 状态明确。
- MCP 客户端配置文件 JSON 有效。
- indexed_files 状态无长期 pending。

退出码：

- 0：全部关键检查通过。
- 1：关键检查失败。
- 2：只有 warning。

- [ ] **Step 5: 将 mcp 子命令委托给现有入口**

不得复制 FastMCP 启动逻辑。将参数转换后调用 `src.mcp_cli.main(argv)`，并让 `mcp_cli.main` 支持可选 argv。

- [ ] **Step 6: 运行测试**

```powershell
python -m pytest tests/test_cli.py tests/test_project_setup.py tests/test_doctor.py tests/test_mcp_cli.py -q
```

- [ ] **Step 7: 提交 CLI**

```powershell
git add src/cli.py src/services/project_setup.py src/services/doctor.py src/mcp_cli.py pyproject.toml scripts/setup_mcp.py tests
git commit -m "feat(cli): add local setup and diagnostics commands"
```

## 模块 M3：目录增量索引

### Task 7: 建立 indexed_files 数据模型

**Files:**
- Create: `alembic/versions/g001_indexed_files.py`
- Create: `src/repositories/indexed_file_repo.py`
- Modify: `src/core/container.py`
- Test: `tests/test_indexed_file_repo.py`
- Test: `tests/test_migration.py`

- [ ] **Step 1: 编写迁移测试**

表结构：

```sql
CREATE TABLE indexed_files (
    path TEXT PRIMARY KEY,
    knowledge_id TEXT,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    last_indexed_at TEXT,
    last_error TEXT
);
CREATE INDEX idx_indexed_files_status ON indexed_files(status);
```

- [ ] **Step 2: 实现 Repository**

接口：

```python
get(path)
upsert(record)
mark_failed(path, error)
mark_deleted(path)
list_by_root(root)
list_by_status(status, limit)
delete(path)
```

所有路径进入 Repository 前必须规范化大小写和分隔符；Windows 路径比较使用 `normcase`。

- [ ] **Step 3: 注册到 Container**

新增 lazy property `indexed_file_repo`，不在 Repository 内回退到新的全局单例。

- [ ] **Step 4: 运行测试与迁移**

```powershell
python -m pytest tests/test_indexed_file_repo.py tests/test_migration.py -q
alembic upgrade head
```

- [ ] **Step 5: 提交**

```powershell
git add alembic src/repositories/indexed_file_repo.py src/core/container.py tests
git commit -m "feat(index): track indexed file state"
```

### Task 8: 实现 PathIndexService

**Files:**
- Create: `src/models/indexing.py`
- Create: `src/services/path_indexer.py`
- Modify: `src/core/container.py`
- Modify: `src/services/async_tasks.py`
- Test: `tests/test_path_indexer.py`

- [ ] **Step 1: 定义模型与失败测试**

模型：

```python
@dataclass
class FileFingerprint:
    path: Path
    size: int
    mtime_ns: int
    sha256: str

@dataclass
class IndexResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    failed: list[dict] = field(default_factory=list)
    job_id: str | None = None
```

- [ ] **Step 2: 实现 manifest 扫描**

规则：

- 只扫描 `file_parser` 支持的后缀。
- 默认跳过隐藏目录、`.git`、`node_modules`、虚拟环境和配置的 ignore patterns。
- 首轮用 size + mtime 快速判断，可能变化时再算 SHA-256。
- manifest 按规范化路径排序，保证结果可复现。

- [ ] **Step 3: 实现 diff**

输出四类：

```python
ManifestDiff(created=[], modified=[], unchanged=[], deleted=[])
```

无变更文件不能进入 parser、embedding 或 vector store。

- [ ] **Step 4: 实现 apply**

- created：复用现有统一文件导入路径。
- modified：更新原知识条目，重建其 Block/vector/FTS。
- deleted：软删除知识条目并标记 indexed_files.deleted。
- failed：保留旧索引，记录错误，不把状态伪装为成功。

- [ ] **Step 5: 接入大目录异步任务**

超过以下任一阈值转 job：

- 文件数。
- 总字节数。
- 单文件现有复杂度阈值。

handler 逐文件更新进度，支持 cancel。

- [ ] **Step 6: 运行测试**

```powershell
python -m pytest tests/test_path_indexer.py tests/test_async_ingest.py tests/test_full_pipeline_e2e.py -q
```

- [ ] **Step 7: 提交**

```powershell
git add src/models/indexing.py src/services/path_indexer.py src/services/async_tasks.py src/core/container.py tests
git commit -m "feat(index): add incremental path indexing service"
```

### Task 9: 实现 Watcher 与 Scheduler

**Files:**
- Create: `src/services/file_watcher.py`
- Create: `src/services/index_scheduler.py`
- Modify: `pyproject.toml`
- Modify: `src/cli.py`
- Modify: `config.example.yaml`
- Test: `tests/test_file_watcher.py`
- Test: `tests/test_index_scheduler.py`

- [ ] **Step 1: 增加 optional dependency**

```toml
watch = ["watchdog>=4.0"]
```

`all` extra 包含 `watch`，核心安装不强制安装。

- [ ] **Step 2: 定义 Scheduler 行为测试**

覆盖：

- 同一路径 500ms 内多个 modify 合并一次。
- delete + create 合并为 modify。
- 不同路径独立排队。
- shutdown 停止接收新任务并等待当前任务。

- [ ] **Step 3: 实现 IndexScheduler**

Scheduler 只负责事件合并和调用 `PathIndexService`，不直接访问数据库、parser 或 vector store。

- [ ] **Step 4: 实现 FileWatcher**

职责：

- 校验 root。
- 启动 watchdog observer。
- 将事件规范化后推入 scheduler。
- 输出状态和最后错误。

- [ ] **Step 5: 接入 CLI**

```powershell
shinehe watch D:\docs --recursive
```

Ctrl+C 必须优雅停止并返回 0。

- [ ] **Step 6: 运行测试**

```powershell
python -m pytest tests/test_file_watcher.py tests/test_index_scheduler.py tests/test_cli.py -q
```

- [ ] **Step 7: 提交**

```powershell
git add src/services/file_watcher.py src/services/index_scheduler.py src/cli.py pyproject.toml config.example.yaml tests
git commit -m "feat(index): watch local directories for incremental updates"
```

## 模块 M4：检索候选与引用统一

### Task 10: 引入 RetrievalCandidate

**Files:**
- Create: `src/models/retrieval.py`
- Modify: `src/services/hybrid_search.py`
- Modify: `src/services/search_service.py`
- Modify: `src/services/rag_pipeline.py`
- Test: `tests/test_retrieval_candidate_contract.py`
- Test: `tests/test_search_service.py`

- [ ] **Step 1: 定义候选模型**

必须包含：

```python
block_id
knowledge_id
text
metadata
vector_score
keyword_score
rrf_score
rerank_score
final_score
match_channels
warnings
```

- [ ] **Step 2: 标准化 vector/FTS 结果**

向量 distance 转为 0-1 相似度时使用唯一 helper；FTS rank 转换也使用唯一 helper。原始值保留在 metadata 供诊断。

- [ ] **Step 3: RRF 保留组成**

每个候选记录：

- vector rank。
- keyword rank。
- 两个 RRF contribution。
- 总 RRF score。
- `match_channels`。

- [ ] **Step 4: 修复 rerank 后过滤**

下游只使用 `final_score`。优先级：

```text
rerank_score -> rrf_score -> vector/keyword normalized score
```

禁止再次读取未定义的 `score` 过滤。

- [ ] **Step 5: 运行测试**

```powershell
python -m pytest tests/test_retrieval_candidate_contract.py tests/test_search_service.py tests/test_search.py -q
```

- [ ] **Step 6: 提交**

```powershell
git add src/models/retrieval.py src/services/hybrid_search.py src/services/search_service.py src/services/rag_pipeline.py tests
git commit -m "refactor(rag): normalize retrieval candidate scoring"
```

### Task 11: 建立统一 Citation Builder

**Files:**
- Create: `src/models/citation.py`
- Create: `src/services/citation_builder.py`
- Modify: `src/services/file_parser.py`
- Modify: `src/services/indexer.py`
- Modify: `src/services/block_store.py`
- Modify: `src/services/rag_pipeline.py`
- Modify: `src/mcp_server.py`
- Test: `tests/test_citation_builder.py`
- Test: `tests/test_rag_sources.py`
- Test: `tests/test_mcp_rag_full.py`

- [ ] **Step 1: 编写各格式位置测试**

fixture 至少覆盖：

- PDF page。
- Excel sheet + row range。
- PPTX slide。
- Markdown heading path + paragraph index。
- Python line range。

- [ ] **Step 2: 定义 Citation**

字段严格遵循 Spec 第 9.3 节。缺失位置必须为 `None`，不能猜测。

- [ ] **Step 3: 在解析和索引阶段保留 location metadata**

结构化 Block properties 进入 BlockStore metadata 时不能丢失 page/sheet/slide/heading/line 信息。

- [ ] **Step 4: 实现 CitationBuilder**

```python
class CitationBuilder:
    def build(self, candidate: RetrievalCandidate, item: dict | None) -> Citation:
        location = location_from_metadata(candidate["metadata"])
        return Citation(
            document=(item or {}).get("title", candidate["metadata"].get("title", "未知")),
            path=candidate["metadata"].get("source_path", ""),
            knowledge_id=candidate["knowledge_id"],
            block_id=candidate["block_id"],
            location=location,
            score=candidate["final_score"],
            score_breakdown=score_breakdown(candidate),
            match_channels=candidate["match_channels"],
            reason=build_match_reason(candidate),
            text=candidate["text"],
        )
```

`reason` 由 `match_channels`、是否 rerank、是否 parent expansion 生成，不调用 LLM。

- [ ] **Step 5: search 和 ask 共用 builder**

禁止在 MCP、SearchService 和 RagPipeline 中分别拼接不同 sources。

- [ ] **Step 6: 修复 source 去重**

默认按 `block_id` 去重，并增加：

```yaml
rag:
  max_blocks_per_document: 3
```

- [ ] **Step 7: 运行测试**

```powershell
python -m pytest tests/test_citation_builder.py tests/test_rag_sources.py tests/test_mcp_rag_full.py tests/test_mcp_server.py -q
```

- [ ] **Step 8: 提交**

```powershell
git add src/models/citation.py src/services/citation_builder.py src/services/file_parser.py src/services/indexer.py src/services/block_store.py src/services/rag_pipeline.py src/mcp_server.py tests
git commit -m "feat(rag): return structured explainable citations"
```

### Task 12: 统一上下文扩展配置

**Files:**
- Modify: `src/services/hybrid_search.py`
- Modify: `src/services/block_context.py`
- Modify: `src/services/parent_child_retrieval.py`
- Modify: `config.example.yaml`
- Test: `tests/test_parent_child.py`
- Test: `tests/test_embedding_context.py`

- [ ] **Step 1: 增加显式默认配置**

```yaml
rag:
  context_sibling_window: 1
  parent_child:
    enabled: true
    max_parent_chars: 4000
  max_blocks_per_document: 3
```

- [ ] **Step 2: 确认原文不被上下文污染**

测试 `blocks.content` 保持原文，而 embedding/prompt context 包含父链和相邻块。

- [ ] **Step 3: 确认 citation 仍指向命中子 Block**

parent content 只进入生成上下文，不替换 `citation.block_id` 和 `citation.text`。

- [ ] **Step 4: 运行测试**

```powershell
python -m pytest tests/test_parent_child.py tests/test_embedding_context.py tests/test_block_store.py -q
```

- [ ] **Step 5: 提交**

```powershell
git add src/services/hybrid_search.py src/services/block_context.py src/services/parent_child_retrieval.py config.example.yaml tests
git commit -m "feat(rag): standardize retrieval context expansion"
```

## 模块 M5：本地 Reranker Provider

### Task 13: 拆分 reranker provider

**Files:**
- Create: `src/services/rerankers/__init__.py`
- Create: `src/services/rerankers/base.py`
- Create: `src/services/rerankers/api.py`
- Create: `src/services/rerankers/local.py`
- Create: `src/services/rerankers/llm.py`
- Create: `src/services/rerankers/factory.py`
- Modify: `src/services/reranker.py`
- Modify: `pyproject.toml`
- Modify: `config.example.yaml`
- Test: `tests/test_reranker_providers.py`

- [ ] **Step 1: 定义统一接口**

```python
class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[RetrievalCandidate], top_n: int) -> list[RetrievalCandidate]:
        raise NotImplementedError
```

- [ ] **Step 2: 迁移现有 API 与 LLM 行为**

先保持结果一致，再增加 local provider；不得在同一提交中改变评分阈值。

- [ ] **Step 3: 增加可选本地依赖**

```toml
local-rerank = ["sentence-transformers>=3.0"]
```

未安装 extra 时：

- `doctor` 显示 unavailable。
- 检索按配置回退到 LLM、RRF 或 disabled。
- 不自动执行 pip 安装或模型下载。

- [ ] **Step 4: 实现 lazy load**

本地模型首次 rerank 时加载，进程内复用。测试用 fake model loader，CI 不下载模型。

- [ ] **Step 5: 增加失败降级测试**

覆盖 endpoint 失败、模型缺失、加载异常和 timeout。每种情况必须保留候选并写 warning。

- [ ] **Step 6: 运行测试**

```powershell
python -m pytest tests/test_reranker_providers.py tests/test_search_service.py tests/test_rag_messages.py -q
```

- [ ] **Step 7: 提交**

```powershell
git add src/services/rerankers src/services/reranker.py pyproject.toml config.example.yaml tests
git commit -m "feat(rag): support pluggable local reranking"
```

## 模块 M6：Eval 与质量门禁

### Task 14: 建立可复现检索 fixture

**Files:**
- Create: `evals/fixtures/`
- Create: `evals/datasets/retrieval_zh.yaml`
- Create: `evals/datasets/retrieval_code.yaml`
- Create: `evals/datasets/retrieval_table.yaml`
- Create: `evals/datasets/retrieval_pdf.yaml`
- Modify: `evals/datasets/no_answer.yaml`
- Test: `tests/test_eval_datasets.py`

- [ ] **Step 1: 创建固定文档集**

内容必须包含可区分的：

- 中文事实。
- 精确符号和文件名。
- 代码函数与错误信息。
- 表格行。
- PDF 页码事实。
- 近义但不正确的干扰文档。

- [ ] **Step 2: 数据集使用稳定 selector**

每题包含：

```yaml
query: "数据库默认使用什么存储引擎？"
expected_sources:
  - path: "fixtures/architecture.md"
    block_contains: "SQLite + FTS5"
    location:
      heading_path: ["Architecture", "Storage"]
must_not_match:
  - path: "fixtures/distractor.md"
category: "keyword"
```

运行时索引完成后，将 selector 解析为实际 block id。

- [ ] **Step 3: 校验数据集**

测试拒绝：

- 空 expected source。
- 不存在的 fixture。
- 无法解析的 block selector。
- 重复 query。

- [ ] **Step 4: 提交 fixture**

```powershell
git add evals/fixtures evals/datasets tests/test_eval_datasets.py
git commit -m "test(eval): add deterministic retrieval fixtures"
```

### Task 15: 实现离线 Retrieval Eval

**Files:**
- Create: `evals/run_retrieval_eval.py`
- Create: `evals/baselines/local.json`
- Modify: `evals/metrics.py`
- Modify: `evals/run_eval.py`
- Test: `tests/test_rag_eval.py`
- Test: `tests/test_retrieval_eval_runner.py`

- [ ] **Step 1: 增加指标**

实现：

- Recall@5。
- MRR。
- nDCG@10。
- citation location completeness。
- no-answer accuracy。
- latency P50/P95。

- [ ] **Step 2: 隔离检索评测**

`run_retrieval_eval.py`：

1. 创建临时 SHINEHE_HOME。
2. 索引 fixture。
3. 使用 deterministic fake embedding 或固定向量。
4. 不调用生成 LLM。
5. 运行 SearchService。
6. 输出 JSON/Markdown。

- [ ] **Step 3: 增加阈值比较**

命令：

```powershell
python evals/run_retrieval_eval.py --all --baseline evals/baselines/local.json --max-regression 0.02
```

明显回退时退出 1；通过时退出 0。

- [ ] **Step 4: 更新旧 runner**

`run_eval.py` 保留 answer eval，用于需要模型的发布前验证；文案不再把空 golden source 的结果称为 Recall 基线。

- [ ] **Step 5: 运行测试**

```powershell
python -m pytest tests/test_rag_eval.py tests/test_retrieval_eval_runner.py -q
python evals/run_retrieval_eval.py --all --baseline evals/baselines/local.json
```

- [ ] **Step 6: 提交**

```powershell
git add evals tests
git commit -m "feat(eval): gate retrieval quality with golden sources"
```

### Task 16: 接入 CI

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `docs/retrieval-quality.md`

- [ ] **Step 1: 增加 retrieval-eval job**

Job 必须：

- 只安装核心、parser 和 dev 依赖。
- 不读取 API key。
- 不下载本地大模型。
- 上传 JSON/Markdown 报告 artifact。

- [ ] **Step 2: 保留测试边界**

CI 分为：

- core unit/contract。
- MCP smoke。
- retrieval eval。
- optional frontend/docker。

不要用一个超长 `pytest tests -q` 作为唯一反馈。

- [ ] **Step 3: 文档化指标定义与基线更新规则**

基线更新必须在 PR 中展示旧值、新值和原因，禁止为了绿灯静默降低阈值。

- [ ] **Step 4: 本地验证 workflow 对应命令**

```powershell
python -m pytest tests/test_mcp_tool_profiles.py tests/test_mcp_contract.py tests/test_path_indexer.py tests/test_citation_builder.py -q
python evals/run_retrieval_eval.py --all --baseline evals/baselines/local.json
```

- [ ] **Step 5: 提交**

```powershell
git add .github/workflows/ci.yml docs/retrieval-quality.md
git commit -m "ci: enforce retrieval quality baseline"
```

## 模块 M7：README、Demo 与发布收口

### Task 17: 重写产品首页与文档导航

**Files:**
- Modify: `README.md`
- Modify: `README_zh.md`
- Modify: `pyproject.toml`
- Modify: `CLAUDE.md`
- Modify: `docs/mcp/agent-usage.md`
- Create: `docs/advanced-features.md`
- Create: `docs/migration/mcp-tool-profiles.md`

- [ ] **Step 1: 重写 README 第一屏**

第一屏固定包含：

- 一句话定位。
- 本地优先说明。
- `shinehe init --local` Quick Start。
- 一段带引用的 search/ask 返回示例。
- 支持客户端。

不在第一屏展示 51 个工具、Wiki、Graph、Plugin、RBAC 或 Windows Service。

- [ ] **Step 2: 增加 Core vs Experimental**

明确高级能力仍存在，但默认关闭；链接到 `docs/advanced-features.md`。

- [ ] **Step 3: 增加 profile 迁移指南**

包含：

```yaml
# 新用户
mcp:
  tool_profile: core

# 老客户端保持完整工具
mcp:
  tool_profile: legacy
  enable_legacy_aliases: true
```

- [ ] **Step 4: 同步项目元数据**

`pyproject.toml.description`、CLAUDE.md 架构说明、MCP 文档的工具数量和推荐流程必须与实际 registry 一致。

- [ ] **Step 5: 增加文档契约测试**

扩展 `tests/test_mcp_docs_prompts.py`：

- README 出现 core 定位和 init 命令。
- 文档不再写死错误工具数量。
- 推荐流程只引用 core 可见工具。

- [ ] **Step 6: 运行测试**

```powershell
python -m pytest tests/test_mcp_docs_prompts.py tests/test_mcp_tool_profiles.py -q
```

- [ ] **Step 7: 提交**

```powershell
git add README.md README_zh.md pyproject.toml CLAUDE.md docs tests/test_mcp_docs_prompts.py
git commit -m "docs: position project as local MCP retrieval engine"
```

### Task 18: 建立可重复 Demo

**Files:**
- Create: `scripts/demo_local_retrieval.py`
- Create: `docs/demo-local-retrieval.md`
- Create: `tests/test_demo_local_retrieval.py`

- [ ] **Step 1: 实现非交互 Demo**

Demo 使用临时目录完成：

1. 生成本地配置。
2. 创建两篇 fixture 文档。
3. 索引目录。
4. 调用 search。
5. 校验 Citation。
6. 修改一篇文件。
7. 执行增量扫描。
8. 再次 search 并验证新内容。

- [ ] **Step 2: 支持 dry-run 和保留工作目录**

参数：

```text
--workdir
--keep
--json-output
```

- [ ] **Step 3: 编写 smoke test**

测试使用 fake embedding，不调用外部服务，必须在 CI 中完成。

- [ ] **Step 4: 运行 Demo**

```powershell
python scripts/demo_local_retrieval.py --json-output .codex_tmp/demo-result.json
```

预期：退出 0，JSON 中 `initial_hit=true`、`incremental_update=true`、`citation_complete=true`。

- [ ] **Step 5: 提交**

```powershell
git add scripts/demo_local_retrieval.py docs/demo-local-retrieval.md tests/test_demo_local_retrieval.py
git commit -m "feat(demo): prove local indexing and cited retrieval flow"
```

### Task 19: 发布前综合验证

**Files:**
- Modify: `PROGRESS.md`
- Modify: `src/version.py`

- [ ] **Step 1: 运行核心回归**

```powershell
python -m pytest `
  tests/test_mcp_tool_profiles.py `
  tests/test_mcp_contract.py `
  tests/test_mcp_server.py `
  tests/test_mcp_docs_prompts.py `
  tests/test_operation_safety.py `
  tests/test_async_ingest.py `
  tests/test_path_indexer.py `
  tests/test_file_watcher.py `
  tests/test_retrieval_candidate_contract.py `
  tests/test_citation_builder.py `
  tests/test_parent_child.py `
  tests/test_embedding_context.py `
  tests/test_retrieval_eval_runner.py -q
```

- [ ] **Step 2: 运行工程门禁**

```powershell
ruff check src tests evals
mypy src --ignore-missing-imports
npm --prefix client run build
docker build --target mcp .
```

如 mypy 仍为 advisory，必须明确记录结果，不能声称全绿。

- [ ] **Step 3: 运行质量与 Demo**

```powershell
python evals/run_retrieval_eval.py --all --baseline evals/baselines/local.json
python scripts/demo_local_retrieval.py
```

- [ ] **Step 4: 手工 MCP smoke**

验证：

- core 模式只显示 10 个工具。
- legacy 模式显示完整工具和别名。
- `kb_capabilities` 与实际工具一致。
- write disabled 时 `index_path` 实际执行被拒绝、dry-run 可用。

- [ ] **Step 5: 更新进度与版本**

进度文档必须分别列出：

- 已完成。
- 延后项。
- 仅 targeted tests 验证的边界。
- retrieval eval 指标。
- 已知兼容风险。

- [ ] **Step 6: 提交发布收口**

```powershell
git add PROGRESS.md src/version.py
git commit -m "chore(release): finalize MCP local retrieval focus"
```

## 最终验收矩阵

| 验收项 | 自动化证据 | 人工证据 |
| --- | --- | --- |
| 默认 8-12 个工具 | `test_mcp_tool_profiles.py` | MCP 客户端 tools list |
| legacy 不破坏 | legacy snapshot | 旧配置连接 |
| 一键本地初始化 | CLI tests | 全新目录运行 |
| 目录增量索引 | path/watcher tests | 修改文件后重新提问 |
| 引用完整 | citation tests | PDF/Excel/代码样例 |
| reranker 可降级 | provider tests | doctor 输出 |
| 检索质量门禁 | retrieval eval | 报告审阅 |
| 默认最小依赖 | package/docker build | 干净环境安装 |
| README 单一叙事 | docs contract tests | 首页审阅 |

## 不进入本计划的后续项

- 删除或重写 GUI。
- 删除 Wiki/Graph/Memory 代码。
- 多用户和 RBAC。
- 云端同步。
- 插件市场。
- 将所有服务目录重组为新的顶层 package。
- 对完整 Web 管理后台继续扩展。

这些能力只有在直接提升 MCP 本地检索体验，或核心产品稳定发布后，才重新进入路线图。
