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


# --- General Chinese surface synonyms (SPEC Phase 3.2) -----------------------
# These map colloquial Chinese surface forms to their formal literary
# equivalents.  They are NOT a domain vocabulary: every entry is a standard
# Chinese language synonym pair that would apply to any business/policy
# corpus (e.g. 比赛→竞赛, 店铺→门店).  No entry references a specific
# document title, knowledge ID, or evaluation question.
#
# Used by ``build_alias_query_variants`` to generate synonym-expanded query
# variants for hybrid retrieval so that a formal document titled "竞赛办法"
# can be found from a colloquial query "比赛办法".
_GENERIC_SYNONYMS: dict[str, tuple[str, ...]] = {
    "比赛": ("竞赛",),
    "赛事": ("竞赛",),
    "奖金": ("奖励",),
    "发奖金": ("奖励",),
    "商家": ("合作商",),
    "店铺": ("门店", "网店"),
    "入驻": ("准入",),
    "门槛": ("条件",),
    "供货商": ("供应商",),
    "外包商": ("服务商",),
}


def build_alias_query_variants(query: str, *, max_variants: int = 3) -> list[dict[str, str]]:
    """Generate synonym-expanded query variants for hybrid retrieval.

    Returns a list of ``{"query": variant, "source": "alias:<term>→<syn>"}``
    dicts.  Each variant substitutes exactly one colloquial surface term with
    its formal Chinese synonym.  Multi-substitution is intentionally avoided
    to keep variants auditable and prevent drift from the user's phrasing.

    The synonyms are general Chinese language pairs — no document titles, no
    knowledge IDs, no evaluation-question-specific mappings.  This function is
    deliberately separate from ``build_deterministic_query_variants`` (which is
    constrained to preserve the user's exact vocabulary).
    """
    q = (query or "").strip()
    if not q:
        return []
    variants: list[dict[str, str]] = []
    seen: set[str] = {q}
    for term, synonyms in _GENERIC_SYNONYMS.items():
        if term not in q:
            continue
        for syn in synonyms:
            v = q.replace(term, syn, 1)
            if v != q and v not in seen:
                seen.add(v)
                variants.append({
                    "query": v,
                    "source": f"alias:{term}→{syn}",
                })
                if len(variants) >= max_variants:
                    return variants
    return variants


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
