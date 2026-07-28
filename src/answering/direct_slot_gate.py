"""direct_slot_evidence — multi-slot accept without lowering global threshold (SPEC v4 §E)."""
from __future__ import annotations

import re
from typing import Any

# High-information slot patterns (generic, not Golden-case specific).
_SLOT_DEFS: list[tuple[str, re.Pattern[str], list[str]]] = [
    ("产品问需", re.compile(r"产品问需|问需工单|问需"), ["产品问需", "问需工单", "问需"]),
    ("初审", re.compile(r"审核初审|初审"), ["审核初审", "初审"]),
    ("产品评估", re.compile(r"产品评估|评估时限|评估"), ["产品评估", "评估时限"]),
    ("工作日时限", re.compile(r"工作日|时限"), ["工作日", "时限"]),
    ("涉诈", re.compile(r"涉诈|诈骗|防诈"), ["涉诈", "诈骗"]),
    ("涉骚扰", re.compile(r"涉骚扰|骚扰"), ["涉骚扰", "骚扰"]),
    ("代理商", re.compile(r"代理商"), ["代理商"]),
    ("处罚", re.compile(r"处罚|罚款|罚"), ["处罚", "罚款"]),
    ("自然月", re.compile(r"自然月|每个号码"), ["自然月", "每个号码"]),
    ("限额", re.compile(r"限额|年付款"), ["限额", "年付款"]),
    ("III类", re.compile(r"III\s*类|Ⅲ\s*类|三类"), ["III类", "三类"]),
    ("差旅", re.compile(r"差旅|住宿|伙食"), ["差旅", "住宿", "伙食"]),
    ("技能竞赛", re.compile(r"技能竞赛|竞赛"), ["技能竞赛", "竞赛"]),
    ("合同章", re.compile(r"合同专用章|实体章|电子章"), ["合同专用章", "实体章", "电子章"]),
]


def extract_query_high_info_slots(question: str) -> list[str]:
    q = question or ""
    found: list[str] = []
    for name, pat, _ in _SLOT_DEFS:
        if pat.search(q) and name not in found:
            found.append(name)
    return found


def evaluate_direct_slot_evidence(
    question: str,
    candidates: list[dict[str, Any]],
    *,
    min_slots: int = 2,
) -> dict[str, Any]:
    """Return direct_slot decision with auditable spans.

    Accepts when a single passage matches >= min_slots high-info query slots
    and includes a fact-type cue (时限/处罚/限额/工作日/元/…).
    """
    q_slots = extract_query_high_info_slots(question)
    empty = {
        "direct_slot_evidence": False,
        "matched_slots": [],
        "passage_id": None,
        "knowledge_id": None,
        "spans": [],
        "reason": "no_high_info_slots" if not q_slots else "no_candidate_match",
        "query_slots": q_slots,
        "score": 0.0,
    }
    if len(q_slots) < min_slots:
        # Still allow if query has 1 slot name but multiple synonyms? No — SPEC
        # requires at least two high-info slots.
        empty["reason"] = f"query_slots_lt_{min_slots}"
        return empty

    fact_type_re = re.compile(r"工作日|时限|处罚|限额|金额|元|%|％|标准|日")
    best: dict[str, Any] | None = None

    for cand in candidates or []:
        if not isinstance(cand, dict):
            continue
        text = str(cand.get("text") or "")
        title = str(cand.get("title") or "")
        blob = f"{title}\n{text}"
        if not text.strip():
            continue
        matched: list[dict[str, Any]] = []
        for name, pat, synonyms in _SLOT_DEFS:
            if name not in q_slots:
                continue
            m = pat.search(blob)
            if not m:
                continue
            # Record which synonym surface form matched.
            surface = m.group(0)
            src = "synonym" if surface not in name else "literal"
            matched.append({
                "slot": name,
                "surface": surface,
                "synonym_source": src,
                "span": [m.start(), m.end()],
                "excerpt": blob[max(0, m.start() - 20): m.end() + 40],
            })
        if len(matched) < min_slots:
            continue
        if not fact_type_re.search(blob):
            continue
        score = len(matched) / max(1, len(q_slots))
        rec = {
            "direct_slot_evidence": True,
            "matched_slots": [m["slot"] for m in matched],
            "passage_id": cand.get("passage_id") or cand.get("id"),
            "knowledge_id": cand.get("knowledge_id"),
            "spans": matched,
            "reason": "multi_slot_fact_match",
            "query_slots": q_slots,
            "score": round(score, 4),
            "candidate": cand,
        }
        if best is None or rec["score"] > best["score"]:
            best = rec

    return best or empty


def apply_direct_slot_accept(
    question: str,
    candidates: list[dict[str, Any]],
    *,
    base_decision: dict[str, Any],
    threshold: float = 0.35,
) -> dict[str, Any]:
    """Merge direct_slot into gate decision without changing threshold."""
    out = dict(base_decision or {})
    out["threshold"] = threshold
    if out.get("accept"):
        out.setdefault("direct_slot_evidence", False)
        return out

    ds = evaluate_direct_slot_evidence(question, candidates)
    out["direct_slot_evidence"] = bool(ds.get("direct_slot_evidence"))
    out["direct_slot_audit"] = {
        k: ds[k]
        for k in (
            "matched_slots", "passage_id", "knowledge_id", "spans",
            "reason", "query_slots", "score",
        )
        if k in ds
    }
    if not ds.get("direct_slot_evidence"):
        return out

    # Accept the matched candidate (and keep any existing items).
    cand = ds.get("candidate")
    items = list(out.get("items") or [])
    if isinstance(cand, dict):
        # Ensure candidate is first and tagged.
        row = dict(cand)
        row["direct_slot_evidence"] = True
        row["final_relevance_score"] = max(
            float(row.get("final_relevance_score") or row.get("score") or 0.0),
            threshold,
        )
        row["score"] = row["final_relevance_score"]
        # Avoid duplicate knowledge/passage
        pid = str(row.get("passage_id") or "")
        kid = str(row.get("knowledge_id") or "")
        filtered = [
            it for it in items
            if str(it.get("passage_id") or "") != pid
            or str(it.get("knowledge_id") or "") != kid
        ]
        items = [row] + filtered
    out["accept"] = True
    out["items"] = items
    out["reason"] = "direct_slot_evidence"
    # Do NOT rewrite top_score below threshold semantics — expose separately.
    out["direct_slot_top"] = ds.get("score")
    if out.get("top_score") is None:
        out["top_score"] = float((cand or {}).get("score") or 0.0)
    return out
