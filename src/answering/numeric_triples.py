"""Condition → value → unit numeric triples (SPEC v4 §C)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

_VALUE_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>万元|亿|元|%|％|天|个工作日|工作日|个月|月|年|次|户|人|个|倍)",
    re.IGNORECASE,
)

# Ordered condition patterns: more specific first.
_CONDITION_PATTERNS: list[tuple[re.Pattern[str], str, list[str]]] = [
    (re.compile(r"III\s*类|Ⅲ\s*类|三类"), "III类", ["III类", "Ⅲ类", "三类"]),
    # Negative lookbehind: "II类" must not be the tail of "III类".
    (re.compile(r"(?<![IⅠ])II\s*类|Ⅱ\s*类|二类"), "II类", ["II类", "Ⅱ类", "二类"]),
    (re.compile(r"(?<![IⅠ二三])I\s*类|(?<![IⅠ二三])Ⅰ\s*类|一类"), "I类", ["I类", "Ⅰ类", "一类"]),
    (re.compile(r"涉诈|诈骗|防诈"), "涉诈", ["涉诈", "诈骗", "防诈"]),
    (re.compile(r"涉骚扰|骚扰"), "涉骚扰", ["涉骚扰", "骚扰"]),
    (re.compile(r"区外"), "区外", ["区外"]),
    (re.compile(r"区内"), "区内", ["区内"]),
    (re.compile(r"团体"), "团体", ["团体"]),
    (re.compile(r"个人"), "个人", ["个人"]),
    (re.compile(r"初审|审核初审"), "初审", ["初审", "审核初审"]),
    (re.compile(r"产品评估|评估"), "产品评估", ["产品评估", "评估"]),
]

_CLAUSE_SPLIT = re.compile(
    r"[；;。\n]|（[一二三四五六七八九十0-9]+）|"
    r"(?=第[一二三四五六七八九十百千0-9]+[条款项])|"
    r"(?=附件\s*[0-9一二三四五六七八九十]*)"
)


@dataclass
class NumericTriple:
    condition: str
    value: str
    unit: str
    evidence_passage_id: str = ""
    condition_span: str = ""
    evidence_char_range: tuple[int, int] | None = None
    raw_match: str = ""

    def display(self) -> str:
        return f"{self.value}{self.unit}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.evidence_char_range is not None:
            d["evidence_char_range"] = list(self.evidence_char_range)
        return d


def extract_query_slots(question: str) -> dict[str, Any]:
    q = question or ""
    conditions: list[str] = []
    for pat, label, _ in _CONDITION_PATTERNS:
        if pat.search(q) and label not in conditions:
            conditions.append(label)
    fact_types: list[str] = []
    if re.search(r"限额|金额|处罚|罚|扣分|占比|比例|标准|多少|元|%|％", q):
        fact_types.append("numeric")
    if re.search(r"时限|工作日|期限|几天|多少天", q):
        fact_types.append("deadline")
    if re.search(r"最新|修订版|版本|哪一年", q):
        fact_types.append("version")
    return {
        "conditions": conditions,
        "fact_types": fact_types,
        "raw": q,
    }


def extract_numeric_triples(
    text: str,
    *,
    passage_id: str = "",
) -> list[NumericTriple]:
    """Extract condition-value-unit triples from a passage **body**.

    SPEC v5: never use the whole passage as a fallback clause (that caused
    every condition to bind to every number). Bind only within the same
    clause / local window. Strip leading 【文档】/【章节】 metadata first.
    """
    from src.answering.passage_evidence import split_metadata_and_body

    raw = text or ""
    body, _start, _meta = split_metadata_and_body(raw)
    if not body.strip():
        body = raw
    triples: list[NumericTriple] = []
    clauses = [c for c in _CLAUSE_SPLIT.split(body) if c and c.strip()]
    # SPEC v5: DO NOT append full body as fallback (cross-row binding).
    if not clauses and body.strip():
        clauses = [body]

    for clause in clauses:
        local_conditions: list[str] = []
        for pat, label, _ in _CONDITION_PATTERNS:
            if pat.search(clause) and label not in local_conditions:
                local_conditions.append(label)
        for m in _VALUE_RE.finditer(clause):
            value = m.group("value")
            unit = m.group("unit")
            # Prefer conditions found in the same clause; else a tight window.
            conds = list(local_conditions)
            if not conds:
                start = max(0, m.start() - 36)
                window = clause[start:m.end() + 10]
                for pat, label, _ in _CONDITION_PATTERNS:
                    if pat.search(window) and label not in conds:
                        conds.append(label)
            if not conds:
                # Single-value short clause may have empty condition; multi-value
                # without local condition is skipped (no cross-row guess).
                if len(list(_VALUE_RE.finditer(clause))) == 1 and len(clause) <= 80:
                    conds = [""]
                else:
                    continue
            abs_start = body.find(clause)
            if abs_start < 0:
                abs_start = 0
            for cond in conds:
                triples.append(NumericTriple(
                    condition=cond,
                    value=value,
                    unit=unit,
                    evidence_passage_id=passage_id,
                    condition_span=clause.strip()[:200],
                    evidence_char_range=(abs_start + m.start(), abs_start + m.end()),
                    raw_match=m.group(0),
                ))
    return _dedupe_triples(triples)


def _dedupe_triples(triples: list[NumericTriple]) -> list[NumericTriple]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[NumericTriple] = []
    for t in triples:
        key = (t.condition, t.value, t.unit, t.evidence_passage_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def filter_triples_for_query(
    triples: list[NumericTriple],
    *,
    question: str,
) -> tuple[list[NumericTriple], dict[str, Any]]:
    slots = extract_query_slots(question)
    conditions = slots.get("conditions") or []
    q = question or ""
    money_intent = bool(re.search(r"处罚|罚|金额|限额|付款|补助|标准|元", q))
    year_intent = bool(re.search(r"哪一年|年份|版本|哪年|最新版本", q))
    deadline_intent = bool(re.search(r"时限|工作日|几天", q))

    kept: list[NumericTriple] = []
    dropped: list[dict[str, Any]] = []

    for t in triples:
        # Drop document-year noise (e.g. 2026年) unless the query asks for a year.
        if t.unit == "年" and not year_intent:
            dropped.append({"triple": t.to_dict(), "reason": "year_unit_without_year_intent"})
            continue
        if money_intent and t.unit not in ("元", "万元", "亿", "%", "％"):
            dropped.append({"triple": t.to_dict(), "reason": "unit_not_money_for_money_intent"})
            continue
        if deadline_intent and t.unit not in ("个工作日", "工作日", "天", "日"):
            # allow only deadline-like units when asking 时限
            if t.unit not in ("个工作日", "工作日", "天"):
                dropped.append({"triple": t.to_dict(), "reason": "unit_not_deadline"})
                continue
        if conditions:
            if t.condition and t.condition in conditions:
                kept.append(t)
            else:
                dropped.append({
                    "triple": t.to_dict(),
                    "reason": "condition_mismatch",
                    "wanted": conditions,
                })
        else:
            kept.append(t)

    # Prefer more specific conditions when multiple match.
    if len(conditions) > 1:
        priority = {c: i for i, c in enumerate(conditions)}
        kept = sorted(kept, key=lambda t: priority.get(t.condition, 99))

    audit = {
        "query_slots": slots,
        "kept": [t.to_dict() for t in kept],
        "dropped": dropped[:50],
    }
    return kept, audit


def select_answer_triples(
    triples: list[NumericTriple],
    *,
    question: str,
) -> list[NumericTriple]:
    kept, _ = filter_triples_for_query(triples, question=question)
    slots = extract_query_slots(question)
    conditions = slots.get("conditions") or []
    if not conditions:
        return kept
    # One triple per condition (first match).
    out: list[NumericTriple] = []
    seen_cond: set[str] = set()
    for t in kept:
        if t.condition in seen_cond:
            continue
        seen_cond.add(t.condition)
        out.append(t)
    return out


def triples_from_evidence_rows(rows: list[dict[str, Any]]) -> list[NumericTriple]:
    all_t: list[NumericTriple] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        pid = str(r.get("passage_id") or "")
        # Prefer body_text so metadata headers never participate.
        text = str(r.get("body_text") or r.get("text") or "")
        all_t.extend(extract_numeric_triples(text, passage_id=pid))
    return all_t
