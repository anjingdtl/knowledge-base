"""Direct evidence gate derived from the current user query.

The gate is intentionally vocabulary-free: it cannot contain a catalogue of
documents, facts, or evaluation examples.  It only verifies that one passage
directly covers several query-derived anchors plus a generic fact cue.
"""
from __future__ import annotations

import re
from typing import Any

from src.answering.query_planner import QueryPlan, plan_query


_FACT_CUE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:元|万元|%|％|日|天|年)|"
    r"不得|禁止|严禁|取消|不再|应当|必须|负责|牵头|归口|"
    r"适用|范围|限额|额度|标准|处罚|罚款|准入|资格|审核|审查|审批|流程|时限|期限|效力"
)


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    return [v for v in values if v and not (v in seen or seen.add(v))]


def _plan_slots(plan: QueryPlan) -> list[str]:
    values = list(plan.anchors or []) + list(plan.conditions or []) + list(plan.scope or [])
    # Predicate *class* is not usually a literal.  Polarity and fact cue are
    # checked separately instead of pretending it is a source surface form.
    return _ordered_unique([v for v in values if len(v) >= 2])[:12]


def extract_query_high_info_slots(question: str) -> list[str]:
    """Compatibility API: return only query-derived evidence slots."""
    return _plan_slots(plan_query(question or ""))


def _matches(candidate: dict[str, Any], plan: QueryPlan, slots: list[str]) -> list[dict[str, Any]]:
    text = str(candidate.get("text") or candidate.get("body_text") or "")
    title = str(candidate.get("title") or "")
    blob = f"{title}\n{text}"
    out: list[dict[str, Any]] = []
    for slot in slots:
        idx = blob.find(slot)
        if idx >= 0:
            out.append({
                "slot": slot,
                "surface": slot,
                "synonym_source": "query_surface",
                "span": [idx, idx + len(slot)],
                "excerpt": blob[max(0, idx - 20): idx + len(slot) + 40],
            })
    if plan.polarity == "negative":
        m = re.search(r"不得|禁止|严禁|取消|不再|废止|停止", blob)
        if m:
            out.append({
                "slot": "polarity_negative",
                "surface": m.group(0),
                "synonym_source": "language_operator",
                "span": [m.start(), m.end()],
                "excerpt": blob[max(0, m.start() - 20): m.end() + 40],
            })
    return out


def evaluate_direct_slot_evidence(
    question: str,
    candidates: list[dict[str, Any]],
    *,
    min_slots: int = 2,
) -> dict[str, Any]:
    """Accept direct passage evidence without changing the global threshold."""
    plan = plan_query(question or "")
    slots = _plan_slots(plan)
    empty = {
        "direct_slot_evidence": False,
        "matched_slots": [],
        "passage_id": None,
        "knowledge_id": None,
        "spans": [],
        "reason": "no_query_anchors" if not slots else "no_candidate_match",
        "query_slots": slots,
        "score": 0.0,
        "typed_plan": {
            "anchors": list(plan.anchors or [])[:8],
            "predicate": plan.predicate,
            "conditions": list(plan.conditions or []),
            "polarity": plan.polarity,
        },
    }
    best: dict[str, Any] | None = None
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        text = str(candidate.get("text") or candidate.get("body_text") or "")
        title = str(candidate.get("title") or "")
        blob = f"{title}\n{text}"
        if not blob.strip():
            continue
        matched = _matches(candidate, plan, slots)
        matched_names = _ordered_unique([m["slot"] for m in matched])
        cue = bool(_FACT_CUE_RE.search(blob))
        # When the user named an operational predicate, anchors alone are not
        # enough: a passage can mention the same entity while describing a
        # different rule.  Require the predicate surface in the same evidence
        # item before the direct-gate exception may promote it.
        predicate_ok = not plan.predicate or plan.predicate in blob
        # A condition is independently meaningful only when the passage also
        # contains a factual cue; otherwise generic headings must not pass.
        condition_hit = bool(set(plan.conditions or []) & set(matched_names)) and cue
        anchor_hit_count = sum(1 for s in matched_names if s != "polarity_negative")
        polarity_ok = plan.polarity != "negative" or "polarity_negative" in matched_names
        direct = cue and predicate_ok and polarity_ok and (
            anchor_hit_count >= min_slots or (condition_hit and anchor_hit_count >= 1)
        )
        if not direct:
            continue
        score = anchor_hit_count / max(1, len(slots))
        if condition_hit:
            score += 0.15
        if plan.polarity == "negative":
            score += 0.1
        record = {
            "direct_slot_evidence": True,
            "matched_slots": matched_names,
            "passage_id": candidate.get("passage_id") or candidate.get("id"),
            "knowledge_id": candidate.get("knowledge_id"),
            "spans": matched,
            "reason": "query_anchor_fact_match",
            "query_slots": slots,
            "score": round(score, 4),
            "candidate": candidate,
            "typed_plan": empty["typed_plan"],
        }
        if best is None or record["score"] > best["score"]:
            best = record
    if best is None and slots and len(slots) < min_slots:
        empty["reason"] = f"query_slots_lt_{min_slots}"
    return best or empty


def apply_direct_slot_accept(
    question: str,
    candidates: list[dict[str, Any]],
    *,
    base_decision: dict[str, Any],
    threshold: float = 0.35,
) -> dict[str, Any]:
    """Merge verified direct evidence into a rejected gate without threshold drift."""
    out = dict(base_decision or {})
    out["threshold"] = threshold
    if out.get("accept"):
        out.setdefault("direct_slot_evidence", False)
        return out
    top_score = float(out.get("top_score") or 0.0)
    # Near-threshold retrieval with one independently verifiable query anchor
    # plus a factual cue is stronger than a score-only rejection.  This is an
    # evidence exception, not a global threshold change.
    min_slots = 1 if top_score >= threshold * 0.90 else 2
    evidence = evaluate_direct_slot_evidence(question, candidates, min_slots=min_slots)
    out["direct_slot_evidence"] = bool(evidence.get("direct_slot_evidence"))
    out["direct_slot_audit"] = {
        key: evidence[key]
        for key in (
            "matched_slots", "passage_id", "knowledge_id", "spans", "reason",
            "query_slots", "score", "typed_plan",
        ) if key in evidence
    }
    if not evidence.get("direct_slot_evidence"):
        return out
    candidate = evidence.get("candidate")
    items = list(out.get("items") or [])
    if isinstance(candidate, dict):
        row = dict(candidate)
        row["direct_slot_evidence"] = True
        row["final_relevance_score"] = max(
            float(row.get("final_relevance_score") or row.get("score") or 0.0), threshold,
        )
        row["score"] = row["final_relevance_score"]
        pid = str(row.get("passage_id") or "")
        kid = str(row.get("knowledge_id") or "")
        items = [row] + [
            item for item in items
            if str(item.get("passage_id") or "") != pid
            or str(item.get("knowledge_id") or "") != kid
        ]
    out["accept"] = True
    out["items"] = items
    out["reason"] = "direct_query_evidence"
    out["direct_slot_top"] = evidence.get("score")
    if out.get("top_score") is None:
        out["top_score"] = float((candidate or {}).get("score") or 0.0)
    return out
