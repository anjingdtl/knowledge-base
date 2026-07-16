"""Document-level retrieval hit deduplication."""
from __future__ import annotations

from typing import Any, Iterable, Sequence


def _score_of(item: dict[str, Any], score_keys: Sequence[str]) -> float:
    for key in score_keys:
        raw = item.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return 0.0


def dedupe_by_knowledge_id(
    items: Iterable[dict[str, Any]],
    *,
    score_keys: Sequence[str] = ("score", "fts_score", "similarity"),
) -> list[dict[str, Any]]:
    """Keep one hit per non-empty knowledge_id (highest score).

    Hits without knowledge_id are kept individually (cannot merge safely).
    Winner order follows first occurrence of each knowledge_id in the input.
    """
    rows = list(items)
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    orphan: list[dict[str, Any]] = []

    for item in rows:
        kid = str(item.get("knowledge_id") or item.get("page_id") or "").strip()
        if not kid:
            orphan.append(item)
            continue
        score = _score_of(item, score_keys)
        prev = best.get(kid)
        if prev is None:
            best[kid] = item
            order.append(kid)
            continue
        if score > _score_of(prev, score_keys):
            best[kid] = item

    return [best[k] for k in order] + orphan
