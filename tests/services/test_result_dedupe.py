"""Document-level hit dedupe for Precision@k."""
from __future__ import annotations

from src.services.result_dedupe import dedupe_by_knowledge_id


def test_keeps_highest_score_per_knowledge_id() -> None:
    items = [
        {"knowledge_id": "a", "score": 0.2, "text": "low"},
        {"knowledge_id": "a", "score": 0.9, "text": "high"},
        {"knowledge_id": "b", "score": 0.5, "text": "b"},
    ]
    out = dedupe_by_knowledge_id(items)
    assert [x["knowledge_id"] for x in out] == ["a", "b"]
    assert out[0]["text"] == "high"
    assert out[0]["score"] == 0.9


def test_empty_knowledge_id_kept_separately() -> None:
    items = [
        {"knowledge_id": "", "score": 0.1, "block_id": "b1"},
        {"knowledge_id": "", "score": 0.2, "block_id": "b2"},
    ]
    out = dedupe_by_knowledge_id(items)
    assert len(out) == 2


def test_preserves_first_seen_order_of_winners() -> None:
    items = [
        {"knowledge_id": "c", "score": 0.4},
        {"knowledge_id": "a", "score": 0.3},
        {"knowledge_id": "c", "score": 0.8},
        {"knowledge_id": "b", "score": 0.5},
    ]
    out = dedupe_by_knowledge_id(items)
    assert [x["knowledge_id"] for x in out] == ["c", "a", "b"]
    assert out[0]["score"] == 0.8
