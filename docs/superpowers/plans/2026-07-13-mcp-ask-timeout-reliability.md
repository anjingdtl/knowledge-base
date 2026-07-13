# MCP ASK Timeout Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee that a blocked RAG dependency yields a structured MCP ASK timeout response within the configured deadline.

**Architecture:** Keep the public `ask` envelope and provider-specific timeouts unchanged. Run the pipeline behind an isolated, bounded daemon-thread bridge with one monotonic deadline, so a blocking dependency cannot hold an MCP request open or create unbounded background work.

**Tech Stack:** Python 3.14, asyncio, threading, pytest, FastMCP.

---

### Task 1: Add the failing deadline regression tests

**Files:**
- Modify: `tests/test_mcp_stability.py`

- [x] **Step 1: Write the failing test**

```python
def test_rag_query_enforces_timeout_when_pipeline_blocks_sync(monkeypatch):
    from src.services.rag_pipeline import RAGService

    class BlockingPipeline:
        async def execute(self, question, conversation_history=None):
            time.sleep(0.2)
            return {"answer": "late", "sources": [], "source_graph": {}}

    service = RAGService(deps={})
    service._pipeline = BlockingPipeline()

    with pytest.raises(concurrent.futures.TimeoutError):
        service.query("blocked", timeout=0.02, skip_cache=True)
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_stability.py::test_rag_query_enforces_timeout_when_pipeline_blocks_sync -q`

Expected: FAIL because the current no-running-loop bridge blocks for about 0.2 seconds and returns `"late"` instead of raising `TimeoutError`.

- [x] **Step 3: Add the MCP timeout-payload regression test**

```python
def test_do_ask_returns_timeout_payload_when_rag_times_out(monkeypatch):
    from types import SimpleNamespace
    from src import mcp_server as mcp_mod

    def raise_timeout(*args, **kwargs):
        raise concurrent.futures.TimeoutError()

    monkeypatch.setattr(
        mcp_mod,
        "_get_container",
        lambda: SimpleNamespace(rag_pipeline=SimpleNamespace(query=raise_timeout)),
    )
    result = mcp_mod._do_ask("blocked")
    assert result["route"]["mode"] == "timeout"
    assert result["answer"] == ""
```

- [x] **Step 4: Run the test to verify the current boundary behavior**

Run: `pytest tests/test_mcp_stability.py -q`

Expected: The new direct-RAG deadline test fails; the timeout-payload test passes because `_do_ask` already maps `TimeoutError` to a structured result.

### Task 2: Enforce the deadline outside the pipeline event loop

**Files:**
- Modify: `src/services/rag_pipeline.py:41-85`
- Test: `tests/test_mcp_stability.py`

- [x] **Step 1: Replace the no-running-loop path in `_run_coroutine_sync`**

```python
result_queue: queue.Queue[tuple[bool, object]] = queue.Queue()

def _runner():
    try:
        result_queue.put((True, asyncio.run(coro)))
    except BaseException as exc:
        result_queue.put((False, exc))

thread = threading.Thread(target=_runner, name="RAGPipelineAsyncBridge", daemon=True)
thread.start()
thread.join(timeout=timeout)
if thread.is_alive():
    raise concurrent.futures.TimeoutError()
success, result = result_queue.get_nowait()
```

- [x] **Step 2: Run the targeted regressions**

Run: `pytest tests/test_mcp_stability.py -q`

Expected: PASS; the blocking pipeline raises `TimeoutError` at approximately 0.02 seconds and existing bridge behavior remains covered.

- [x] **Step 3: Run MCP contract coverage**

Run: `pytest tests/test_mcp_stability.py tests/test_mcp_contract.py tests/test_mcp_cli.py -q`

Expected: PASS with no MCP envelope or CLI regressions.

### Task 3: Verify the deployed transport behavior

**Files:**
- No source changes expected.

- [x] **Step 1: Start the Streamable HTTP MCP server on an unused localhost port**

Run: `python run_mcp.py --transport streamable-http --host 127.0.0.1 --port 9011`

- [x] **Step 2: Run JSON-RPC initialize, `ask`, then `ping` against `/mcp`**

Expected: `ask` returns a valid MCP tool result and the following `ping` returns successfully on the same session.

- [x] **Step 3: Check the worktree and commit the implementation**

Run: `git status --short; git add src/services/rag_pipeline.py tests/test_mcp_stability.py docs/superpowers/plans/2026-07-13-mcp-ask-timeout-reliability.md; git commit -m "fix: enforce MCP ask timeout boundary"`

Expected: Only the timeout bridge, regression tests, and plan are included.
