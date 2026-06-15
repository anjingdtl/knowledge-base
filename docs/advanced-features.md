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
- Neo4j backend (optional) — Cypher queries, batch UNWIND, efficient traversal
- SQLite backend (default) — zero dependency

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
