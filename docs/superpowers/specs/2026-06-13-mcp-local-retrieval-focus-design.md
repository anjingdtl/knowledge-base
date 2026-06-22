# MCP 本地高精准检索收束改造 Spec

> **日期**: 2026-06-13
> **状态**: Implemented and health-reviewed in v1.3.1
> **输入建议**: `F:\迅雷下载\knowledge_base_mcp_local_retrieval_optimization.docx`
> **适用项目**: ShineHeKnowledge
> **核心定位**: Local-first MCP knowledge retrieval engine for AI assistants.
> **验证**: 2026-06-13 全量测试 `828 passed, 2 skipped`，Ruff/mypy/前端构建/检索评测/Demo 全绿；本地 Docker 因无 CLI 由远端 CI 验证。

## 1. 执行决策

ShineHeKnowledge 下一阶段不再以“继续增加知识管理功能”为主线，而是收束为：

> 把本地文档持续索引为可被 Claude、Cursor、Cline 等 AI Agent 稳定调用、可解释、可评测的私有 MCP 知识检索服务。

本次改造采用“兼容式收束”，不做大爆炸式重写：

1. 默认产品面只突出 MCP、本地索引、高精准检索和引用溯源。
2. 现有 GUI、API、Wiki、Graph、Agent Memory、插件和高级运维能力继续保留。
3. 默认 MCP 工具面压缩到 8-12 个；完整工具通过配置档显式启用。
4. 复用已经完成的混合检索、RRF、reranker、Parent-Child、MCP 安全闭环和异步任务。
5. 新增工作的重点是工具暴露收束、命令行初始化、目录持续索引、统一引用契约和可发布的检索质量门禁。

## 2. 建议稿与当前实现对照

外部建议稿基于产品表象提出了正确方向，但部分能力在当前仓库中已经存在。改造必须先区分“已有能力”和“真实缺口”。

| 建议项 | 当前状态 | 本次处理 |
| --- | --- | --- |
| Vector + FTS5 + RRF 混合检索 | 已实现于 `src/services/hybrid_search.py` | 保留，统一候选分数与命中解释 |
| Query rewrite | 已实现 | 保留，纳入质量评测 |
| Reranker | 已实现 API/LLM fallback | 增加 provider 边界和可选本地 reranker |
| Chunk/Parent expansion | 已有 BlockContext 与 Parent-Child | 统一配置，补齐默认值和回归测试 |
| Block 级引用 | 已有基础字段 | 补齐路径、页码/行号、匹配原因和分数组成 |
| MCP 写操作默认安全 | 已有 `write_policy`、dry-run、audit、undo | 默认配置改为只读；不重复建设安全系统 |
| 大文件异步导入 | 已实现 | 由新的 `index_path` 复用 |
| Ollama | GUI 首次向导已有预设 | 抽离为 CLI/GUI 共用配置档 |
| MCP 客户端配置 | 已有模板和 `scripts/setup_mcp.py` | 收口到 `shinehe init` |
| Eval benchmark | 已有运行器和指标骨架 | 修复数据集无有效 golden source、增加阈值门禁 |
| 目录增量维护 | 尚未完成 | 本次核心新增 |
| 8-12 个核心 MCP 工具 | 当前 51 个原始工具和约 51 个别名全部注册 | 本次核心新增 |
| README 单一叙事 | 当前首页仍强调图谱、Wiki、插件和 51 个工具 | 本次核心改造 |

## 3. 目标与非目标

### 3.1 目标

1. 新用户在 5 分钟内完成本地配置、索引目录和 MCP 客户端接入。
2. 默认 MCP 工具列表控制在 8-12 个，Agent 能稳定选择正确工具。
3. 默认检索链路固定为 query rewrite -> vector + FTS5 -> RRF -> rerank -> context expansion -> citation packaging。
4. `search` 与 `ask` 返回统一、可追溯、可解释的引用结构。
5. 本地目录发生新增、修改、删除时可增量同步索引。
6. 检索质量通过真实 golden dataset、基线文件和 CI 门禁证明。
7. 默认安装不要求 GUI、外部图数据库、Web Client、Wiki 或插件依赖。

### 3.2 非目标

1. 不删除现有 GUI、API、Wiki、Graph、Agent Memory 或插件代码。
2. 不在本轮引入新的向量数据库或外部图数据库作为核心依赖。
3. 不重写整个 `src/services` 目录。
4. 不新增多用户、RBAC、团队协作或云端托管能力。
5. 不把 `shinehe init` 做成新的 GUI；CLI 使用标准输入输出即可。
6. 不承诺所有高级工具都适合默认 Agent 调用。

## 4. 产品分层

### 4.1 核心层

默认安装、默认文档和默认 MCP 工具面只覆盖：

- MCP Server
- 本地文件解析与索引
- SQLite + sqlite-vec + FTS5
- 混合检索与 RRF
- query rewrite 与 rerank
- Block 上下文扩展
- 引用与命中解释
- 目录增量索引
- Eval 与诊断

### 4.2 增强层

安装后可显式启用：

- 结构化 Query DSL
- source graph
- 高级异步任务
- API 服务
- GUI 索引管理器
- 本地 cross-encoder reranker

### 4.3 实验层

默认不注册到 MCP 工具面，不出现在 README 第一屏：

- Wiki workflow
- SQLite graph traversal
- Agent Memory
- Plugin system
- Web 管理后台
- 多用户和权限系统

## 5. 目标用户路径

### 5.1 首次使用

```text
pip install shinehe-knowledge[parsers]
  -> shinehe init --local --path D:\docs --client claude-code
  -> shinehe index D:\docs
  -> Agent 调用 kb_capabilities
  -> Agent 调用 search / ask
  -> 返回带路径和原文位置的引用
```

### 5.2 长期使用

```text
shinehe watch D:\docs
  -> 文件新增/修改/删除
  -> debounce + hash diff
  -> 增量解析和索引
  -> Agent 下一次查询自动使用最新内容
```

### 5.3 诊断

```text
shinehe doctor
  -> 检查配置目录、SQLite/FTS/sqlite-vec、模型端点、索引状态和 MCP 配置
  -> 输出可执行修复建议
```

## 6. MCP 工具面设计

### 6.1 工具配置档

新增配置：

```yaml
mcp:
  tool_profile: core
  enable_legacy_aliases: false
  experimental_tools_enabled: false
  write_policy: disabled
  allow_http_write: false
```

支持以下配置档：

| 配置档 | 用途 | 工具范围 |
| --- | --- | --- |
| `core` | 默认 Agent 检索 | 8-12 个稳定核心工具 |
| `extended` | 高级研究与诊断 | core + Query DSL + source graph + job 管理 |
| `admin` | 本地人工维护 | extended + CRUD + audit/undo |
| `full` | 全功能用户 | 全部非实验原始工具；显式开关后追加实验工具，不自动增加别名 |
| `legacy` | 旧客户端兼容 | 当前全部原始工具 + namespaced aliases |

### 6.2 默认核心工具

默认 `core` 配置档注册以下 10 个工具：

| 工具 | 目的 | 副作用 |
| --- | --- | --- |
| `ping` | 连通性检查 | 只读 |
| `kb_capabilities` | 返回当前配置档、能力、限制和推荐流程 | 只读 |
| `search` | 高精准检索，返回结构化引用 | 只读 |
| `ask` | 基于检索结果生成带引用回答 | 只读 |
| `read` | 按文档或 Block 读取原文 | 只读 |
| `list_knowledge` | 列出已索引文档 | 只读 |
| `index_path` | 索引文件或目录，可返回异步任务 | 写 |
| `get_job` | 查询索引任务 | 只读 |
| `list_jobs` | 列出索引任务 | 只读 |
| `reindex_all` | 修复或重建索引 | 写/管理 |

设计约束：

- `index_path` 是文件、目录和大文件任务的统一入口。
- 默认 `write_policy=disabled`，因此 Agent 默认只能检索；用户显式开启后才能从 MCP 索引。
- CLI 和文件监听不受 MCP 工具暴露配置影响。
- `route_query`、`structured_query`、`ask_with_query` 等高级入口进入 `extended`。
- Wiki、Graph、Memory 工具只进入 `full/legacy`，并受 `experimental_tools_enabled` 控制。

### 6.3 注册方式

当前 `@mcp.tool` 在模块导入时直接注册全部工具，不适合按配置档筛选。目标结构为：

```text
src/mcp/
  tool_registry.py     # ToolDefinition、profile 过滤、注册
  tool_profiles.py     # core/extended/admin/full/legacy 清单
  aliases.py           # 旧别名，只在 legacy 启用
src/mcp_server.py      # 工具实现、prompt/resource、server lifespan
```

工具函数继续复用现有实现。第一轮不按业务域拆分 51 个函数，避免同时进行产品收束和大规模代码搬迁。

## 7. 本地初始化与配置

### 7.1 统一 CLI

新增主命令：

```text
shinehe init
shinehe init --local
shinehe init --path <directory>
shinehe init --client claude-code,cursor,cline
shinehe index <path>
shinehe watch <path>
shinehe doctor
shinehe mcp [现有 shinehe-mcp 参数]
```

`shinehe-mcp` 保留，避免破坏已有配置。

### 7.2 配置档复用

将 `src/gui/setup_wizard.py` 内的 `PROVIDER_PRESETS` 抽离为无 GUI 依赖模块，供 CLI、GUI 和测试共同使用。

本地档默认：

```yaml
embedding:
  base_url: http://localhost:11434/v1
  model: nomic-embed-text
  provider: ollama

llm:
  base_url: http://localhost:11434/v1
  model: qwen2.5
  provider: ollama

reranker:
  provider: disabled
  enabled: false

rag:
  search_mode: blend
  enable_query_rewriting: true
  enable_rerank: false
  context_sibling_window: 1
  parent_child:
    enabled: true

mcp:
  tool_profile: core
  write_policy: disabled
```

用户安装本地 reranker extra 后，初始化命令可显式启用：

```yaml
reranker:
  provider: local
  enabled: true
  model: BAAI/bge-reranker-v2-m3
```

### 7.3 配置位置

- 默认用户目录：`~/.shinehe/config.yaml`
- 数据目录：`~/.shinehe/data`
- `SHINEHE_HOME` 可覆盖默认位置。
- 项目根目录已有 `config.yaml` 的老用户继续兼容。
- CLI 写配置前必须展示目标路径，覆盖已有文件需要 `--force` 或交互确认。

## 8. 目录索引生命周期

### 8.1 统一 Path Index Service

新增 `PathIndexService`，同时服务 CLI、MCP 和 watcher：

```python
index_path(
    path: Path,
    recursive: bool = True,
    dry_run: bool = False,
    force: bool = False,
) -> IndexResult
```

处理规则：

1. 单文件复用现有 `ingest_file` 服务。
2. 目录按支持类型扫描，生成稳定 manifest。
3. 大文件或大目录转为异步 job。
4. 相同 hash 跳过。
5. 修改文件更新对应知识条目和 Block 索引。
6. 删除文件默认软删除对应知识条目。
7. 路径必须通过允许根目录校验。

### 8.2 文件状态表

新增 `indexed_files` 表：

| 字段 | 说明 |
| --- | --- |
| `path` | 规范化绝对路径，唯一键 |
| `knowledge_id` | 对应知识条目 |
| `size` | 文件大小 |
| `mtime_ns` | 修改时间 |
| `sha256` | 内容 hash |
| `status` | indexed / pending / failed / deleted |
| `last_indexed_at` | 最近成功索引时间 |
| `last_error` | 最近错误 |

### 8.3 Watcher

- `watchdog` 放入可选依赖 `watch`。
- 事件先 debounce，再由 `IndexScheduler` 合并。
- 原子保存产生的 delete/create 序列应合并为一次 update。
- watcher 退出时停止接收新任务并等待当前任务到安全检查点。
- watcher 的状态可被 `shinehe doctor` 和 GUI 管理页读取。

## 9. 高精准检索契约

### 9.1 统一候选模型

当前候选结果混用 `distance`、`rrf_score`、`rerank_score` 和 `score`。新增内部统一模型：

```python
class RetrievalCandidate(TypedDict):
    block_id: str
    knowledge_id: str
    text: str
    metadata: dict
    vector_score: float | None
    keyword_score: float | None
    rrf_score: float | None
    rerank_score: float | None
    final_score: float
    match_channels: list[str]
```

`final_score` 是所有下游过滤、排序和引用展示的唯一排序字段。

### 9.2 默认链路

```text
query rewrite
  -> vector retrieval + FTS5 retrieval
  -> RRF fusion
  -> metadata filter
  -> rerank
  -> parent/sibling expansion
  -> citation packaging
  -> answer grounding
```

约束：

- FTS 命中不能被向量候选完全挤出。
- reranker 失败时保留 RRF 排序并写入 warning。
- 不再用不存在的 `score` 字段二次过滤 rerank 结果。
- source 去重以 `block_id` 为主，而不是只按 `knowledge_id` 去重。
- 每个文档默认最多保留可配置数量的 Block，避免单文档垄断上下文。

### 9.3 统一引用结构

`search` 和 `ask.sources` 共享以下结构：

```json
{
  "document": "architecture.md",
  "path": "D:/docs/architecture.md",
  "knowledge_id": "doc_001",
  "block_id": "doc_001_block_07",
  "location": {
    "page": null,
    "sheet": null,
    "slide": null,
    "heading_path": ["Architecture", "Storage"],
    "paragraph_index": 12,
    "line_start": null,
    "line_end": null
  },
  "score": 0.87,
  "score_breakdown": {
    "vector": 0.82,
    "keyword": 0.64,
    "rrf": 0.031,
    "rerank": 0.87
  },
  "match_channels": ["semantic", "keyword"],
  "reason": "semantic + keyword match; reranked",
  "text": "SQLite 使用 WAL 模式保存本地索引。"
}
```

位置字段按格式尽力提供：

- PDF：页码。
- Excel：sheet + row range。
- PPTX：slide。
- Markdown/DOCX：heading path + paragraph index。
- 代码/文本：line range。

## 10. Reranker 设计

保留 `LLMReranker` 对外行为，但拆出 provider：

```text
Reranker
  -> ApiReranker
  -> LocalCrossEncoderReranker (optional extra)
  -> LLMFallbackReranker
  -> DisabledReranker
```

要求：

- 未安装本地依赖时返回明确诊断，不静默下载大模型。
- 模型加载延迟到首次调用，并缓存单实例。
- reranker 失败不导致检索失败。
- `shinehe doctor` 显示当前 provider、模型和可用状态。

## 11. Eval 与发布门禁

### 11.1 当前问题

现有 `evals/datasets/*.yaml` 的 `relevant_knowledge_ids` 多数为空，Recall/MRR 无法形成真实质量约束；`citation_accuracy` 和 `faithfulness` 也主要以“是否存在 source”判断，不能证明引用正确。

### 11.2 新评测结构

```text
evals/
  fixtures/               # 固定可索引文档
  datasets/
    retrieval_zh.yaml
    retrieval_code.yaml
    retrieval_pdf.yaml
    retrieval_table.yaml
    no_answer.yaml
  baselines/
    local.json
  run_retrieval_eval.py
  run_answer_eval.py
```

检索数据集至少包含：

- query
- expected source path
- expected block selector 或 block id
- expected location
- query category
- must_include / must_not_include

### 11.3 指标

- Recall@5
- MRR
- nDCG@10
- Citation location completeness
- No-answer accuracy
- P50/P95 latency
- 索引增量更新正确率

### 11.4 门禁

PR 离线门禁：

- 固定 fixture。
- 不调用云端 LLM。
- 不要求本地大模型。
- 检索指标不能低于基线容差。

发布前完整门禁：

- 使用推荐本地配置或指定 provider。
- 运行 answer/citation 评测。
- 生成 Markdown 和 JSON 报告。

## 12. README、文档与 Demo

README 第一屏只回答：

1. 项目解决什么问题。
2. 数据是否留在本地。
3. 如何在 5 分钟内接入 Agent。
4. 返回的引用长什么样。

推荐结构：

```text
定位 + 30 秒 Demo
  -> Quick Start
  -> Core Features
  -> Supported Clients
  -> Retrieval Quality
  -> Core vs Experimental
  -> Advanced Docs
```

高级功能迁移到独立文档导航，不从代码中删除。

Demo 必须覆盖：

1. 初始化本地配置。
2. 索引目录。
3. 通过 MCP 提问。
4. 返回结构化引用。
5. 修改文件。
6. watcher 增量更新。
7. 再次提问得到新内容。

## 13. 兼容策略

1. `shinehe-mcp` 保留。
2. 原工具函数和返回 envelope 保留。
3. 老配置未设置 `mcp.tool_profile` 时，首次升级按 `legacy` 兼容；新生成配置默认 `core`。
4. namespaced aliases 只在 `legacy` 或显式配置下注册。
5. `kb_capabilities` 必须报告当前配置档、已注册工具、隐藏工具类别和写策略。
6. 数据库升级只新增表和索引，不破坏现有 `knowledge_items`、`blocks` 和向量数据。
7. `config.example.yaml` 使用新默认值；实际用户配置不自动覆盖。

## 14. 安全与非功能要求

| ID | 要求 | 验收 |
| --- | --- | --- |
| SEC-01 | 新配置默认 MCP 只读 | `write_policy=disabled` |
| SEC-02 | HTTP 写操作默认关闭 | `allow_http_write=false` |
| SEC-03 | 索引路径受允许根目录约束 | 越权路径返回 `PERMISSION_DENIED` |
| QUA-01 | 默认工具数 8-12 | schema snapshot 验证 |
| QUA-02 | 统一引用字段 | search/ask contract tests |
| QUA-03 | 检索质量不回退 | 离线 eval threshold 通过 |
| QUA-04 | 目录变更可增量同步 | create/update/delete E2E 通过 |
| PER-01 | 无变更扫描快速结束 | 1000 文件 manifest diff 不重新解析 |
| PER-02 | watcher 事件合并 | 同一路径 debounce 窗口内只处理一次 |
| OPS-01 | 诊断可执行 | `shinehe doctor` 非零退出码表示失败 |

## 15. 分期与依赖

| 阶段 | 主题 | 依赖 | 可独立交付 |
| --- | --- | --- | --- |
| Phase 0 | 基线与契约冻结 | 无 | 是 |
| Phase 1 | CLI 与本地初始化 | Phase 0 | 是 |
| Phase 2 | Path Index + Watcher | Phase 1 | 是 |
| Phase 3 | MCP 工具配置档 | Phase 0、Phase 2 | 是 |
| Phase 4 | 检索候选与引用契约 | Phase 0 | 是 |
| Phase 5 | Eval 与质量门禁 | Phase 4 | 是 |
| Phase 6 | README、Demo、发布收口 | Phase 1-5 | 是 |

Phase 1 和 Phase 4 可并行；Phase 2 依赖 CLI 配置位置；Phase 3 在 `index_path` 服务可用后冻结默认核心工具清单；Phase 5 依赖统一候选和引用字段。

## 16. 验收定义

改造完成必须同时满足：

1. 新配置启动时只注册 8-12 个核心 MCP 工具。
2. `legacy` 配置下现有工具和别名仍可用。
3. `shinehe init --local --path <dir> --client <client>` 可生成配置和客户端 MCP 配置。
4. `shinehe index <dir>` 可索引目录并跳过未变化文件。
5. `shinehe watch <dir>` 能正确处理新增、修改和删除。
6. `search` 和 `ask` 返回统一 Citation，至少包含路径、Block、score、reason 和原文。
7. PDF/Excel/PPTX/Markdown/代码文件按可用元数据返回位置。
8. reranker 失败时自动降级且不丢失检索结果。
9. Eval fixture 包含有效 golden source，CI 能阻止明显质量回退。
10. README 第一屏不再以 51 个工具、Wiki、Graph 或插件为主卖点。
11. 默认安装和 Docker MCP 镜像不依赖 GUI、外部图数据库和 Web Client。
12. 所有新增行为有针对性测试；已有 MCP envelope、安全和导入测试继续通过。

## 17. 关键风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 工具精简破坏旧 Agent 配置 | 未设置 profile 的老配置走 legacy；保留 `shinehe-mcp` |
| 注册重构影响 FastMCP schema | 先建立 core/legacy schema snapshot，再改注册方式 |
| watcher 重复索引或误删 | hash + debounce + soft delete + operation log |
| 本地模型体验受机器性能影响 | local reranker 为可选 extra；doctor 输出明确诊断 |
| 引用位置元数据不完整 | 按格式渐进提供；缺失字段为 null，不伪造位置 |
| Eval 指标虚高 | 使用固定 fixture 和真实 golden block/location，不用“有 source 即正确” |
| 产品收束与现有高级用户冲突 | 代码保留，默认产品面和工具面分层，不删除能力 |

## 18. 最终判断

本次改造的核心不是继续叠加检索算法，而是把已有能力整理成一个清晰、短路径、可验证的产品：

- 对新用户：一个命令完成本地配置和 Agent 接入。
- 对 Agent：少量、稳定、语义明确的核心工具。
- 对检索结果：统一分数、命中原因和可追溯引用。
- 对长期使用：本地目录自动增量维护。
- 对发布质量：用真实 benchmark 证明“高精准”，而不是只写在 README 中。
