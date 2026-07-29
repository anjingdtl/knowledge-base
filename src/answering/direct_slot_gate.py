"""direct_slot_evidence — typed QueryPlan matcher without lowering threshold (SPEC v4 §E + v6 §4.1)."""
from __future__ import annotations

import re
from typing import Any

from src.answering.query_planner import plan_query

# Generic high-info patterns retained as fallback synonym tables (not Golden facts).
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
    ("收支两条线", re.compile(r"收支两条线|小金库"), ["收支两条线", "小金库"]),
    ("保密", re.compile(r"保密|商业秘密|邮箱|微信"), ["保密", "商业秘密", "邮箱"]),
    ("准入", re.compile(r"准入|入驻|门槛|合作"), ["准入", "入驻", "门槛"]),
]


def extract_query_high_info_slots(question: str) -> list[str]:
    """Legacy name: high-info slots from fixed patterns + typed plan anchors."""
    q = question or ""
    found: list[str] = []
    for name, pat, _ in _SLOT_DEFS:
        if pat.search(q) and name not in found:
            found.append(name)
    # Typed plan contributions
    plan = plan_query(q)
    for a in (plan.anchors or [])[:6]:
        if a and a not in found and len(a) >= 2:
            found.append(a)
    for c in plan.conditions or []:
        if c not in found:
            found.append(c)
    if plan.predicate and plan.predicate not in found:
        found.append(plan.predicate)
    return found


def evaluate_direct_slot_evidence(
    question: str,
    candidates: list[dict[str, Any]],
    *,
    min_slots: int = 2,
) -> dict[str, Any]:
    """Return direct_slot decision using typed plan + pattern slots.

    Accepts when a single passage matches >= min_slots high-info query slots
    and includes a fact-type cue, OR when typed anchors+predicate are jointly hit.
    """
    plan = plan_query(question or "")
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
        "typed_plan": {
            "anchors": list(plan.anchors or [])[:8],
            "predicate": plan.predicate,
            "conditions": list(plan.conditions or []),
            "polarity": plan.polarity,
        },
    }

    fact_type_re = re.compile(
        r"工作日|时限|处罚|限额|金额|元|%|％|标准|日|不得|禁止|负责|牵头|准入|取消|占比"
    )
    best: dict[str, Any] | None = None

    for cand in candidates or []:
        if not isinstance(cand, dict):
            continue
        text = str(cand.get("text") or cand.get("body_text") or "")
        title = str(cand.get("title") or "")
        blob = f"{title}\n{text}"
        if not text.strip() and not title.strip():
            continue

        matched: list[dict[str, Any]] = []
        # Pattern slots
        for name, pat, synonyms in _SLOT_DEFS:
            if name not in q_slots:
                continue
            m = pat.search(blob)
            if not m:
                continue
            surface = m.group(0)
            src = "synonym" if surface not in name else "literal"
            matched.append({
                "slot": name,
                "surface": surface,
                "synonym_source": src,
                "span": [m.start(), m.end()],
                "excerpt": blob[max(0, m.start() - 20): m.end() + 40],
            })

        # Typed anchors / predicate / polarity
        for a in (plan.anchors or [])[:8]:
            if not a or len(a) < 2:
                continue
            idx = blob.find(a)
            if idx >= 0 and not any(m["slot"] == a for m in matched):
                matched.append({
                    "slot": a,
                    "surface": a,
                    "synonym_source": "typed_anchor",
                    "span": [idx, idx + len(a)],
                    "excerpt": blob[max(0, idx - 20): idx + len(a) + 40],
                })
        if plan.predicate and plan.predicate in blob:
            if not any(m["slot"] == plan.predicate for m in matched):
                idx = blob.find(plan.predicate)
                matched.append({
                    "slot": plan.predicate,
                    "surface": plan.predicate,
                    "synonym_source": "typed_predicate",
                    "span": [idx, idx + len(plan.predicate)],
                    "excerpt": blob[max(0, idx - 20): idx + len(plan.predicate) + 40],
                })
        if plan.polarity == "negative" and re.search(r"不得|禁止|严禁|取消|不再", blob):
            if not any(m["slot"] == "polarity_negative" for m in matched):
                matched.append({
                    "slot": "polarity_negative",
                    "surface": "negative",
                    "synonym_source": "typed_polarity",
                    "span": [0, 0],
                    "excerpt": "",
                })

        # Accept criteria
        typed_ok = False
        if plan.anchors:
            anchor_hits = sum(1 for a in plan.anchors if a and a in blob)
            if anchor_hits >= 2:
                typed_ok = True
            if plan.predicate and plan.predicate in blob and anchor_hits >= 1:
                typed_ok = True
        if plan.conditions:
            cond_hits = sum(1 for c in plan.conditions if c in blob)
            if cond_hits >= 1 and fact_type_re.search(blob):
                typed_ok = True

        if len(matched) < min_slots and not typed_ok:
            continue
        if not fact_type_re.search(blob) and not typed_ok:
            continue
        score = len(matched) / max(1, len(q_slots) or 1)
        if typed_ok:
            score = max(score, 0.75)
        rec = {
            "direct_slot_evidence": True,
            "matched_slots": [m["slot"] for m in matched],
            "passage_id": cand.get("passage_id") or cand.get("id"),
            "knowledge_id": cand.get("knowledge_id"),
            "spans": matched,
            "reason": "multi_slot_fact_match" if len(matched) >= min_slots else "typed_plan_match",
            "query_slots": q_slots,
            "score": round(score, 4),
            "candidate": cand,
            "typed_plan": empty["typed_plan"],
        }
        if best is None or rec["score"] > best["score"]:
            best = rec

    if best is None and len(q_slots) < min_slots:
        # Typed-only path when few fixed slots: still try anchors
        for cand in candidates or []:
            if not isinstance(cand, dict):
                continue
            text = str(cand.get("text") or cand.get("body_text") or "")
            title = str(cand.get("title") or "")
            blob = f"{title}\n{text}"
            anchor_hits = [a for a in (plan.anchors or []) if a and a in blob]
            if len(anchor_hits) >= 2 or (
                plan.predicate and plan.predicate in blob and anchor_hits
            ):
                return {
                    "direct_slot_evidence": True,
                    "matched_slots": anchor_hits + ([plan.predicate] if plan.predicate else []),
                    "passage_id": cand.get("passage_id") or cand.get("id"),
                    "knowledge_id": cand.get("knowledge_id"),
                    "spans": [],
                    "reason": "typed_plan_match",
                    "query_slots": q_slots,
                    "score": 0.8,
                    "candidate": cand,
                    "typed_plan": empty["typed_plan"],
                }
        empty["reason"] = f"query_slots_lt_{min_slots}"
        return empty

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
            "reason", "query_slots", "score", "typed_plan",
        )
        if k in ds
    }
    if not ds.get("direct_slot_evidence"):
        return out

    cand = ds.get("candidate")
    items = list(out.get("items") or [])
    if isinstance(cand, dict):
        row = dict(cand)
        row["direct_slot_evidence"] = True
        row["final_relevance_score"] = max(
            float(row.get("final_relevance_score") or row.get("score") or 0.0),
            threshold,
        )
        row["score"] = row["final_relevance_score"]
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
    out["direct_slot_top"] = ds.get("score")
    if out.get("top_score") is None:
        out["top_score"] = float((cand or {}).get("score") or 0.0)
    return out
