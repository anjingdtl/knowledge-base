# Provider Cancellation Matrix

Production-pilot final validation (Phase 4).

| Provider | Call style | Cancellation class | Mechanism | `background_work_may_continue` |
|----------|------------|--------------------|-----------|--------------------------------|
| LLM (MiniMax / OpenAI-compatible HTTP) | sync HTTP | sync cooperative | connect/read/total timeouts via `Deadline.provider_timeout` + cancel event | false when socket closes |
| Embedding (SiliconFlow / bge-m3 HTTP) | sync HTTP batch | sync cooperative | per-batch HTTP timeouts + deadline clamp | false when socket closes |
| Reranker (bge-reranker HTTP) | sync HTTP | sync cooperative | HTTP timeouts | false when socket closes |
| URL fetch / ingest | sync HTTP | sync cooperative | SSRF-safe fetch + timeouts | false |
| OCR (if enabled) | plugin | sync non-cooperative* | `run_in_terminable_process` | **false** (process kill) |
| Non-cooperative sync SDK sleep/hang | sync | sync non-cooperative | `run_in_terminable_process` / `run_with_deadline(..., isolate="process")` | **false** |
| Async MCP / FastMCP handlers | async | async cancellable | `asyncio.wait_for` + task cancel | false |

\* OCR not in default production path; isolation API ready.

## Process isolation contract

```text
spawn worker process
pass only picklable minimal args (no DB conn, no API key blob in shared mem, no Container)
timeout → terminate → join → confirm exit
cleanup IPC queue
max_provider_workers = 8
max_abandoned_workers target = 0 for process mode
circuit_breaker_failure_threshold = 20
circuit_breaker_cooldown = 30s
```

## API

- `src.services.deadline.run_in_terminable_process`
- `src.services.deadline.run_with_deadline(..., isolate="process")`
- Thread isolate remains for cooperative callables only (legacy honest `background_work_may_continue=true` if hang)

## Gate

- Process-mode timeout: `cancelled=true`, `background_work_may_continue=false`
- 50 consecutive timeouts: no abandoned worker growth
- Service continues to accept ping/search after timeouts
