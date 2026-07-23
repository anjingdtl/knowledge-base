<div align="center">

# ShineHe Knowledge

**Local-First, Verifiable MCP Knowledge Engine for AI Agents**

[\[中文文档\]](README_zh.md)

[![Version](https://img.shields.io/badge/version-1.11.0-blue.svg)](https://github.com/anjingdtl/knowledge-base)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.10-3776AB.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-verified%20hybrid-orange.svg)](src/mcp/tool_profiles.py)

</div>

---

## What Is It

```text
Local documents
→ Raw Evidence Index
→ Verified Canonical Knowledge
→ MCP Search / Ask / Read
→ Traceable Answer
```

ShineHe Knowledge is a **local-first knowledge retrieval engine for AI agents**.  
**Raw documents and blocks are the final evidence.** Verified Wiki V2 claims enhance answers only when eligible. A maintenance control plane protects serving quality without becoming a second fact store.

- **Index local documents** (PDF, DOCX, Markdown, Excel, code, …) into SQLite + FTS5 + sqlite-vec
- **Default `verified` mode**: read gated claims + raw retrieval; agent writes off
- **Unified hybrid search / ask** with claim + evidence citations and conflict disclosure
- **Maintenance center**: protective automation (R1), drafts for review (R3), human for R4
- **All data stays local**. No cloud storage dependency.

## Interface Policy

The **PySide6 desktop GUI** (`python main.py`) is the primary daily-use interface and the only UI receiving active product and visual maintenance.

The React Web UI in `client/` remains available only as a **backup interface** for limited local/API access. It is temporarily not maintained: do not add features, redesign it, or treat it as the release-quality administrative interface unless a new explicit product decision reactivates it.

## Current Health

**v1.10.5** — Auditable evaluation foundations and truthful provider/routing execution:

- **Auditable Ground Truth workflow** — rule-assisted candidates are separated from primary/secondary human review and strict frozen publication
- **Freeze safety** — formal evaluation reads only `frozen/`; unreviewed or disputed samples cannot enter Ground Truth
- **Truthful Routing Harness** — preserves Agent-recommended arguments exactly and separates empty, timeout, validation, transport, and task completion outcomes
- **Production Provider isolation** — non-streaming LLM, Embedding, API/local Reranker calls use bounded, terminable process workers
- **Honest release status** — the 196 candidates still require real double review; frozen Ground Truth remains empty, so independent full acceptance is not allowed
- No Schema / Alembic change vs v1.10.4; formal `data/kb.db` remained unchanged

See [v1.10.5 Release Notes](docs/release/v1.10.5-release-notes.md), [three-foundation-fix report](docs/reports/production-pilot-foundation-three-fixes-2026-07-16.md), [PROGRESS](PROGRESS.md), and earlier notes ([v1.10.4](docs/release/v1.10.4-release-notes.md) · [v1.10.3](docs/release/v1.10.3-release-notes.md) · [v1.10.2](docs/release/v1.10.2-release-notes.md)).

## 30-Second Demo

```bash
# 1. Install
pip install -e ".[parsers]"

# 2. Initialize (default: verified mode — raw + verified wiki read, no agent writes)
shinehe init --local --path D:\docs --client claude-code
# authoring: shinehe init --mode authoring --local --path D:\knowledge

# 3. Index your documents
shinehe index D:\docs

# 4. Start MCP server (Claude Desktop / Cursor / Cline will connect automatically)
shinehe mcp --transport stdio
```

Your AI assistant can call `kb_capabilities`, then `search` / `ask` / `read`, and receive answers with claim + evidence citation trails:

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

### Verified Hybrid Retrieval
Query routing → parallel verified claims + raw hybrid (vector/FTS/RRF) → Serving Gate → conflict/freshness checks → unified ranking. Wiki failure never blocks raw answers.

### Structured Citations
Claim citations link to original evidence blocks. Every raw hit includes path, block ID, location (page/sheet/slide/heading/line), score breakdown, and match reason.

### Three Operating Modes
`verified` (default) · `authoring` (explicit maintenance) · `evidence_only` (raw-only / ablation). Legacy `wiki_first` / `legacy` map at runtime without rewriting your config file.

### Incremental Directory Indexing
`shinehe watch D:\docs` monitors your documents and automatically re-indexes new, modified, or deleted files with debounce and hash-based diff.

### MCP Tool Profiles
Default `extended` profile exposes 20 tools (10 core read tools + Query DSL, source graph, async ingest). Switch to `core`, `admin`, `full`, or `legacy` via `config.yaml`.

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
- `mcp.tool_profile=extended` (20 tools: 10 core read tools + Query DSL / source graph / async ingest)
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

The default `extended` profile registers 20 tools: the 10 core retrieval tools listed below, plus Query DSL, source graph, and async ingest tooling. The 10 core tools are always exposed and optimized for AI agent retrieval (switch to `core` if you only want these):

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

Advanced tools (CRUD, Wiki, Graph, Agent Memory) ship in `admin`, `full`, and `legacy` profiles. See [docs/advanced-features.md](docs/advanced-features.md).

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

**Experimental (opt-in):** Wiki workflow, SQLite graph traversal, Agent Memory, Plugin system, Web admin UI, multi-user RBAC.

Advanced features remain in the codebase but are hidden from the default MCP tool face. Enable them via `mcp.experimental_tools_enabled=true` in `config.yaml`. See [docs/advanced-features.md](docs/advanced-features.md).

## SQLite Graph Storage

Graph data is stored in the local SQLite database through Page, Block, Tag, entity reference, and semantic relation tables. No external graph database is required. Unified graph views, source graphs, and multi-hop traversal are built from `data/kb.db`.

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
