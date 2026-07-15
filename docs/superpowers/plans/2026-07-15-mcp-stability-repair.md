# MCP Stability Report Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the five MCP parameter-contract failures found by the 2026-07-15 stability test without changing retrieval semantics.

**Architecture:** Treat the MCP tool signature, docstring and `_define_tool` description as one public contract. Add compatibility only where it has a clear, bounded meaning (`tags` pagination and document-id task extraction); remove the unimplemented `hybrid` promise from `execute_query`.

**Tech Stack:** Python 3.12, FastMCP, pytest, Ruff, Mypy, SQLite.

---

## File Structure

- Modify: `src/mcp/tools/retrieval.py` — B1/B5 public descriptions and validation text.
- Modify: `src/mcp/tools/graph.py` — B2 public description and JSON validation feedback.
- Modify: `src/mcp/tools/ingest.py` — B3 pagination contract.
- Modify: `src/mcp/tools/memory.py` — B4 bounded `doc_id` compatibility.
- Create: `tests/test_mcp_stability_report_repair.py` — direct public-contract regressions.
- Create: `docs/mcp-stability-repair-2026-07-15.md` — operator-facing repair record and P95/URL diagnostic runbook.

### Task 1: Lock down B1/B2/B5 public contracts

**Files:** `tests/test_mcp_stability_report_repair.py`, `src/mcp/tools/retrieval.py`, `src/mcp/tools/graph.py`

- [ ] Write failing tests proving that `hybrid` is rejected without being advertised, malformed `start_ids` returns a JSON example, and the `ask_with_query` description states the required input.
- [ ] Run `pytest tests/test_mcp_stability_report_repair.py -k "hybrid or graph_traverse or ask_with_query" -q`; expect RED because the current error still lists `hybrid` and the graph error lacks a recovery example.
- [ ] Change `execute_query` descriptions/errors to `structured / graph`; add `start_ids='["knowledge-id"]'` plus `limit` to `graph_traverse`; map `JSONDecodeError` to `VALIDATION_ERROR`; state `question` or `search_query` is required.
- [ ] Re-run the targeted test and require GREEN.
- [ ] Commit: `fix(mcp): align query and graph contracts`.

### Task 2: Add B3 tags pagination

**Files:** `tests/test_mcp_stability_report_repair.py`, `src/mcp/tools/ingest.py`

- [ ] Write a failing test that inserts `alpha`, `beta`, `gamma`, calls `tags(limit=2, offset=1)`, and expects `beta`, `gamma` plus `count`, `limit`, `offset`, `next_offset`, `truncated` metadata.
- [ ] Run `pytest tests/test_mcp_stability_report_repair.py -k tags -q`; expect RED because the current function rejects keyword arguments.
- [ ] Add optional non-negative `limit` and `offset`; preserve no-argument behavior; return predictable slice and pagination metadata.
- [ ] Re-run the targeted test and require GREEN.
- [ ] Commit: `fix(mcp): paginate tag listing`.

### Task 3: Add B4 document-id task extraction compatibility

**Files:** `tests/test_mcp_stability_report_repair.py`, `src/mcp/tools/memory.py`

- [ ] Write failing tests showing `extract_tasks_from_doc(doc_id=<existing id>)` extracts a marked action item and that missing/both source arguments return `VALIDATION_ERROR`.
- [ ] Run `pytest tests/test_mcp_stability_report_repair.py -k extract_tasks -q`; expect RED because the existing function requires positional `content` and rejects `doc_id`.
- [ ] Accept exactly one of `content` and `doc_id`; use `get_knowledge(doc_id)` only; return `NOT_FOUND` for an absent item; pass resolved text into the existing memory service.
- [ ] Re-run the targeted test and require GREEN.
- [ ] Commit: `fix(mcp): support task extraction by document id`.

### Task 4: Publish repair record and verify release gates

**Files:** `docs/mcp-stability-repair-2026-07-15.md`

- [ ] Document B1–B5 root causes, decisions, tests and production diagnosis for LLM P95 / failed URLs (`shinehe doctor`, `get_job`, trace inspection).
- [ ] Run `pytest tests/test_mcp_stability_report_repair.py -q`, `pytest tests/test_mcp_contract.py tests/test_mcp_server.py tests/test_mcp_stability.py -q`, `ruff check src tests`, and `mypy src`; require exit 0 for each.
- [ ] Commit: `docs: record MCP stability repair`.
