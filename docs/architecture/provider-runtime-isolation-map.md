# Provider runtime isolation map

This map is the production wiring inventory for the three-foundation fix.  A
`process` entry means the parent constructs a minimal serializable
`ProviderRequest`; the child resolves credentials by environment/keyring name,
performs one call, and returns only `ProviderResponse`.

| Operation | Module / function | Provider | Sync model | Network timeout | Isolation | Production call path | Coverage / reason |
|---|---|---|---|---|---|---|---|
| LLM generation | `src.services.llm.LLMService._run_generate` | OpenAI-compatible LLM | non-cooperative sync SDK | SDK request timeout plus parent deadline clamp | `process` | `AnswerService -> Generator -> LLMService.chat_with_usage -> run_provider_operation` and all other non-streaming `LLMService.chat*` callers | `test_llm_process_isolation_wiring.py`; a stuck SDK/native stack is terminable |
| LLM streaming | `src.services.llm.LLMService.chat_stream` | OpenAI-compatible streaming LLM | cooperative iterator with request timeout | explicit request timeout | `thread_cooperative` | RAG streaming entry points | Explicit constant and call timeout; a stream cannot be serialized across the one-shot response boundary |
| Embedding | `src.services.embedding.EmbeddingService.embed_batch._embed_one` | OpenAI-compatible embedding | non-cooperative sync SDK | SDK request timeout plus parent deadline clamp | `process` | indexing, query embedding, Wiki matching -> `EmbeddingService` -> `run_provider_operation` | `test_embedding_timeout_cleanup.py`; child owns and releases its HTTP client |
| API reranker | `src.services.rerankers.api.ApiReranker.rerank` | SiliconFlow/Cohere-compatible HTTP | non-cooperative sync HTTP | connect/read/total timeout plus parent deadline clamp | `process` | `SearchService -> ApiReranker -> run_provider_operation` | `test_reranker_timeout_cleanup.py`; timeout is re-raised, not converted to success |
| Local reranker | `src.services.rerankers.local.LocalCrossEncoderReranker.rerank` | sentence-transformers CrossEncoder | native/model inference may be non-cooperative | parent wall deadline | `process` | `SearchService -> LocalCrossEncoderReranker -> run_provider_operation` | same runtime; model instance, DB, and Container stay out of the request |
| LLM fallback reranker | `src.services.rerankers.llm.LLMFallbackReranker._score_batch_llm` | configured LLM | delegates to central LLM service | central LLM timeout | `process` via LLM service | `SearchService -> LLMFallbackReranker -> LLMService.chat` | covered by central LLM wiring |
| OCR | none in the production source tree | not configured | not applicable | not applicable | not enabled | no production call path found | NOT TESTED because the project has no OCR provider implementation or enabled OCR path |
| URL fetch | `src.services.file_parser.parse_url` | HTTP web source | cooperative `httpx` | explicit timeout, per-hop SSRF validation, bounded redirects | `thread_cooperative` | URL ingestion only | Existing HTTP client closes on return/error; not part of LLM/Embedding/Reranker one-shot provider runtime |
| Setup connectivity probe | `src.gui.setup_wizard._ConnectivityWorker.run` | embedding endpoint | cooperative QThread diagnostic | explicit 8-second SDK timeout | `thread_cooperative` | pre-save GUI connectivity test, not a service Provider path | Kept separate because the user-entered secret has not yet been persisted for child lookup |

Runtime safety invariants:

- Multiprocessing uses `spawn`.
- No `AppContainer`, SQLite/DB connection, HTTP session, logger handler, model
  instance, or plaintext API key is present in `ProviderRequest`.
- The child resolves only the named secret (`secret_env_key`) and redacts it
  from any returned error.
- Timeout performs terminate + join, then kill + join if necessary; the queue
  is closed and its feeder thread joined.
- Worker concurrency is bounded (default 8), circuit state is observable, and
  `kb_health_check` reports `provider_isolation` without secrets.
- Full prompts are not written to default logs or runtime diagnostics.
