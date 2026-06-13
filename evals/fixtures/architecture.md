# System Architecture

## Storage Layer

The knowledge base uses SQLite with WAL mode as the primary database. Full-text search is powered by FTS5, providing BM25-ranked keyword search across all indexed content.

Vector embeddings are stored using sqlite-vec, supporting efficient approximate nearest neighbor search. The default embedding model is BAAI/bge-m3, producing 1024-dimensional vectors.

## Search Pipeline

The hybrid search pipeline combines vector similarity and keyword matching through Reciprocal Rank Fusion (RRF). The default fusion constant k=60 balances precision and recall.

Query rewriting generates multiple search variants using the configured LLM, improving recall for complex or ambiguous queries.

## Configuration

Default configuration is stored in config.yaml. The system supports multiple AI providers including Ollama for local deployment, SiliconFlow, OpenAI, and others.

Reranking improves precision by re-scoring top candidates using either a dedicated rerank API or LLM-based scoring as fallback.
