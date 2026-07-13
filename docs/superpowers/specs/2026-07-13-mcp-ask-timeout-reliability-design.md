# MCP ASK Timeout Reliability Design

**Goal:** Ensure a stalled RAG dependency cannot leave an MCP `ask` request without a response or make later tool calls appear unavailable.

**Context:** `RAGService.query()` runs an async pipeline through `_run_coroutine_sync()`. The pipeline's async stages invoke synchronous embedding, reranking, and LLM clients directly. While one of those calls is blocking, its event loop cannot process the `asyncio.wait_for()` timeout. The configured `rag.ask.total_timeout` therefore is not an enforceable request deadline.

**Chosen approach:** Run the entire coroutine in an isolated daemon thread for both caller contexts, and wait for its result only up to the configured deadline. The MCP tool then converts the resulting `TimeoutError` to its existing structured timeout envelope. This makes the deadline independent of blocking dependency calls. Existing per-provider HTTP timeouts remain the mechanism for eventually releasing the isolated worker.

**Alternatives considered:**

- Rely only on provider HTTP timeouts: does not bound the sum of retrieval, rerank, and generation work, and remains dependent on provider behavior.
- Convert every pipeline dependency to async: larger, cross-cutting change that is not necessary to make the MCP boundary reliable.

**Behavior:**

- A normal `ask` response is unchanged.
- A blocked pipeline raises `concurrent.futures.TimeoutError` by the configured deadline even when called without an existing event loop.
- `ask` returns its current `route.mode=timeout` structured result instead of leaving the MCP request open.
- A regression test uses a blocking async stage to prove the original no-running-loop path returns within the deadline; a second assertion verifies the MCP `ask` timeout envelope.

**Files:**

- `src/services/rag_pipeline.py` — enforce the synchronous RAG deadline at the thread boundary.
- `tests/test_mcp_stability.py` — regression coverage for a blocked pipeline and the MCP timeout envelope.
