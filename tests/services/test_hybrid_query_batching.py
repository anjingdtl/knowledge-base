"""Regression coverage for generic multi-query vector batching."""
from __future__ import annotations

from src.services.hybrid_search import HybridSearcher
from src.retrieval.raw_retriever import deterministic_fallback_rank


class _Config:
    def get(self, _key, default=None):
        return default


class _PassageStore:
    def vector_search(self, query, *, top_k, query_embedding):
        return [{
            "id": f"p-{query}", "passage_id": f"p-{query}",
            "knowledge_id": f"k-{query}", "text": query,
            "distance": 0.1, "metadata": {},
        }]


def test_passage_vector_search_embeds_query_variants_in_one_batch(monkeypatch):
    calls: list[list[str]] = []

    def fake_embed_batch(self, texts):
        calls.append(list(texts))
        return [[float(index)] for index, _ in enumerate(texts)]

    monkeypatch.setattr(
        "src.services.embedding.EmbeddingService.embed_batch_with_cache", fake_embed_batch,
    )
    searcher = HybridSearcher(config=_Config(), passage_store=_PassageStore())
    rows, warnings = searcher._passage_vector_search(["原问法", "保留实体的变体"], 5)
    assert calls == [["原问法", "保留实体的变体"]]
    assert warnings == []
    assert [row["id"] for row in rows] == ["p-原问法", "p-保留实体的变体"]


def test_deterministic_fallback_uses_query_evidence_and_explicit_recency():
    rows = [
        {"id": "old", "knowledge_id": "old", "title": "旧版赛事管理规则", "text": "一级赛事分类", "rrf_score": 0.09, "version_year": 2021},
        {"id": "new", "knowledge_id": "new", "title": "赛事管理规则修订", "text": "新版管理要求", "rrf_score": 0.08, "version_year": 2025},
    ]
    ranked = deterministic_fallback_rank("赛事管理规则最新修订版", rows, top_k=2)
    assert [row["id"] for row in ranked] == ["new", "old"]
    assert ranked[0]["fallback_score_breakdown"]["recency"] > 0
