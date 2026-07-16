"""Numeric + unit phrase matching for retrieval ranking.

Used to prefer exact number+unit hits (e.g. ``60米``) over unit-mismatch
confusers (e.g. ``60珠/米``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Compound density units first (珠/米) so plain 米 does not swallow them.
_NUM_UNIT_RE = re.compile(
    r"(?P<number>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>"
    r"珠/米|珠／米|"
    r"%|％|米|厘米|毫米|公里|千米|秒|分钟|小时|天|周|月|个月|年|"
    r"户|人|个|次|倍|珠|元|万元|亿|kg|g|m|cm|mm|km|s|ms|w|kw"
    r")",
    re.IGNORECASE,
)

# Context phrase: capture short surrounding words after number+unit
_CONTEXT_WINDOW = 6


@dataclass(frozen=True)
class NumberUnitHit:
    number: str
    unit: str
    phrase: str


def extract_number_units(text: str) -> list[NumberUnitHit]:
    if not text:
        return []
    hits: list[NumberUnitHit] = []
    for m in _NUM_UNIT_RE.finditer(text):
        number = m.group("number")
        unit = m.group("unit")
        phrase = f"{number}{unit}"
        hits.append(NumberUnitHit(number=number, unit=unit, phrase=phrase))
    return hits


def _normalize_unit(unit: str) -> str:
    u = (unit or "").lower().strip().replace("／", "/")
    aliases = {
        "％": "%",
        "个月": "月",
        "千米": "公里",
        "km": "公里",
        "m": "米",
        "cm": "厘米",
        "mm": "毫米",
        "s": "秒",
        "ms": "毫秒",
        "珠／米": "珠/米",
    }
    return aliases.get(u, u)


def score_numeric_unit_match(query: str, candidate_text: str) -> dict:
    """Return feature flags + additive score adjustment for ranking.

    Features:
      - exact_number_unit_match
      - number_match_unit_mismatch
      - context_phrase_match
    """
    q_hits = extract_number_units(query)
    c_hits = extract_number_units(candidate_text or "")
    features = {
        "exact_number_unit_match": False,
        "number_match_unit_mismatch": False,
        "context_phrase_match": False,
    }
    if not q_hits:
        return {"features": features, "score_delta": 0.0}

    q_text = query or ""
    c_text = candidate_text or ""
    delta = 0.0

    # Context tokens in query excluding pure number/unit
    context_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}", q_text))
    for h in q_hits:
        context_tokens.discard(h.unit)
        context_tokens.discard(h.number)

    for qh in q_hits:
        q_unit = _normalize_unit(qh.unit)
        exact = any(
            ch.number == qh.number and _normalize_unit(ch.unit) == q_unit for ch in c_hits
        )
        number_only = any(ch.number == qh.number for ch in c_hits)
        unit_mismatch = number_only and not exact and any(
            ch.number == qh.number and _normalize_unit(ch.unit) != q_unit for ch in c_hits
        )
        # also treat "60珠/米" as mismatch for query "60米"
        if number_only and not exact:
            # slash-compound units containing query unit as denominator
            if re.search(rf"{re.escape(qh.number)}\s*[^0-9\s]{{1,6}}/{re.escape(qh.unit)}", c_text):
                unit_mismatch = True

        phrase_hit = qh.phrase in c_text.replace(" ", "") or (
            qh.number in c_text and qh.unit in c_text and not unit_mismatch
        )

        if exact or phrase_hit:
            features["exact_number_unit_match"] = True
            delta += 0.35
        elif unit_mismatch:
            features["number_match_unit_mismatch"] = True
            delta -= 0.55
        elif number_only:
            delta += 0.05

    # Phrase context: boost if majority of non-numeric query tokens appear near hit
    if context_tokens:
        hit_count = sum(1 for t in context_tokens if t in c_text)
        if hit_count >= max(1, len(context_tokens) // 2):
            features["context_phrase_match"] = True
            delta += 0.25 * (hit_count / max(len(context_tokens), 1))
        elif hit_count == 0 and features["exact_number_unit_match"] is False:
            # Number alone without any context → mild penalty for confusable docs
            if any(h.number in c_text for h in q_hits):
                delta -= 0.1

    return {"features": features, "score_delta": delta}


def apply_numeric_unit_ranking(query: str, items: list[dict], *, text_keys: tuple[str, ...] = (
    "text", "content", "title", "summary", "chunk_text",
)) -> list[dict]:
    """Mutate items with unit features and re-rank by score + delta."""
    for item in items:
        blob = " ".join(str(item.get(k) or "") for k in text_keys)
        scored = score_numeric_unit_match(query, blob)
        item["numeric_unit_features"] = scored["features"]
        base = float(item.get("fts_score") or item.get("score") or 0.0)
        item["score"] = max(0.0, min(1.0, base + scored["score_delta"]))
        item["fts_score"] = item["score"]
    items.sort(key=lambda x: float(x.get("score") or x.get("fts_score") or 0), reverse=True)
    return items
