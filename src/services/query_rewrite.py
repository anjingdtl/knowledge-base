"""Generic, surface-preserving query variants for compatibility callers.

This module deliberately does not translate colloquial evaluation questions
into document titles or policy facts.  Semantic expansion belongs to the
retrieval index/model; lexical variants retain only text supplied by the user.
"""
from __future__ import annotations

import re
from typing import Any


def _terms(query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        import jieba
        raw = [token.strip() for token in jieba.lcut(q)]
    except Exception:  # pragma: no cover - minimal environments
        raw = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9_-]*", q)
    stop = {"什么", "多少", "怎么", "如何", "是否", "有没有", "请问", "一下", "公司"}
    seen: set[str] = set()
    return [
        token for token in raw
        if len(token) >= 2 and token not in stop and not (token in seen or seen.add(token))
    ]


def expand_query(query: str) -> list[str]:
    """Return original text plus a bounded term-only surface variant."""
    q = (query or "").strip()
    if not q:
        return []
    variants = [q]
    terms = _terms(q)
    surface = " ".join(terms[:8])
    if surface and surface != q:
        variants.append(surface)
    return variants


def canonical_terms(query: str) -> list[str]:
    """Compatibility name: terms are query-derived, never canonical facts."""
    return _terms(query)[:8]


def merge_candidates_by_query(base_query: str, candidate_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge candidate lists while preserving raw passage diversity."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    orphans: list[dict[str, Any]] = []
    for rows in candidate_lists:
        for item in rows:
            if not isinstance(item, dict):
                continue
            key = str(item.get("passage_id") or item.get("id") or item.get("knowledge_id") or "").strip()
            if not key:
                orphans.append(item)
                continue
            if key not in merged:
                merged[key] = item
                order.append(key)
            else:
                old = merged[key]
                if _score(item) > _score(old):
                    merged[key] = item
    return [merged[key] for key in order] + orphans


def _score(item: dict[str, Any]) -> float:
    for key in ("final_relevance_score", "rrf_score", "final_score", "score", "fts_score"):
        try:
            if item.get(key) is not None:
                return float(item[key])
        except (TypeError, ValueError):
            pass
    return 0.0
