<div align="center">

# ShineHe Knowledge

**Local-First MCP Knowledge Retrieval Engine for AI Assistants**

[\[中文文档\]](README_zh.md)

[![Version](https://img.shields.io/badge/version-1.3.0-blue.svg)](https://github.com/anjingdtl/knowledge-base)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.10-3776AB.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-10%20core%20tools-orange.svg)](src/mcp/tool_profiles.py)

</div>

---

## What Is It

ShineHe Knowledge is a **local-first, privacy-focused MCP knowledge retrieval engine** that turns your documents into a high-precision search service for AI assistants like Claude, Cursor, and Cline.

- **Index your local documents** (PDF, DOCX, Markdown, Excel, code, etc.) into a SQLite-based vector + keyword search engine
- **Expose 10 core MCP tools** for AI agents to search, ask questions, and retrieve cited answers
- **Return structured citations** with document path, block ID, score breakdown, and match reasons
- **Incremental directory watching** automatically re-indexes changed files
- **All data stays local** (SQLite + sqlite-vec + FTS5). No cloud storage dependency.

## 30-Second Demo

```bash
# 1. Install
pip install -e ".[parsers]"

# 2. Initialize local configuration
shinehe init --local --path D:\docs --client claude-code

# 3. Index your documents
shinehe index D:\docs

# 4. Start MCP server (Claude Desktop / Cursor / Cline will connect automatically)
shinehe mcp --transport stdio
```

Your AI assistant can now call `search` or `ask` and receive answers with full citation trails:

```json
{
  "document": "architecture.md",
  "path": "D:/docs/architecture.md",
  "knowledge_id": "doc_001",
  "block_id": "doc_001_block_07",
  "location": {
    "heading_path": ["Architecture", "Storage"],
    "paragraph_index": 12
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
  "text": "SQLite uses WAL mode for local indexing."
}
```

## Supported Clients

- **Claude Desktop** — `mcp_config_templates/claude_desktop.json`
- **Cursor** — `mcp_config_templates/cursor.json`
- **Cline** — `mcp_config_templates/cline.json`
- **Continue** — `mcp_config_templates/continue.json`
- **Any MCP-compatible client** — stdio or HTTP/SSE transport

## Core Features

### High-Precision Retrieval
6-stage configurable RAG pipeline: Query Rewrite → Vector + FTS5 Hybrid Search → RRF Fusion → Rerank → Context Expansion → Citation Packaging.

### Structured Citations
Every search result includes document path, block ID, location (page/sheet/slide/heading/line), score breakdown by channel (vector/keyword/RRF/rerank), match reason, and original text.

### Incremental Directory Indexing
`shinehe watch D:\docs` monitors your documents and automatically re-indexes new, modified, or deleted files with debounce and hash-based diff.

### MCP Tool Profiles
Default `core` profile exposes 10 stable tools for AI agents. Advanced users can switch to `extended`, `admin`, `full`, or `legacy` profiles via `config.yaml`.

### Local Reranker (Optional)
Pluggable reranker providers: API-based, local cross-encoder (sentence-transformers), LLM fallback, or disabled. Falls back gracefully on failure.

### Eval & Quality Gates
Fixed fixture datasets with golden sources, baseline thresholds, and CI integration prove retrieval quality (Recall@5, MRR, nDCG@10, citation completeness).

## Quick Start

### Install

```bash
# MCP core mode (minimal dependencies)
pip install -e .

# With document parsers (PDF, DOCX, Excel, etc.)
pip install -e ".[parsers]"

# Full-featured mode (GUI + API + parsers + Wiki + Graph)
pip install -e ".[all]"
```

### Initialize

```bash
# Local-first setup with Ollama (recommended for privacy)
shinehe init --local --path D:\docs --client claude-code

# Or use cloud API endpoints (edit config.yaml manually)
shinehe init --path D:\docs --client cursor
```

`shinehe init --local` generates:
- Ollama embedding/LLM configuration (`http://localhost:11434/v1`)
- `mcp.tool_profile=core` (10 tools)
- `mcp.write_policy=disabled` (read-only by default)
- `rag.search_mode=blend` (vector + keyword)
- `rag.parent_child.enabled=true` (context expansion)

### Index & Watch

```bash
# Index a directory
shinehe index D:\docs

# Watch for incremental updates (Ctrl+C to stop)
shinehe watch D:\docs

# Diagnose configuration
shinehe doctor
```

### Launch MCP Server

```bash
# stdio mode (Claude Desktop / Cursor / Cline)
shinehe mcp --transport stdio

# HTTP mode (port 9000)
shinehe mcp --transport streamable-http --port 9000

# Legacy entry point (still works)
python run_mcp.py
```

## Core MCP Tools

The default `core` profile registers 10 tools optimized for AI agent retrieval:

| Tool | Purpose | Side Effect |
|------|---------|-------------|
| `ping` | Connectivity check | read |
| `kb_capabilities` | Query current profile, capabilities, limits | read |
| `search` | High-precision retrieval with structured citations | read |
| `ask` | Generate cited answers from retrieval results | read |
| `read` | Read original document or block content | read |
| `list_knowledge` | List indexed documents | read |
| `index_path` | Index file or directory (returns async job for large inputs) | write |
| `get_job` | Query indexing job status | read |
| `list_jobs` | List indexing jobs | read |
| `reindex_all` | Rebuild all indexes | write |

Advanced tools (Query DSL, source graph, CRUD, Wiki, Graph, Agent Memory) are available in `extended`, `admin`, `full`, and `legacy` profiles. See [docs/advanced-features.md](docs/advanced-features.md).

## Retrieval Quality

Retrieval quality is proven with fixed fixture datasets, golden sources, and CI gates:

- **Recall@5** — percentage of queries where the correct document appears in top 5 results
- **MRR** — mean reciprocal rank of the first correct hit
- **nDCG@10** — normalized discounted cumulative gain
- **Citation completeness** — percentage of citations with valid path, block ID, and location
- **No-answer accuracy** — correct rejection of unanswerable queries

Baseline thresholds are enforced in CI. See [docs/retrieval-quality.md](docs/retrieval-quality.md) and [evals/baselines/local.json](evals/baselines/local.json).

## Core vs Experimental

**Core (default):** MCP Server, local file indexing, hybrid search, RRF, rerank, context expansion, structured citations, directory watching, eval gates.

**Experimental (opt-in):** Wiki workflow, Graph traversal (Neo4j), Agent Memory, Plugin system, Web admin UI, multi-user RBAC.

Advanced features remain in the codebase but are hidden from the default MCP tool face. Enable them via `mcp.experimental_tools_enabled=true` in `config.yaml`. See [docs/advanced-features.md](docs/advanced-features.md).

## Graph Backend (SQLite / Neo4j)

Switch the knowledge base's graph storage backend from **Settings → Graph Backend** in the GUI:

- **SQLite (default)** — zero-config, with `GraphSyncHook` automatically mirroring create/delete operations into the graph view. Best for small-to-medium graphs.
- **Neo4j (optional)** — designed for large-scale relationship analysis and Cypher traversal. After picking Neo4j, click **Auto-Deploy Neo4j** to download and install Neo4j Community 5.x into `%LOCALAPPDATA%\Neo4j` (UAC is triggered on demand to set `NEO4J_HOME`), then use **Start Neo4j** and **Full Migration** to import existing data.

## Architecture

```
knowledge-base/
├── main.py / run_api.py / run_mcp.py   # GUI/API/MCP entry points → create_container()
├── config.yaml                          # Main configuration
├── src/
│   ├── core/container.py                # Dependency injection container
│   ├── mcp/tool_registry.py             # Declarative tool registration with profile filtering
│   ├── mcp/tool_profiles.py             # core/extended/admin/full/legacy tool sets
│   ├── mcp_server.py                    # FastMCP server (tool implementations, prompts, resources)
│   ├── cli.py                           # shinehe init/index/watch/doctor/mcp
│   ├── services/
│   │   ├── path_indexer.py              # Incremental directory indexing
│   │   ├── file_watcher.py              # watchdog-based directory monitoring
│   │   ├── hybrid_search.py             # Vector + keyword + RRF fusion
│   │   ├── search_service.py            # Unified search pipeline (MCP + API)
│   │   ├── rag_pipeline.py              # 6-stage configurable RAG
│   │   ├── citation_builder.py          # Structured citation with location metadata
│   │   ├── rerankers/                   # Pluggable reranker providers (API/local/LLM/disabled)
│   │   └── ...
│   ├── repositories/                    # Data access layer (indexed_files, knowledge_items, blocks)
│   └── models/                          # RetrievalCandidate, Citation, KnowledgeItem, Block
├── evals/                               # Retrieval quality fixtures, datasets, baselines
└── tests/                               # Contract, integration, and eval tests
```

## Documentation

- [Quick Start & Agent Usage](docs/mcp/agent-usage.md)
- [MCP Tool Profiles & Migration Guide](docs/migration/mcp-tool-profiles.md)
- [Advanced Features](docs/advanced-features.md)
- [Retrieval Quality & Eval Gates](docs/retrieval-quality.md)
- [Current Optimization Spec](docs/superpowers/specs/2026-06-13-mcp-local-retrieval-focus-design.md)
- [Module Implementation Plan](docs/superpowers/plans/2026-06-13-mcp-local-retrieval-focus.md)
- [Project Status](PROGRESS.md)

## Deployment

```bash
# Docker (MCP-only image)
docker build --target mcp -t shinehe-knowledge:mcp .
docker run -v ~/.shinehe/data:/data shinehe-knowledge:mcp

# Windows installer
python scripts/build_windows.py

# Windows Service (auto-start + crash recovery)
python windows_service.py install
python windows_service.py start
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+ / FastAPI / FastMCP |
| Vectors | sqlite-vec / bge-m3 (1024-dim) |
| Storage | SQLite + FTS5 / Alembic migrations |
| Reranker | sentence-transformers (optional) / API / LLM fallback |
| Frontend | React 19 / Vite / TypeScript (optional web client) |
| Build | PyInstaller + Inno Setup / Docker / Windows Service |

## License

[MIT License](LICENSE)
