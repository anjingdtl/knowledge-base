# Structured/Graph RAG Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first RAG-quality foundation: structured Block import, Small-to-Big context, link expansion, rules-based query routing, and answer source graphs.

**Architecture:** Keep SQLite/sqlite-vec and the existing FileGraph/RAG pipeline. Add small services around the current Block-first model rather than introducing a new database or replacing existing MCP/API contracts.

**Tech Stack:** Python 3, SQLite, sqlite-vec, FastMCP, PyQt, pytest

**Status:** Completed on 2026-06-04. Verification: `pytest tests -q` -> 162 passed, 1 third-party warning.

---

## File Structure

**Create:**

- `src/services/link_discovery.py` - scan `[[Page]]` and `[[Page#Block]]` into `entity_refs`
- `src/services/query_router.py` - route obvious logic queries to SQL-backed block search
- `src/services/source_graph.py` - build `{nodes, edges}` payloads from RAG sources
- `tests/test_structured_graph_rag_phase1.py` - phase acceptance tests

**Modify:**

- `src/services/block_context.py` - promote helper functions to `BlockContextService`
- `src/services/hybrid_search.py` - preserve block ids through vector, keyword, and blend paths
- `src/services/file_graph.py` - preserve structured `parent_id` after legacy chunk insertion and run link discovery after sync
- `src/services/markdown_outline.py` - parse indented `- key:: value` lines as blocks, not properties
- `src/services/rag_pipeline.py` - use `QueryRouter` and return `source_graph`
- `src/mcp_server.py` and `src/gui/import_dialog.py` - prefer `ParsedFile.structured` over flat text on import
- `src/api/routes.py` - expose `source_graph` from `/chat/ask`
- `config.yaml` - add context sibling/link expansion defaults

---

### Task 1: Structured Import

- [x] Add a failing MCP ingest test where `parse_file()` returns `ParsedFile.structured`; assert parent/child rows are preserved in `blocks`.
- [x] Fix `_do_ingest_file()` to define `container = _get_container()` once and pass `parsed.structured if parsed.structured else parsed.content` to `FileGraphService.create_page()`.
- [x] Apply the same structured-first rule to GUI file import.
- [x] Reorder `FileGraphService._rebuild_page_cache()` so `insert_chunks()` runs before the final structured `insert_blocks()` write.
- [x] Verify `pytest tests/test_structured_graph_rag_phase1.py::test_mcp_ingest_file_uses_structured_blocks_when_parser_provides_them -q`.

### Task 2: Search Identity and Small-to-Big Context

- [x] Add a failing keyword-search test proving the returned hit keeps `id == block_id` and includes parent plus sibling context.
- [x] Preserve `id` and `metadata.block_id` in `_vector_search()`, `_keyword_search()`, and `_blend_search()`.
- [x] Replace the old parent-only helper with `BlockContextService.build_context()` that assembles parent chain, current block, sibling window, and linked summaries.
- [x] Keep `get_block_context()` and `enrich_result_with_context()` as compatibility wrappers.
- [x] Verify the focused context test.

### Task 3: Link Discovery and Expansion

- [x] Add a failing test for `LinkDiscoveryService.discover_links()` creating block-level `entity_refs`.
- [x] Implement exact-title page matching and optional `#Block Content` target matching.
- [x] Call link discovery after `FileGraphService.sync_page()` rebuilds blocks.
- [x] Ensure `BlockContextService` expands linked Page/Block summaries through `entity_refs`.

### Task 4: Query Router

- [x] Add a failing test where `#bug ::status unresolved [[前端重构]]` returns a block without calling hybrid search.
- [x] Implement `QueryRouter.route()` for tags, properties, wiki links, and strong logic words.
- [x] Implement SQL-backed block search across `knowledge_items.tags`, `block_property_index`, and `entity_refs`.
- [x] Wire `QueryRouter` into `VectorSearchStage` before hybrid search.

### Task 5: Source Graph Payload

- [x] Add a failing test proving `RAGService.query()` returns `source_graph`.
- [x] Implement `build_source_graph()` from sources, block ancestors, containing pages, and block entity refs.
- [x] Return `source_graph` from `RagPipeline.execute()` and add a compatibility fallback in `RAGService.query()`.
- [x] Expose `source_graph` from API `/chat/ask`; MCP `ask` passes it through automatically.

## Test Plan

- `pytest tests/test_structured_graph_rag_phase1.py -q`
- `pytest tests/test_file_graph.py tests/test_search.py tests/test_rag_messages.py -q`
- `pytest tests/test_mcp_server.py tests/test_api.py tests/test_search_service.py -q`
- Full suite if targeted tests pass: `pytest tests -q`

## Assumptions

- No external vector database or graph database in phase 1.
- Full JSON DSL and LLM Agentic Router remain phase 3 work.
- MCP `dry_run`, operation logs, and undo/redo move to an independent operation-safety plan.
