# Troubleshooting FAQ

## Why is search returning no results?

Check that the embedding service is running. If using Ollama, verify that the embedding model is downloaded: `ollama pull nomic-embed-text`.

Also check that FTS5 is enabled in your SQLite build. Run `python -c "import sqlite3; print(sqlite3.fts5)"` to verify.

## How to improve search quality?

Enable reranking by setting `reranker.enabled: true` in config.yaml. For best results, use a dedicated rerank model like BAAI/bge-reranker-v2-m3.

Increase `rag.top_k` to retrieve more candidates before reranking filters them.

## Index is corrupted, what to do?

Run `shinehe doctor` to diagnose issues. If the database is corrupted, use `reindex_all` to rebuild from source files.
