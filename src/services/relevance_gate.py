"""Unified relevance / no-answer gate for semantic, FTS, and hybrid hits.

FTS keyword hits alone are not sufficient evidence to answer a question.
"""
from __future__ import annotations

import re
from typing import Any

from src.services.numeric_unit_match import extract_number_units, score_numeric_unit_match

_CURRENT_INFO_RE = re.compile(
    r"(今天|今日|当前|现在|最新|实时|股价|行情|此刻|刚刚)",
)
_CJK_TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_LATIN_TERM_RE = re.compile(r"[A-Za-z0-9]{2,}")


def is_current_information_query(query: str) -> bool:
    return bool(_CURRENT_INFO_RE.search(query or ""))


def extract_query_terms(query: str) -> set[str]:
    q = query or ""
    terms = set(_CJK_TERM_RE.findall(q))
    terms |= {t.lower() for t in _LATIN_TERM_RE.findall(q)}
    # Drop ultra-generic terms that alone never justify an answer
    stop = {
        "多少",
        "什么",
        "哪些",
        "怎么",
        "如何",
        "是否",
        "可以",
        "相关",
        "问题",
        "今天",
        "今日",
        "当前",
        "现在",
        "最新",
        "实时",
    }
    return {t for t in terms if t not in stop}


def _candidate_text(item: dict) -> str:
    parts = [
        str(item.get("title") or ""),
        str(item.get("text") or item.get("content") or item.get("summary") or ""),
        str(item.get("chunk_text") or ""),
    ]
    return "\n".join(parts)


def score_candidate_relevance(query: str, item: dict) -> dict[str, Any]:
    """Compute multi-signal relevance features and a final score in [0, 1]."""
    text = _candidate_text(item)
    title = str(item.get("title") or "")
    terms = extract_query_terms(query)
    text_l = text.lower()
    title_l = title.lower()

    covered = 0
    for t in terms:
        if t.lower() in text_l or t.lower() in title_l:
            covered += 1
    query_term_coverage = (covered / len(terms)) if terms else 0.0

    # Phrase-ish: consecutive bigrams of CJK terms present
    cjk_terms = [t for t in _CJK_TERM_RE.findall(query or "") if t not in ("多少", "什么")]
    phrase_hits = 0
    phrase_total = max(0, len(cjk_terms) - 1)
    for i in range(phrase_total):
        phrase = cjk_terms[i] + cjk_terms[i + 1]
        if phrase in text or phrase in title:
            phrase_hits += 1
    phrase_coverage = (phrase_hits / phrase_total) if phrase_total else query_term_coverage

    title_hits = sum(1 for t in terms if t.lower() in title_l)
    title_score = (title_hits / len(terms)) if terms else 0.0

    semantic_score = float(
        item.get("score")
        or item.get("similarity")
        or item.get("semantic_score")
        or 0.0
    )
    fts_score = float(item.get("fts_score") or item.get("fts_rank") or 0.0)
    if fts_score > 1.0:
        # raw FTS ranks are often large negative/positive; clamp via existing field if present
        fts_score = min(1.0, abs(fts_score) / 20.0)

    nu = score_numeric_unit_match(query, text)
    features = nu.get("features") or {}
    numeric_unit_score = 0.0
    if features.get("exact_number_unit_match"):
        numeric_unit_score = 1.0
    elif features.get("number_match_unit_mismatch"):
        numeric_unit_score = 0.0
    elif extract_number_units(query):
        numeric_unit_score = 0.15
    else:
        numeric_unit_score = 0.5  # N/A — neutral

    freshness_score = 0.5
    if is_current_information_query(query):
        freshness_score = 0.1  # local corpus is not live market data

    # Weighted blend — keyword-only FTS cannot dominate weak partial hits,
    # but full query-term coverage is strong lexical evidence.
    final = (
        0.25 * min(1.0, max(0.0, semantic_score))
        + 0.15 * min(1.0, max(0.0, fts_score))
        + 0.15 * title_score
        + 0.25 * query_term_coverage
        + 0.10 * phrase_coverage
        + 0.05 * numeric_unit_score
        + 0.05 * freshness_score
    )
    # Strong lexical evidence: nearly all query terms appear in the candidate.
    if query_term_coverage >= 0.8:
        final = max(final, 0.40 + 0.25 * query_term_coverage)
    if query_term_coverage >= 1.0 and not extract_number_units(query):
        final = max(final, 0.55)
    if phrase_coverage >= 0.5 and query_term_coverage >= 0.5:
        final = max(final, 0.45)

    # Hard penalties
    if features.get("number_match_unit_mismatch"):
        final *= 0.35
    if extract_number_units(query) and not features.get("exact_number_unit_match"):
        # Numeric question without exact unit match is weak evidence
        final = min(final, 0.34)
    if is_current_information_query(query):
        final = min(final, 0.25)
    # Single generic term overlap (e.g. only "营收") is not enough for a
    # specific numeric/entity question with many unused terms.
    if terms and query_term_coverage < 0.4 and semantic_score < 0.5:
        final = min(final, 0.30)

    final = max(0.0, min(1.0, final))
    return {
        "semantic_score": round(semantic_score, 4),
        "fts_score": round(fts_score, 4),
        "title_score": round(title_score, 4),
        "numeric_unit_score": round(numeric_unit_score, 4),
        "phrase_coverage": round(phrase_coverage, 4),
        "query_term_coverage": round(query_term_coverage, 4),
        "freshness_score": round(freshness_score, 4),
        "final_relevance_score": round(final, 4),
        "features": features,
    }


def apply_relevance_scores(query: str, items: list[dict]) -> list[dict]:
    for item in items:
        scores = score_candidate_relevance(query, item)
        item["relevance"] = scores
        item["final_relevance_score"] = scores["final_relevance_score"]
        # Expose blended score for downstream thresholds without clobbering
        # a higher semantic score when already present.
        if "score" not in item or item.get("score") is None:
            item["score"] = scores["final_relevance_score"]
    items.sort(
        key=lambda x: float(x.get("final_relevance_score") or x.get("score") or 0.0),
        reverse=True,
    )
    return items


def evaluate_evidence(
    query: str,
    items: list[dict],
    *,
    threshold: float = 0.35,
) -> dict[str, Any]:
    """Return gate decision for a candidate list."""
    if is_current_information_query(query):
        # Local KB cannot answer live / "today" questions unless evidence is
        # extremely strong and explicitly fresh — default to no-answer.
        return {
            "accept": False,
            "no_match": True,
            "reason": "requires_current_external_data",
            "top_score": 0.0,
            "threshold": threshold,
            "items": [],
        }

    if not items:
        return {
            "accept": False,
            "no_match": True,
            "reason": "no_candidates",
            "top_score": 0.0,
            "threshold": threshold,
            "items": [],
        }

    ranked = apply_relevance_scores(query, list(items))
    top = float(ranked[0].get("final_relevance_score") or 0.0)
    accepted = [r for r in ranked if float(r.get("final_relevance_score") or 0.0) >= threshold]

    if not accepted or top < threshold:
        return {
            "accept": False,
            "no_match": True,
            "reason": "insufficient_relevant_evidence",
            "top_score": round(top, 4),
            "threshold": threshold,
            "items": [],
        }

    return {
        "accept": True,
        "no_match": False,
        "reason": None,
        "top_score": round(top, 4),
        "threshold": threshold,
        "items": accepted,
    }
