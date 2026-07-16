"""Document-level retrieval hit deduplication and light re-ranking."""
from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

_CJK = re.compile(r"[\u4e00-\u9fff]{2,}")
_LATIN = re.compile(r"[A-Za-z0-9]{2,}")


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


def _query_terms(query: str) -> set[str]:
    terms: set[str] = set()
    for run in _CJK.findall(query or ""):
        if len(run) <= 4:
            terms.add(run)
        else:
            for n in (4, 3, 2):
                for i in range(len(run) - n + 1):
                    terms.add(run[i : i + n])
    terms |= {t.lower() for t in _LATIN.findall(query or "")}
    stop = {"什么", "哪些", "怎么", "如何", "所有", "全部", "主题", "内容", "关于"}
    return {t for t in terms if t not in stop and len(t) >= 2}


def boost_title_term_overlap(
    query: str,
    items: list[dict[str, Any]],
    *,
    weight: float = 0.12,
) -> list[dict[str, Any]]:
    """Boost items whose title shares multi-char query terms (Precision aid)."""
    terms = _query_terms(query)
    if not terms or not items:
        return items
    for item in items:
        title = str(item.get("title") or "")
        if not title:
            continue
        hits = sum(1 for t in terms if t in title)
        if hits <= 0:
            continue
        base = _score_of(item, ("score", "fts_score", "similarity"))
        item["score"] = min(1.0, base + weight * min(hits, 4))
        if "fts_score" in item:
            item["fts_score"] = item["score"]
    items.sort(
        key=lambda x: _score_of(x, ("score", "fts_score", "similarity")),
        reverse=True,
    )
    return items
