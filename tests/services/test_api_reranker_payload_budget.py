"""Ensure remote reranking has a bounded payload without losing a safe tail."""
from __future__ import annotations

from src.services.provider_runtime import ProviderResponse
from src.services.rerankers.api import ApiReranker


class _Config:
    def get(self, key, default=None):
        return {
            "reranker.max_candidates": 2,
            "reranker.max_document_chars": 100,
            "rag.rerank.min_score": 0.0,
        }.get(key, default)


def test_api_reranker_bounds_remote_documents_and_keeps_unranked_tail(monkeypatch):
    captured = {}

    def fake_run(_operation, request, **_kwargs):
        captured["payload"] = request.payload
        return ProviderResponse(ok=True, data={"results": [
            {"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.8},
        ]})

    monkeypatch.setattr("src.services.rerankers.api.run_provider_operation", fake_run)
    reranker = ApiReranker("https://example.test/rerank", "model", "key", _Config())
    rows = [{"id": str(i), "text": "a" * 120} for i in range(4)]
    result = reranker.rerank("query", rows, top_n=3)
    assert captured["payload"]["documents"] == ["a" * 100, "a" * 100]
    assert captured["payload"]["top_n"] == 2
    assert [row["id"] for row in result] == ["1", "0", "2"]
    assert "rerank_score" not in result[2]
