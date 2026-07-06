# Advanced Features

> These features remain in the codebase but are **hidden from the default MCP tool face**.
> Enable them via `mcp.experimental_tools_enabled=true` or by switching to a non-core profile.

## MCP Tool Profiles

| Profile | Tools | Use Case |
|---------|-------|----------|
| `core` | 10 core tools | Minimal surface for AI agents |
| `extended` | core + Query DSL + source graph + job management (**default**) | General AI agents |
| `admin` | extended + CRUD + audit/undo | Local human maintenance |
| `full` | all non-experimental tools | Power users |
| `legacy` | all tools + namespaced aliases | Backward compatibility |

Configure in `config.yaml`:

```yaml
mcp:
  tool_profile: extended    # core | extended | admin | full | legacy
  enable_legacy_aliases: false
  experimental_tools_enabled: false
```

See [docs/migration/mcp-tool-profiles.md](migration/mcp-tool-profiles.md) for migration from v1.2.

## Extended Tools

Available in `extended` profile and above:

- `search_fulltext` — FTS5-only full-text search
- `tags` — tag queries
- `route_query` — agentic routing analysis
- `execute_query` — explicit QuerySpec execution
- `structured_query` — DSL conditional queries
- `explain_query` — execution plan explanation
- `ask_with_query` — controllable Q&A with QuerySpec
- `get_source_graph` — RAG evidence chain tracing
- `create_ingest_job` — async ingest job creation
- `cancel_job` — job cancellation

## Admin Tools

Available in `admin` profile and above:

- `create` / `update` / `delete` / `restore_knowledge` — full CRUD lifecycle
- `ingest_url` — URL ingestion
- `preview_operation` — dry-run write preview
- `get_operation_log` / `undo_operation` — audit and rollback
- `list_recent_operations` / `query_operation_logs` — operation log queries

## Experimental Features

Gated behind `mcp.experimental_tools_enabled=true`:

### Wiki Workflow

Full content lifecycle: draft → review → published → deprecated.

- `wiki_lint` — dead link detection
- `fix_dead_references` — LLM-powered dead link repair
- `wiki_submit_review` / `wiki_approve` / `wiki_reject` / `wiki_deprecate` — workflow transitions
- `wiki_workflow_history` / `wiki_list_versions` / `wiki_restore_version` — version management
- `save_to_wiki` — publish knowledge to Wiki

### Knowledge Graph

- `graph_traverse` — multi-hop graph traversal
- SQLite graph storage — Page, Block, Tag, entity reference, and semantic relation traversal with zero external service dependency

### Agent Memory

- `remember_fact` / `recall_facts` — persistent fact storage
- `update_project_context` — project-level context management
- `search_decisions` — decision history search
- `summarize_recent_changes` — change summary
- `extract_tasks_from_doc` — task extraction from documents

### Plugin System

Hook-based event-driven architecture. Plugins fire on knowledge create/delete/update.

### Web Admin UI

React 19 + Vite + TypeScript frontend at `client/`.

```bash
cd client && npm install && npm run dev
```

### Multi-User RBAC

JWT-based authentication for REST API. See `src/api/auth.py`.

## Runtime Modes

Beyond the default MCP stdio/HTTP server:

- **Desktop GUI** — `python main.py` (PySide6, dark sci-fi theme)
- **REST API** — `python run_api.py` (FastAPI, port 8000, JWT auth)
- **Windows Service** — `python windows_service.py install/start` (auto-start + crash recovery)
- **Docker** — `docker build --target mcp -t shinehe-knowledge:mcp .`

## Structured Query DSL

Available in `extended` profile:

```json
{
  "mode": "structured",
  "filters": {
    "tags": {"$contains": "architecture"},
    "created_at": {"$gte": "2026-01-01"}
  },
  "sort": {"field": "score", "order": "desc"},
  "limit": 10
}
```

## Source Graph Tracing

`get_source_graph` returns the evidence chain behind a RAG answer, showing how blocks link to documents and how scores were composed.

## 规模自适应路由(Size-Aware Router)

`mode=wiki_first` 时,查询先经 `SizeAwareRouter` 按规模三档分流,补齐「小规模用
index / 大规模用搜索」原则:

- **wiki_read**(小):查询 token ≤ `rag.size_aware.small_query_max_tokens`(默认 12)
  且 `index.md` 命中 wiki 页 ≤ `small_wiki_page_threshold`(默认 3)→ 仅读 wiki 页,
  **零向量调用**。
- **full_search**(大):含意图词(哪些/所有/对比/全部/列举)或 wiki 无命中 → 向量 +
  lexical + parent-child 全量搜索。
- **blend**(中间):wiki 先行 + 搜索补充,RRF 融合两路。

规则层零 LLM 成本;`rag.size_aware.llm_fallback` 默认关闭。`mode=legacy` 时
SizeAwareRouter 不介入,检索行为与 v1.4.0 一致。路由准确率可经
`python evals/run_retrieval_eval.py --routing` 量化。

## Wiki Parent-Child 上下文

wiki 检索命中 `entities`/`concepts`/`syntheses`/`comparisons` 页时,`WikiParentRetriever`
按页类型取溯源键回查其引用的 source 页摘要(≤ `rag.wiki_parent_child.wiki_parent_context_max_length`,
默认 2000),作为 `parent_context` 注入候选,使 wiki 页回答更完整。复用 block 检索的
`parent_context` 字段语义与 CitationBuilder 渲染路径。

## 中文 lexical 强化(lexical_zh)

keyword 通道(FTS5 + jieba)三项强化,提升中文召回(`retrieval_zh` Recall@5 基线 0.6):

- **专名分词**:`rag.lexical_zh.dict_path`(默认 `data/lexical_zh_dict.txt`,
  `shinehe init` 生成空模板)→ jieba 用户词典,专名(如「创智杯」)不再被错切。
- **同义词扩展**:`rag.lexical_zh.synonym_path` → query 改写时并集进 FTS5。
- **语种权重**:RRF keyword 权重按语种拆分 `rrf_weight_keyword_zh`(默认 0.7)/
  `rrf_weight_keyword_en`(默认 0.5)。

字典/同义词加载失败仅 warning 不阻塞检索。词典只对**新写入**的 block 生效;存量数据
需 `shinehe index --reindex` 重建 FTS 才能享受专名分词。检索引擎可通过
`python evals/run_retrieval_eval.py --engine real-hybrid` 走真实 HybridSearcher 验证
lexical 通道(对比默认 `--engine offline` 的 BM25 基线)。
