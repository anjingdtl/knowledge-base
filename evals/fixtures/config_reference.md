# Configuration Reference

## embedding section

- base_url: URL of the embedding API endpoint
- model: Name of the embedding model (e.g., nomic-embed-text for Ollama)
- provider: Provider identifier (ollama, siliconflow, openai)
- timeout: Request timeout in seconds (default: 30)

## rag section

- search_mode: blend (hybrid), embedding (vector only), or keywords (FTS only)
- top_k: Maximum number of results to return (default: 8)
- score_threshold: Minimum score to include a result (default: 0.35)
- enable_query_rewriting: Use LLM to generate query variants (default: true)
- enable_rerank: Apply reranking to search results (default: true)

## mcp section

- tool_profile: Controls which tools are exposed (core, extended, admin, full, legacy)
- write_policy: Controls write operations (disabled, preview_only, interactive, token_required)
