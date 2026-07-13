"""Conflict disclosure helpers for verified hybrid answers (Phase 4).

Spec §7.7: when related claims conflict, do not emit a single definitive
conclusion; disclose both sides with evidence and set conflict_disclosed.
"""
from __future__ import annotations

import re
from typing import Any, Sequence

_NUM_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>%|gbps|mbps|kbps|ghz|mhz|ms|s|m|km|g|kg|v|a|w|kw)?",
    re.IGNORECASE,
)
_NEG_MARKERS = ("不", "非", "无", "未", "禁止", "不得", "无法", "不能", "否")
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) >= 2}


def _extract_numbers(text: str) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for m in _NUM_RE.finditer(text or ""):
        try:
            num = float(m.group("num"))
        except (TypeError, ValueError):
            continue
        unit = (m.group("unit") or "").lower()
        out.append((num, unit))
    return out


def _has_negation(text: str) -> bool:
    t = text or ""
    return any(m in t for m in _NEG_MARKERS)


def _statements_overlap(a: str, b: str, *, min_overlap: int = 2) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) >= min_overlap


def claims_conflict(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any] | None:
    """Return a conflict descriptor if two claim result rows conflict, else None.

    Heuristics (deterministic, no LLM):
    - lexical subject overlap AND conflicting numeric values
      (same unit, both unitless, or different units → still disclose)
    - lexical subject overlap AND opposite negation polarity
    """
    sa = str(a.get("text") or a.get("statement") or "")
    sb = str(b.get("text") or b.get("statement") or "")
    if not sa or not sb:
        return None
    if sa.strip() == sb.strip():
        return None

    nums_a, nums_b = _extract_numbers(sa), _extract_numbers(sb)
    has_numeric_signal = bool(nums_a and nums_b)
    # Require less overlap when numeric values already differ (short statements)
    min_ov = 1 if has_numeric_signal else 2
    if not _statements_overlap(sa, sb, min_overlap=min_ov):
        return None

    reasons: list[str] = []
    if nums_a and nums_b:
        for na, ua in nums_a:
            for nb, ub in nums_b:
                if abs(na - nb) <= 1e-9:
                    continue
                if ua and ub and ua != ub:
                    reasons.append("numeric_unit_mismatch")
                else:
                    reasons.append("numeric_mismatch")
                break
            if reasons:
                break

    neg_a, neg_b = _has_negation(sa), _has_negation(sb)
    if neg_a != neg_b:
        reasons.append("polarity_mismatch")

    if not reasons:
        return None

    return {
        "claim_a_id": a.get("claim_id") or a.get("candidate_id"),
        "claim_b_id": b.get("claim_id") or b.get("candidate_id"),
        "statement_a": sa,
        "statement_b": sb,
        "reason_codes": reasons,
        "evidence_a": list(a.get("evidence") or []),
        "evidence_b": list(b.get("evidence") or []),
        "status_a": a.get("status"),
        "status_b": b.get("status"),
    }


def detect_claim_conflicts(
    claim_rows: Sequence[dict[str, Any]],
    *,
    max_pairs: int = 20,
) -> list[dict[str, Any]]:
    """Pairwise conflict scan over claim-like result rows."""
    claims = [
        c for c in claim_rows
        if (c.get("candidate_type") == "claim" or c.get("source") == "verified_claim"
            or c.get("claim_id"))
        and (c.get("text") or c.get("statement"))
    ]
    conflicts: list[dict[str, Any]] = []
    n = len(claims)
    for i in range(n):
        for j in range(i + 1, n):
            hit = claims_conflict(claims[i], claims[j])
            if hit:
                conflicts.append(hit)
                if len(conflicts) >= max_pairs:
                    return conflicts
    return conflicts


def is_freshness_sensitive_query(query: str) -> bool:
    q = query or ""
    markers = ("当前", "最新", "现行", "现在", "截至", "最新版", "现行版", "目前")
    return any(m in q for m in markers)


def filter_stale_claims(
    claim_rows: Sequence[dict[str, Any]],
    *,
    drop_stale: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split claims into kept vs excluded-for-stale."""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for c in claim_rows:
        freshness = str(c.get("freshness") or "current")
        evidence = c.get("evidence") or []
        any_stale = freshness.startswith("stale") or any(
            e.get("stale") for e in evidence if isinstance(e, dict)
        )
        if drop_stale and any_stale:
            dropped.append(c)
        else:
            kept.append(c)
    return kept, dropped
