"""Real embedding retrieval index for offline fixture eval.

Indexes evals/fixtures with the live EmbeddingService (e.g. bge-m3), then
ranks by cosine similarity. Used for non-CI real-model retrieval measurement.
"""
from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _split_markdown(content: str) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []
    for line in content.split("\n"):
        if line.startswith("#"):
            if current_lines:
                chunks.append({
                    "heading": current_heading,
                    "text": "\n".join(current_lines).strip(),
                })
            current_heading = line.lstrip("#").strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        chunks.append({
            "heading": current_heading,
            "text": "\n".join(current_lines).strip(),
        })
    return [c for c in chunks if c["text"].strip()]


def _split_python(content: str) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    current_lines: list[str] = []
    current_name = ""
    for line in content.split("\n"):
        if line.startswith("def ") or line.startswith("class "):
            if current_lines:
                chunks.append({
                    "heading": current_name,
                    "text": "\n".join(current_lines).strip(),
                })
            m = re.match(r"(def|class)\s+(\w+)", line)
            current_name = m.group(2) if m else line.strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        chunks.append({
            "heading": current_name,
            "text": "\n".join(current_lines).strip(),
        })
    return [c for c in chunks if c["text"].strip()]


class RealEmbeddingIndex:
    """Fixture index powered by real EmbeddingService cosine search."""

    def __init__(self, embedding: Any):
        self._embedding = embedding
        self.documents: list[dict[str, Any]] = []

    def index_fixture(self, path: Path, content: str) -> None:
        if path.suffix in (".md", ".markdown"):
            chunks = _split_markdown(content)
        elif path.suffix == ".py":
            chunks = _split_python(content)
        else:
            chunks = [{"heading": path.stem, "text": content}]
        for i, chunk in enumerate(chunks):
            self.documents.append({
                "source_path": path.name,
                "title": path.name,
                "heading": chunk.get("heading", ""),
                "text": chunk["text"],
                "chunk_index": i,
                "vector": None,
            })

    def build_vectors(self, *, batch_size: int = 16) -> None:
        texts = [d["text"][:4000] for d in self.documents]
        if not texts:
            return
        logger.info("Embedding %d fixture chunks with real model…", len(texts))
        vectors = self._embedding.embed_batch(texts, batch_size=batch_size)
        if len(vectors) != len(self.documents):
            raise RuntimeError(
                f"embedding count mismatch: {len(vectors)} != {len(self.documents)}",
            )
        for doc, vec in zip(self.documents, vectors):
            doc["vector"] = vec

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        if not self.documents:
            return []
        if any(d.get("vector") is None for d in self.documents):
            self.build_vectors()
        qv = self._embedding.embed(query[:4000])
        scored: list[tuple[float, dict]] = []
        for doc in self.documents:
            score = _cosine(qv, doc["vector"] or [])
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, doc in scored[:top_k]:
            block_id = f"{doc['source_path']}:{doc['chunk_index']}"
            heading_path = [doc["heading"]] if doc["heading"] else []
            results.append({
                "source_path": doc["source_path"],
                "title": doc["title"],
                "text": doc["text"],
                "heading": doc["heading"],
                "score": score,
                "metadata": {
                    "source_path": doc["source_path"],
                    "chunk_index": doc["chunk_index"],
                    "knowledge_id": doc["source_path"],
                    "block_id": block_id,
                },
                "citation": {
                    "document": doc["title"],
                    "path": doc["source_path"],
                    "knowledge_id": doc["source_path"],
                    "block_id": block_id,
                    "location": {
                        "heading_path": heading_path,
                        "paragraph_index": doc["chunk_index"],
                    },
                    "score": score,
                    "score_breakdown": {
                        "vector": score,
                        "keyword": None,
                        "rrf": None,
                        "rerank": None,
                    },
                    "match_channels": ["vector"],
                    "reason": "real embedding cosine",
                    "text": doc["text"],
                },
            })
        return results
