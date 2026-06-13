---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '326076e1-9135-4ee2-a42b-717e45b37cee'
  PropagateID: '326076e1-9135-4ee2-a42b-717e45b37cee'
  ReservedCode1: 'd378b48f-d73f-4e6f-9832-9bc9ef613052'
  ReservedCode2: 'd378b48f-d73f-4e6f-9832-9bc9ef613052'
---

<div align="center">

# ShineHe Knowledge

**Local-First AI Knowledge Base — RAG Q&A + MCP Toolchain + Knowledge Graph**

[\[中文文档\]](README_zh.md)

[![Version](https://img.shields.io/badge/version-1.2.0-blue.svg)](https://github.com/anjingdtl/knowledge-base)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.10-3776AB.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-51%20tools-orange.svg)](src/mcp_server.py)

</div>

---

## What Is It

ShineHe Knowledge is a **locally-running, privacy-first** knowledge base system:

- Feed your documents in, ask questions in natural language, AI retrieves + generates answers
- Native MCP Server with 51 original tools and 51 namespaced aliases, directly callable from Claude / Cursor / Cline and other AI tools
- Built-in knowledge graph (SQLite + Neo4j dual backend), Wiki workflow, hybrid search engine
- Plugin architecture with hook-based extensibility and swappable graph database backends

All data stays local (SQLite + sqlite-vec). No cloud storage dependency.

## Key Features

### RAG Intelligent Q&A
6-stage configurable pipeline: Query Rewriting → Wiki Retrieval → Hybrid Search → Reranking → Generation → Post-processing.  
Supports both Agentic Router (auto-routing) and DSL (precise query) modes.

### Hybrid Search Engine
Vector search (bge-m3 1024-dim) + keyword search (FTS5) + RRF fusion, with Chinese segmentation optimization (jieba).

### Knowledge Graph (Dual Backend)
- **SQLite backend** (default): zero dependency, queries knowledge tables directly
- **Neo4j backend** (optional): Cypher queries, batch UNWIND, efficient multi-hop traversal
- Pluggable backend interface + data migration + incremental sync + event-driven sync hooks
- File-first outline graph, multi-hop traversal, structured DSL queries, Agentic Router

### MCP Server
51 original tools + 51 namespaced aliases + 3 resources + 5 prompts, covering search, Q&A, CRUD, ingestion, Wiki, graph, query, operations, and agent memory.
Write safety closed loop with `preview_operation` (dry-run) + `undo_operation` (rollback).

### Wiki System
Full workflow (draft → review → published → deprecated), version snapshots & restore, LLM-powered dead link repair, knowledge health check.

### Operation Safety
Preview before write (dry_run), full audit log, undo any operation — agents can never "accidentally" break things.

### Multi-Modal Document Parsing
PDF / DOCX / TXT / Markdown / HTML / Excel / images / code files. Large files automatically processed asynchronously.

### Plugin System
Hook-based event-driven architecture — plugin callbacks fire on knowledge create/delete/update. Extend with a single registration.

### Four Runtime Modes
Desktop GUI (PySide6) / REST API (FastAPI) / MCP Server (stdio + HTTP) / Windows Service, all sharing the same service layer.

## Quick Start

### Install

```bash
# MCP core mode (minimal dependencies)
pip install -e .

# Full-featured mode (GUI + API + parsers + Wiki + Graph)
pip install -e ".[all]"

# Neo4j graph backend only
pip install -e ".[graph]"
```

### Configure

Edit `config.yaml` with your LLM / Embedding API settings (any OpenAI-compatible endpoint):

```yaml
embedding:
  base_url: https://api.siliconflow.cn/v1
  model: BAAI/bge-m3

llm:
  base_url: https://api.minimaxi.com/v1
  model: MiniMax-M3

# Optional: Neo4j graph backend
graph_backend:
  provider: neo4j          # default: sqlite
  uri: bolt://localhost:7687
  user: neo4j
  password: your_password
  database: neo4j
```

### Launch

```bash
# Desktop GUI
python main.py

# REST API (port 8000)
python run_api.py

# MCP Server (stdio mode)
python run_mcp.py

# MCP Server (HTTP mode, port 9000)
shinehe-mcp -t streamable-http --port 9000

# Windows Service (auto-start on boot + crash recovery)
python windows_service.py install
python windows_service.py start
```

### Web Client

```bash
cd client
npm install
npm run dev      # Dev server (port 5173)
npm run build    # Production build
```

## MCP Tools Overview

| Category | Tools | Description |
|----------|-------|-------------|
| **Connection** | `ping` | Connectivity check, <10ms response |
| **Search** | `search` / `search_fulltext` | Semantic search / Full-text search (FTS5) |
| **Q&A** | `ask` / `ask_with_query` | RAG Q&A / Controllable Q&A with explicit QuerySpec |
| **CRUD** | `create` / `read` / `update` / `delete` / `restore_knowledge` | Full lifecycle management (including soft-delete restore) |
| **Ingest** | `ingest_file` / `ingest_url` | File / URL ingestion, large files auto-async |
| **Async Jobs** | `create_ingest_job` / `get_job` / `list_jobs` / `cancel_job` | Ingest async job management |
| **General Async** | `create_async_job` / `get_async_job` / `list_async_jobs` / `cancel_async_job` | General async task framework |
| **Index** | `reindex_all` | Full index rebuild |
| **Tags** | `tags` / `list_knowledge` | Tag and knowledge list queries |
| **Structured Query** | `structured_query` / `explain_query` | DSL conditional queries / Execution plan explanation |
| **Graph** | `graph_traverse` / `get_source_graph` | Multi-hop traversal / RAG evidence chain tracing |
| **Smart Routing** | `route_query` / `execute_query` | Agentic routing analysis / Explicit QuerySpec execution |
| **Wiki** | `wiki_lint` / `fix_dead_references` / `wiki_submit_review` / `wiki_approve` / `wiki_reject` / `wiki_deprecate` / `wiki_workflow_history` / `wiki_list_versions` / `wiki_restore_version` / `save_to_wiki` | Full Wiki workflow + dead link repair + version management |
| **Operations** | `kb_capabilities` / `query_operation_logs` / `get_operation_log` / `undo_operation` / `preview_operation` / `list_recent_operations` | Capability query / Audit log / Undo / Preview |

## Architecture

```
knowledge-base/
├── main.py / run_api.py / run_mcp.py   # Four entry points → create_container() init
├── config.yaml                          # Main configuration
├── windows_service.py                   # Windows Service (auto-start + crash recovery)
├── client/                              # React 19 + Vite + TypeScript frontend
├── src/
│   ├── core/container.py                # Dependency injection container
│   ├── api/                             # FastAPI REST API (JWT auth)
│   ├── services/                        # Core service layer
│   │   ├── rag_pipeline.py              # 6-stage RAG pipeline
│   │   ├── hybrid_search.py             # Hybrid search (vector + keyword + RRF)
│   │   ├── vectorstore.py               # sqlite-vec vector store
│   │   ├── block_store.py               # Block-level vector store
│   │   ├── unified_graph.py             # Unified knowledge graph (backend-agnostic)
│   │   ├── graph_backend/               # 🔌 Pluggable graph database backends
│   │   │   ├── base.py                  #   Abstract interface + data classes
│   │   │   ├── factory.py               #   Backend factory
│   │   │   ├── sqlite_backend.py        #   SQLite backend (default)
│   │   │   ├── neo4j_backend.py         #   Neo4j backend (Cypher queries)
│   │   │   ├── migration.py             #   SQLite → Neo4j data migration
│   │   │   └── sync_hooks.py            #   Event-driven incremental sync
│   │   ├── neo4j_manager.py             # Neo4j process management (auto start/stop)
│   │   ├── wiki_*.py                    # Wiki workflow system
│   │   └── ...                          # More services
│   ├── mcp_server.py                    # FastMCP Server (51 tools + 51 aliases)
│   ├── plugins/                         # 🔌 Plugin hook system
│   ├── gui/                             # PySide6 desktop UI
│   │   ├── wiki_view.py                 #   Wiki management (health check / dead link fix / workflow)
│   │   ├── graph_view.py               #   Graph visualization (force-directed / dual backend)
│   │   ├── settings_dialog.py           #   Settings (7 tabs incl. service management)
│   │   └── ...                          #   More views
│   └── repositories/                    # Data access layer
└── tests/                               # Test suite
```

## One-Click AI Tool Integration

`mcp_config_templates/` provides ready-to-use JSON configs for popular AI coding tools:

- Claude Desktop
- Cursor
- Cline
- Continue
- Other MCP-compatible clients

## Deployment

```bash
# Docker
docker compose up -d

# Windows installer
python scripts/build_windows.py

# Windows Service (auto-start + crash recovery)
python windows_service.py install
sc failure ShineHeMCP reset= 86400 actions= restart/5000/restart/10000/restart/30000
python windows_service.py start
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+ / FastAPI / FastMCP / PySide6 |
| Vectors | sqlite-vec / bge-m3 (1024-dim) |
| Storage | SQLite + FTS5 / Alembic migrations |
| Graph | SQLite (default) / Neo4j (optional, Cypher queries) |
| Frontend | React 19 / Vite / TypeScript / Tailwind CSS |
| Build | PyInstaller + Inno Setup / Docker / Windows Service |

## License

[MIT License](LICENSE)
