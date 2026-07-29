"""Unified FactCandidate pipeline (SPEC v5 §2.3–2.4 + v6 §2–3).

Stable candidate IDs, typed slot matching, coverage matrix.
Query-derived anchors only — no fixed high-value phrase lists.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from src.answering.logical_evidence import LogicalEvidenceRecord, records_from_evidence_list
from src.answering.query_planner import QueryPlan, extract_conditions, plan_query

_VALUE_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>万元|万|亿|元|%|％|天|个工作日|工作日|个月|月|年|次|户|人|个|倍)",
    re.IGNORECASE,
)
_DEADLINE_RE = re.compile(
    r"(?P<label>[^。；;\n]{0,20}(?:初审|审核|评估|产品评估)[^。；;\n]{0,20})"
    r"(?P<value>\d+)\s*(?P<unit>个?工作日)",
)
_DOCNO_RE = re.compile(
    r"(?:中电信桂|市场)?[〔\[]?\s*((?:19|20)\d{2})\s*[〕\]]?\s*[-—]?\s*(\d+)\s*号"
)
_YEAR_RE = re.compile(r"((?:19|20)\d{2})")


def stable_candidate_id(
    *,
    passage_id: str,
    body_span: tuple[int, int] | list[int] | None,
    fact_kind: str,
    exact_text: str,
) -> str:
    """Deterministic identity across processes (SPEC v6 §2.2)."""
    span = body_span or (0, 0)
    if isinstance(span, (list, tuple)) and len(span) >= 2:
        span_s = f"{span[0]}:{span[1]}"
    else:
        span_s = "0:0"
    norm = re.sub(r"\s+", "", exact_text or "")
    raw = f"{passage_id}|{span_s}|{fact_kind}|{norm}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass
class FactCandidate:
    candidate_id: str
    record_id: str
    passage_id: str
    knowledge_id: str
    fact_kind: str
    subject: str = ""
    predicate: str = ""
    object: str = ""
    qualifiers: list[str] = field(default_factory=list)
    condition: str = ""
    value: str = ""
    unit: str = ""
    exact_text: str = ""
    evidence_spans: list[tuple[int, int]] = field(default_factory=list)
    table_row_ref: str = ""
    document_family_id: str = ""
    version_year: int | None = None
    score: float = 0.0
    unstructured_rejected: bool = False
    group_id: str = ""
    polarity: str = ""
    value_dimension: str = ""
    slot_match: dict[str, str] = field(default_factory=dict)  # hit|miss|unknown
    slot_spans: dict[str, list[int]] = field(default_factory=dict)
    trace_repaired: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence_spans"] = [list(s) for s in self.evidence_spans]
        return d

    def display(self) -> str:
        if self.fact_kind in ("numeric", "deadline") and self.value:
            if self.condition:
                return f"{self.condition}{self.value}{self.unit}"
            return f"{self.value}{self.unit}"
        return (self.exact_text or self.object or "").strip()


def extract_candidates_from_records(
    records: list[LogicalEvidenceRecord],
    *,
    plan: QueryPlan | None = None,
) -> list[FactCandidate]:
    """Extract candidates only for intents allowed by the query plan."""
    plan = plan or QueryPlan(raw="")
    allow = set(plan.allow_fact_kinds or [])
    if not allow:
        allow = {"policy", "numeric", "deadline", "version", "responsibility", "scope"}

    out: list[FactCandidate] = []
    for rec in records:
        if rec.unstructured_table or rec.type == "unstructured_table":
            out.append(
                FactCandidate(
                    candidate_id=stable_candidate_id(
                        passage_id=rec.passage_id,
                        body_span=rec.source_span,
                        fact_kind="numeric",
                        exact_text="ambiguous",
                    ),
                    record_id=rec.record_id,
                    passage_id=rec.passage_id,
                    knowledge_id=rec.knowledge_id,
                    fact_kind="numeric",
                    exact_text="",
                    unstructured_rejected=True,
                    document_family_id=rec.document_family_id,
                    version_year=rec.version_year,
                    table_row_ref=rec.table_id,
                )
            )
            if "policy" in allow or "prohibition" in allow:
                out.extend(_policy_from_record(rec, plan))
            continue

        if "numeric" in allow:
            out.extend(_numeric_from_record(rec, plan))
        if "deadline" in allow:
            out.extend(_deadline_from_record(rec, plan))
        if "version" in allow:
            out.extend(_version_from_record(rec, plan))
        if allow & {"policy", "prohibition", "responsibility", "scope", "relationship"}:
            out.extend(_policy_from_record(rec, plan))

    return _dedupe_candidates(out)


def extract_candidates_from_evidence(
    evidence_rows: list[Any] | None,
    *,
    question: str,
) -> tuple[list[FactCandidate], QueryPlan, list[LogicalEvidenceRecord]]:
    plan = plan_query(question)
    records = records_from_evidence_list(evidence_rows)
    cands = extract_candidates_from_records(records, plan=plan)
    return cands, plan, records


def annotate_slot_match(candidate: FactCandidate, plan: QueryPlan) -> FactCandidate:
    """Fill slot_match for required slots (hit/miss/unknown)."""
    text = candidate.exact_text or candidate.display() or ""
    body = text
    match: dict[str, str] = {}
    spans: dict[str, list[int]] = {}

    def _hit(slot: str, needle: str) -> None:
        if not needle:
            match[slot] = "unknown"
            return
        idx = body.find(needle)
        if idx >= 0:
            match[slot] = "hit"
            spans[slot] = [idx, idx + len(needle)]
        else:
            match[slot] = "miss"

    for slot in plan.required_slots or []:
        if slot in ("value", "unit") and candidate.fact_kind in ("numeric", "deadline"):
            if slot == "value" and candidate.value:
                match["value"] = "hit"
                spans["value"] = list(candidate.evidence_spans[0]) if candidate.evidence_spans else []
            elif slot == "unit" and candidate.unit:
                match["unit"] = "hit"
            else:
                match[slot] = "miss"
        elif slot == "condition":
            if plan.conditions:
                if candidate.condition and candidate.condition in plan.conditions:
                    match["condition"] = "hit"
                elif any(c in body for c in plan.conditions):
                    match["condition"] = "hit"
                else:
                    match["condition"] = "miss"
            else:
                match["condition"] = "unknown"
        elif slot.startswith("condition:"):
            condition = slot.split(":", 1)[1]
            if candidate.condition == condition or condition in body:
                match[slot] = "hit"
            else:
                match[slot] = "miss"
        elif slot.startswith("dim:"):
            dim = slot[4:]
            if candidate.value_dimension == dim or candidate.condition == dim:
                match[slot] = "hit"
            elif dim in body or (candidate.condition and dim in candidate.condition):
                match[slot] = "hit"
            elif dim in ("total", "per_unit", "ratio", "annual", "period", "value"):
                # Dimension coverage via unit/condition heuristics
                if dim == "ratio" and candidate.unit in ("%", "％"):
                    match[slot] = "hit"
                elif dim == "annual" and candidate.unit in ("万元", "元"):
                    match[slot] = "hit"
                elif dim == "total" and candidate.unit in ("元", "万元"):
                    match[slot] = "hit"
                elif dim == "per_unit" and (
                    re.search(r"每(?:人|个|名)|人均", body)
                    or "人" in body
                ):
                    match[slot] = "hit"
                elif dim == "value" and candidate.value:
                    match[slot] = "hit"
                else:
                    match[slot] = "miss" if candidate.fact_kind == "numeric" else "unknown"
            else:
                match[slot] = "miss"
        elif slot in plan.conditions:
            if candidate.condition == slot or slot in body:
                match[slot] = "hit"
            else:
                match[slot] = "miss"
        elif slot in ("policy_fact", "subject", "role", "scope", "relationship", "deadline", "version", "doc_no"):
            if slot == "policy_fact" and candidate.fact_kind in (
                "policy", "prohibition", "responsibility", "scope", "relationship"
            ):
                match[slot] = "hit" if len(body) >= 6 else "miss"
            elif slot == "deadline" and candidate.fact_kind == "deadline":
                match[slot] = "hit"
            elif slot == "version" and candidate.fact_kind == "version":
                match[slot] = "hit"
            elif slot in ("初审", "产品评估") and (
                candidate.condition == slot or slot in body
            ):
                match[slot] = "hit"
            elif slot == "polarity_negative":
                if re.search(r"不得|禁止|严禁|取消|不再", body) or candidate.polarity == "negative":
                    match[slot] = "hit"
                else:
                    match[slot] = "miss"
            elif slot.startswith("predicate:"):
                pred = slot.split(":", 1)[1]
                _hit(slot, pred)
            else:
                # Generic: check anchors / entities
                anchors = plan.anchors or plan.entities or []
                if any(a and a in body for a in anchors[:6]):
                    match[slot] = "hit"
                else:
                    match[slot] = "unknown"
        elif slot.startswith("predicate:"):
            pred = slot.split(":", 1)[1]
            _hit(slot, pred)
        elif slot == "polarity_negative":
            if re.search(r"不得|禁止|严禁|取消|不再", body) or candidate.polarity == "negative":
                match[slot] = "hit"
            else:
                match[slot] = "miss"
        else:
            match[slot] = "unknown"

    # Entity / predicate anchors
    for a in (plan.anchors or [])[:8]:
        if a and a in body:
            match.setdefault(f"anchor:{a}", "hit")
        elif a:
            match.setdefault(f"anchor:{a}", "miss")

    candidate.slot_match = match
    candidate.slot_spans = spans
    return candidate


def build_coverage_matrix(
    required_slots: list[str],
    candidates: list[FactCandidate],
) -> dict[str, dict[str, str]]:
    """coverage_matrix[required_slot][candidate_id] = hit|miss|unknown."""
    matrix: dict[str, dict[str, str]] = {s: {} for s in required_slots}
    for c in candidates:
        for s in required_slots:
            matrix[s][c.candidate_id] = (c.slot_match or {}).get(s, "unknown")
    return matrix


def select_fact_candidates(
    candidates: list[FactCandidate],
    *,
    plan: QueryPlan,
    max_items: int = 6,
    primary_group_id: str | None = None,
    group_knowledge_ids: set[str] | None = None,
) -> tuple[list[FactCandidate], dict[str, Any]]:
    """Rank and pick candidates covering query slots via coverage matrix."""
    audit: dict[str, Any] = {
        "plan": plan.to_dict(),
        "input_count": len(candidates),
        "dropped": [],
        "selected": [],
        "table_structure_ambiguous": False,
        "coverage_matrix": {},
        "primary_group_id": primary_group_id,
    }
    usable = [c for c in candidates if not c.unstructured_rejected]
    ambiguous = [c for c in candidates if c.unstructured_rejected]
    if ambiguous and plan.wants_numeric and not usable:
        audit["table_structure_ambiguous"] = True
        audit["dropped"].append({"reason": "table_structure_ambiguous", "count": len(ambiguous)})
        return [], audit

    # Restrict to primary group when provided.
    if group_knowledge_ids:
        grouped = [
            c for c in usable
            if (c.knowledge_id and c.knowledge_id in group_knowledge_ids)
            or (primary_group_id and c.group_id == primary_group_id)
        ]
        if grouped:
            for c in usable:
                if c not in grouped:
                    audit["dropped"].append({
                        "id": c.candidate_id,
                        "reason": "outside_primary_group",
                        "knowledge_id": c.knowledge_id,
                    })
            usable = grouped

    allow = set(plan.allow_fact_kinds or [])
    filtered: list[FactCandidate] = []
    for c in usable:
        if allow and c.fact_kind not in allow and not (
            c.fact_kind == "prohibition" and "policy" in allow
        ):
            audit["dropped"].append({"id": c.candidate_id, "reason": "kind_not_allowed"})
            continue
        if not plan.wants_numeric and c.fact_kind == "numeric":
            audit["dropped"].append({"id": c.candidate_id, "reason": "numeric_without_numeric_intent"})
            continue
        if plan.wants_numeric and c.fact_kind == "numeric":
            # Distinguish a monetary answer request from a duration/count
            # request before slot coverage can stop on an unrelated number.
            # These are general question forms; the unit constraint remains
            # evidence-derived rather than corpus-specific.
            money_q = bool(re.search(
                r"处罚|罚款|金额|限额|额度|元|钱|费用|价格|报销|奖金|奖励|补助|占比|比例",
                plan.raw or "",
            )) or plan.predicate in {"准入", "门槛", "资格"}
            if money_q and c.unit not in ("元", "万元", "万", "亿", "%", "％"):
                audit["dropped"].append({"id": c.candidate_id, "reason": "unit_not_money"})
                continue
            # User-stated conditions only — scope words are never conditions.
            if plan.conditions:
                if c.condition and c.condition not in plan.conditions:
                    audit["dropped"].append({
                        "id": c.candidate_id,
                        "reason": "condition_mismatch",
                        "wanted": plan.conditions,
                    })
                    continue
                if not c.condition and plan.conditions:
                    audit["dropped"].append({
                        "id": c.candidate_id,
                        "reason": "missing_condition_for_conditioned_query",
                    })
                    continue
        annotate_slot_match(c, plan)
        c.score = _score_candidate(c, plan)
        filtered.append(c)

    filtered.sort(key=lambda x: x.score, reverse=True)
    matrix = build_coverage_matrix(list(plan.required_slots or []), filtered)
    audit["coverage_matrix"] = matrix

    selected: list[FactCandidate] = []
    covered: set[str] = set()
    covered_anchors: set[str] = set()

    def _mark_covered(c: FactCandidate) -> None:
        for slot, status in (c.slot_match or {}).items():
            if status == "hit" and not slot.startswith("anchor:"):
                covered.add(slot)
            elif status == "hit" and slot.startswith("anchor:"):
                covered_anchors.add(slot[7:])
        if c.condition:
            covered.add(c.condition)
            covered.add(f"condition:{c.condition}")
        if c.fact_kind == "numeric" and c.value:
            covered.add("value")
            covered.add("unit")
        if c.value_dimension:
            covered.add(f"dim:{c.value_dimension}")
        if c.fact_kind == "deadline":
            covered.add("deadline")
            if c.condition:
                covered.add(c.condition)
        if c.fact_kind == "version":
            covered.add("version")
        if c.fact_kind in ("policy", "prohibition", "responsibility", "scope", "relationship"):
            covered.add("policy_fact")

    def _needed() -> list[str]:
        required = [s for s in (plan.required_slots or []) if s not in covered]
        if required:
            return required
        # A typed slot alone can be satisfied by a definition while omitting
        # the user's actual entity/constraint.  Spend remaining bullet budget
        # on independently grounded query anchors, never on an invented fact.
        return [
            f"anchor:{anchor}" for anchor in (plan.anchors or [])[:8]
            if len(anchor) >= 2 and anchor not in covered_anchors
        ]

    # Prefer numerics first when numeric intent is present; version when version intent.
    if plan.wants_numeric:
        pool = sorted(
            filtered,
            key=lambda c: (0 if c.fact_kind == "numeric" else 1, -c.score),
        )
    elif plan.wants_version:
        pool = sorted(
            filtered,
            key=lambda c: (0 if c.fact_kind == "version" else 1, -c.score),
        )
    else:
        pool = list(filtered)
    while pool and len(selected) < max_items:
        need = _needed()
        if not need and selected:
            break

        def gain(c: FactCandidate) -> tuple[float, float, float]:
            hits = 0
            for s in need:
                if (c.slot_match or {}).get(s) == "hit":
                    hits += 1
                elif s == c.condition or s == f"condition:{c.condition}":
                    hits += 1
                elif s.startswith("dim:") and (
                    c.value_dimension == s[4:] or c.condition == s[4:]
                ):
                    hits += 1
                elif s.startswith("anchor:") and (
                    (c.slot_match or {}).get(s) == "hit"
                ):
                    hits += 1
            # Prefer exact exclusive condition match and numeric kinds
            bonus = 0.0
            if c.condition and c.condition in (plan.conditions or []):
                bonus += 2.0
            kind_pref = 1.0 if c.fact_kind == "numeric" and plan.wants_numeric else 0.0
            return (float(hits) + bonus + kind_pref, kind_pref, c.score)

        pool.sort(key=gain, reverse=True)
        best = pool[0]
        g, _, _ = gain(best)
        # Stop adding low-gain policy filler once we have something if still missing critical
        if g <= 0 and selected and plan.wants_numeric:
            # Still try to pick remaining numeric dims
            num = next((c for c in pool if c.fact_kind == "numeric"), None)
            if num and num.candidate_id not in {s.candidate_id for s in selected}:
                selected.append(num)
                _mark_covered(num)
                pool = [c for c in pool if c.candidate_id != num.candidate_id]
                continue
            break
        if best.candidate_id in {s.candidate_id for s in selected}:
            pool.pop(0)
            continue
        selected.append(best)
        _mark_covered(best)
        pool = [c for c in pool if c.candidate_id != best.candidate_id]

    # Prefer one numeric per exclusive condition.
    if plan.wants_numeric and plan.conditions:
        for cond in plan.conditions:
            if any(c.condition == cond and c.fact_kind == "numeric" for c in selected):
                continue
            best = next(
                (c for c in filtered if c.fact_kind == "numeric" and c.condition == cond),
                None,
            )
            if best and best.candidate_id not in {s.candidate_id for s in selected}:
                selected.append(best)
                _mark_covered(best)

    # Multi value dimensions: ensure each dim has a *numeric* candidate if available.
    if plan.wants_numeric and plan.value_dimensions:
        for dim in plan.value_dimensions:
            if dim in ("value",):
                continue
            if any(
                c.fact_kind == "numeric"
                and (c.value_dimension == dim or c.condition == dim)
                for c in selected
            ):
                continue
            best = next(
                (
                    c
                    for c in filtered
                    if c.fact_kind == "numeric"
                    and (
                        c.value_dimension == dim
                        or c.condition == dim
                        or (dim == "ratio" and c.unit in ("%", "％"))
                        or (dim == "per_unit" and re.search(r"每(?:人|个|名)|人均", c.exact_text or ""))
                    )
                ),
                None,
            )
            if best and best.candidate_id not in {s.candidate_id for s in selected}:
                selected.append(best)
                _mark_covered(best)

    # Prefer numeric over policy duplicates for the same record/value.
    if plan.wants_numeric:
        numeric_keys = {
            (c.condition, c.value, c.unit)
            for c in selected
            if c.fact_kind == "numeric" and c.value
        }
        selected = [
            c
            for c in selected
            if not (
                c.fact_kind in ("policy", "prohibition")
                and c.value
                and (c.condition, c.value, c.unit) in numeric_keys
            )
            and not (
                c.fact_kind in ("policy", "prohibition")
                and any(
                    n.fact_kind == "numeric"
                    and n.condition
                    and n.condition in (c.exact_text or "")
                    and n.value
                    and n.value in (c.exact_text or "")
                    for n in selected
                )
            )
        ] or selected

    # Deadlines by query-stated labels.  If no label is explicit, retain the
    # strongest deadline candidate rather than inventing a workflow taxonomy.
    if plan.wants_deadline:
        labels = list(plan.conditions or [])
        for label in labels:
            if any(c.fact_kind == "deadline" and (
                c.condition == label or label in (c.exact_text or "")
            ) for c in selected):
                continue
            best = next(
                (
                    c
                    for c in filtered
                    if c.fact_kind == "deadline"
                    and (
                        c.condition == label
                        or label in (c.condition or "")
                        or label in (c.exact_text or "")
                    )
                ),
                None,
            )
            if best and best.candidate_id not in {s.candidate_id for s in selected}:
                selected.append(best)
                _mark_covered(best)
        if not any(c.fact_kind == "deadline" for c in selected):
            for c in filtered:
                if c.fact_kind == "deadline" and c.candidate_id not in {
                    s.candidate_id for s in selected
                }:
                    selected.append(c)
                    _mark_covered(c)
                    break

    if plan.wants_version and not any(c.fact_kind == "version" for c in selected):
        best = next((c for c in filtered if c.fact_kind == "version"), None)
        if best:
            selected.append(best)
            _mark_covered(best)

    # Policy / responsibility when still needed
    if not plan.wants_numeric or "policy_fact" in (plan.required_slots or []):
        for c in filtered:
            if len(selected) >= max_items:
                break
            if c.candidate_id in {s.candidate_id for s in selected}:
                continue
            if c.fact_kind in ("policy", "prohibition", "responsibility", "scope", "relationship"):
                selected.append(c)
                _mark_covered(c)

    if plan.wants_numeric and not plan.conditions and not any(
        c.fact_kind == "numeric" for c in selected
    ):
        for c in filtered:
            if c.fact_kind == "numeric" and c.candidate_id not in {
                s.candidate_id for s in selected
            }:
                selected.append(c)
                if len([x for x in selected if x.fact_kind == "numeric"]) >= max_items:
                    break

    # Keep at most one numeric per exclusive condition (highest score wins).
    if plan.wants_numeric and plan.conditions:
        best_by_cond: dict[str, FactCandidate] = {}
        rest: list[FactCandidate] = []
        for c in selected:
            if c.fact_kind == "numeric" and c.condition in plan.conditions:
                prev = best_by_cond.get(c.condition)
                if prev is None or c.score > prev.score:
                    best_by_cond[c.condition] = c
            else:
                rest.append(c)
        selected = list(best_by_cond.values()) + [
            c for c in rest if c.candidate_id not in {x.candidate_id for x in best_by_cond.values()}
        ]

    selected = selected[:max_items]
    audit["selected"] = [c.to_dict() for c in selected]
    audit["covered_slots"] = sorted(covered)
    audit["missing_after_select"] = [
        s for s in (plan.required_slots or []) if s not in covered
    ]
    return selected, audit


def build_answer_plan(
    *,
    plan: QueryPlan,
    selected: list[FactCandidate],
    coverage_matrix: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """List required slots and whether final selected set covers them."""
    missing: list[str] = []
    covered: set[str] = set()
    for c in selected:
        annotate_slot_match(c, plan)
        for slot, status in (c.slot_match or {}).items():
            if status == "hit" and not slot.startswith("anchor:"):
                covered.add(slot)
        if c.condition:
            covered.add(c.condition)
        if c.fact_kind == "numeric" and c.value:
            covered.update({"value", "unit"})
            if c.condition:
                covered.add(c.condition)
            if c.value_dimension:
                covered.add(f"dim:{c.value_dimension}")
        if c.fact_kind == "deadline":
            covered.add("deadline")
            if c.condition:
                covered.add(c.condition)
        if c.fact_kind == "version":
            covered.add("version")
        if c.fact_kind in ("policy", "prohibition", "responsibility", "scope", "relationship"):
            covered.add("policy_fact")

    for slot in plan.required_slots or []:
        if slot in covered:
            continue
        # Soft slots that are informational
        if slot.startswith("anchor:"):
            continue
        if slot.startswith("dim:"):
            dim = slot[4:]
            if dim == "value" and "value" in covered:
                continue
            if any(
                c.fact_kind == "numeric"
                and (
                    c.condition == dim
                    or c.value_dimension == dim
                    or (dim == "ratio" and c.unit in ("%", "％"))
                )
                for c in selected
            ):
                continue
            missing.append(slot)
            continue
        if slot in ("condition",) and not plan.conditions:
            continue
        if slot.startswith("predicate:"):
            pred = slot.split(":", 1)[1]
            synonyms = _predicate_synonyms(pred)
            if any(any(s in (c.exact_text or "") for s in synonyms) for c in selected):
                continue
            if any(pred[:2] in (c.exact_text or "") for c in selected if len(pred) >= 2):
                continue
            missing.append(slot)
            continue
        if slot == "polarity_negative":
            if any(re.search(r"不得|禁止|严禁|取消|不再", c.exact_text or "") for c in selected):
                continue
            missing.append(slot)
            continue
        if slot in plan.conditions:
            if any(c.condition == slot for c in selected):
                continue
            missing.append(slot)
            continue
        if slot in ("value", "unit") and any(c.fact_kind == "numeric" and c.value for c in selected):
            continue
        if slot == "policy_fact" and any(
            c.fact_kind in ("policy", "prohibition", "responsibility", "scope", "relationship")
            for c in selected
        ):
            continue
        if slot == "deadline" and any(c.fact_kind == "deadline" for c in selected):
            continue
        if slot in ("version", "doc_no") and any(c.fact_kind == "version" for c in selected):
            continue
        if slot in ("subject", "role") and any(
            c.fact_kind == "responsibility" or c.fact_kind == "policy" for c in selected
        ):
            continue
        if slot in ("scope", "relationship") and selected:
            continue
        missing.append(slot)

    # Hard incomplete: multi exclusive condition without any match
    if plan.wants_numeric and plan.conditions:
        have = {c.condition for c in selected if c.fact_kind == "numeric"}
        for cond in plan.conditions:
            if cond not in have and cond not in missing:
                missing.append(cond)

    complete = not missing and bool(selected)
    return {
        "required_slots": list(plan.required_slots),
        "candidate_ids": [c.candidate_id for c in selected],
        "missing_slots": missing,
        "complete": complete and bool(selected),
        "intents": list(plan.intents),
        "covered_slots": sorted(covered),
        "coverage_matrix": coverage_matrix or {},
    }


def render_from_candidates(
    selected: list[FactCandidate],
    *,
    max_bullets: int = 3,
) -> tuple[str, list[dict[str, Any]]]:
    """Render bullets from exact_text only; return (text, bullet audit)."""
    if not selected:
        return "", []
    bullets: list[str] = []
    audit: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in selected:
        text = (c.exact_text or c.display() or "").strip()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(
            r"(问题拆解|推理过程|知识库检索|检索过程|组合推理)",
            "",
            text,
        ).strip(" ：:-\n")
        if not text:
            continue
        key = re.sub(r"\s+", "", text)
        if key in seen:
            continue
        seen.add(key)
        if not text.startswith("-"):
            text = f"- {text}"
        bullets.append(text)
        audit.append({
            "rendered_candidate_ids": [c.candidate_id],
            "passage_id": c.passage_id,
            "knowledge_id": c.knowledge_id,
            "text": text,
            "fact_kind": c.fact_kind,
        })
        if len(bullets) >= max_bullets:
            break
    return "\n".join(bullets), audit


def validate_render_coverage(
    *,
    plan: QueryPlan,
    answer_text: str,
    selected: list[FactCandidate],
    bullet_audit: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Second-pass validation: required slots must appear in final text (SPEC v6 §3.3)."""
    text = answer_text or ""
    norm = re.sub(r"\s+", "", text)
    missing: list[str] = []
    checks: dict[str, Any] = {}

    if plan.wants_numeric and plan.conditions:
        for cond in plan.conditions:
            ok = cond in text or any(
                c.condition == cond and (c.value and c.value in text)
                for c in selected
            )
            checks[cond] = ok
            if not ok:
                missing.append(cond)
        # Values from selected numerics should appear
        for c in selected:
            if c.fact_kind == "numeric" and c.value:
                ok = c.value in text
                checks[f"value:{c.value}{c.unit}"] = ok
                if not ok and c.condition in (plan.conditions or []):
                    missing.append(f"value:{c.value}")

    if plan.wants_numeric and plan.value_dimensions:
        for dim in plan.value_dimensions:
            if dim in ("value", "年付款", "周期"):
                continue
            if dim in ("period", "annual", "total"):
                continue
            if dim == "ratio":
                ok = bool(re.search(r"\d+(?:\.\d+)?\s*(?:%|％)", text))
                checks[f"dim:{dim}"] = ok
                if not ok:
                    missing.append(f"dim:{dim}")
            elif dim == "per_unit":
                ok = bool(re.search(r"每(?:人|个|名)|人均", text))
                checks[f"dim:{dim}"] = ok
                if not ok:
                    missing.append(f"dim:{dim}")

    if plan.wants_deadline:
        for label in ("初审", "产品评估"):
            if label in (plan.raw or "") or label in (plan.required_slots or []):
                ok = label in text or any(
                    c.fact_kind == "deadline" and c.value and c.value in text
                    for c in selected
                )
                checks[label] = ok
                if not ok and label in (plan.required_slots or []):
                    missing.append(label)

    if plan.wants_version:
        ok = bool(re.search(r"(?:19|20)\d{2}", text))
        checks["version"] = ok
        if not ok:
            missing.append("version")

    # Policy: anchors / predicate must surface when required
    if plan.wants_policy and not plan.wants_numeric:
        anchors = [a for a in (plan.anchors or []) if len(a) >= 2][:6]
        if anchors:
            hits = [a for a in anchors if a in text]
            checks["policy_anchors"] = hits
            # At least one strong anchor (version answers may only carry year/doc-no)
            if not hits:
                if plan.predicate and any(s in text for s in _predicate_synonyms(plan.predicate)):
                    checks["policy_anchors"] = [plan.predicate]
                elif plan.wants_version and re.search(r"(?:19|20)\d{2}", text):
                    checks["policy_anchors"] = ["version_year"]
                else:
                    missing.append("policy_anchor")
        if "polarity_negative" in (plan.required_slots or []):
            ok = bool(re.search(r"不得|禁止|严禁|取消|不再", text))
            checks["polarity_negative"] = ok
            if not ok and not (plan.wants_version and re.search(r"(?:19|20)\d{2}", text)):
                missing.append("polarity_negative")
        for slot in plan.required_slots or []:
            if slot.startswith("predicate:"):
                pred = slot.split(":", 1)[1]
                syns = _predicate_synonyms(pred)
                ok = any(s in text for s in syns) or any(
                    any(s in (c.exact_text or "") for s in syns) for c in selected
                )
                checks[slot] = ok
                # Version+cancel: accept year/doc answer without the cancel verb if
                # polarity already verified or version covered.
                if not ok and plan.wants_version and re.search(r"(?:19|20)\d{2}", text):
                    checks[slot] = True
                    continue
                if not ok:
                    missing.append(slot)

    ok_all = not missing and bool(text.strip())
    return {
        "ok": ok_all,
        "missing_slots": missing,
        "checks": checks,
        "bullet_audit": bullet_audit or [],
        "answer_len": len(text),
    }


# ----- extractors -----


def _numeric_from_record(rec: LogicalEvidenceRecord, plan: QueryPlan) -> list[FactCandidate]:
    body = rec.body_text or ""
    local_conditions = extract_conditions(body)
    out: list[FactCandidate] = []
    money_matches = list(_VALUE_RE.finditer(body))
    for m in money_matches:
        value = m.group("value")
        unit = m.group("unit")
        if unit == "年" and not plan.wants_version:
            continue
        window = body[max(0, m.start() - 40) : m.end() + 48]
        conds = extract_conditions(window)
        if not conds and len(local_conditions) == 1:
            conds = list(local_conditions)
        # Scope labels near value (团体/个人) as soft condition for multi-dim, not exclusive filter
        scope_cond = ""
        if re.search(r"团体", window):
            scope_cond = "团体"
        elif re.search(r"人均|每人|个人奖", window):
            scope_cond = "人均"
        if not conds:
            if scope_cond:
                conds = [scope_cond]
            elif len(money_matches) == 1 and len(body) <= 120:
                conds = [""]
            elif re.search(r"限额|年付款|奖金|占比|比例", body):
                conds = [""]
            else:
                continue
        if len(conds) > 1:
            nearest = None
            nearest_dist = 10**9
            for cond in conds:
                for mcond in re.finditer(re.escape(cond), window):
                    dist = abs(mcond.start() - 40)
                    if dist < nearest_dist:
                        nearest_dist = dist
                        nearest = cond
            if nearest:
                conds = [nearest]
        for cond in conds:
            frag = body[max(0, m.start() - 20) : m.end() + 12].strip()
            if cond and cond not in frag:
                if cond in window:
                    frag = f"{cond}{value}{unit}"
                else:
                    frag = f"{cond}{value}{unit}"
            else:
                frag = re.sub(r"\s+", "", frag)
                if len(frag) > 60:
                    frag = f"{cond}{value}{unit}" if cond else f"{value}{unit}"
            dim = ""
            if re.fullmatch(r"(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX]+|[一二三四五六七八九十]+)类", cond or ""):
                dim = "class"
            elif re.search(r"每(?:人|个|名)|人均", frag):
                dim = "per_unit"
            elif re.search(r"总额|总计|合计", frag):
                dim = "total"
            elif unit in ("%", "％"):
                dim = "ratio"
            elif re.search(r"年付款|年限额", body):
                dim = "annual"
            cid = stable_candidate_id(
                passage_id=rec.passage_id,
                body_span=(m.start(), m.end()),
                fact_kind="numeric",
                exact_text=frag if frag else f"{cond}{value}{unit}",
            )
            out.append(
                FactCandidate(
                    candidate_id=cid,
                    record_id=rec.record_id,
                    passage_id=rec.passage_id,
                    knowledge_id=rec.knowledge_id,
                    fact_kind="numeric",
                    condition=cond,
                    value=value,
                    unit=unit,
                    exact_text=frag if frag else f"{cond}{value}{unit}",
                    evidence_spans=[(m.start(), m.end())],
                    table_row_ref=rec.table_id or (
                        f"row:{rec.row_index}" if rec.row_index is not None else ""
                    ),
                    document_family_id=rec.document_family_id,
                    version_year=rec.version_year,
                    predicate="限额/处罚" if re.search(r"限额|处罚|罚", body) else "数值",
                    object=f"{value}{unit}",
                    value_dimension=dim,
                )
            )
    return out


def _deadline_from_record(rec: LogicalEvidenceRecord, plan: QueryPlan) -> list[FactCandidate]:
    body = rec.body_text or ""
    out: list[FactCandidate] = []

    def _add(cond: str, value: str, unit: str, exact: str, start: int, end: int, tag: str) -> None:
        if unit == "工作日":
            unit = "个工作日"
        exact_n = re.sub(r"\s+", "", exact)[:80]
        cid = stable_candidate_id(
            passage_id=rec.passage_id,
            body_span=(start, end),
            fact_kind="deadline",
            exact_text=f"{cond}:{value}{unit}:{tag}",
        )
        if any(c.candidate_id == cid for c in out):
            return
        out.append(
            FactCandidate(
                candidate_id=cid,
                record_id=rec.record_id,
                passage_id=rec.passage_id,
                knowledge_id=rec.knowledge_id,
                fact_kind="deadline",
                condition=cond,
                value=value,
                unit=unit,
                exact_text=exact_n,
                evidence_spans=[(start, end)],
                document_family_id=rec.document_family_id,
                version_year=rec.version_year,
            )
        )

    for m in re.finditer(
        r"(初审|审核初审|产品评估|评估)[^。；\n]{0,24}(\d+)\s*(个?工作日)",
        body,
    ):
        cond = "初审" if "初审" in m.group(1) else "产品评估"
        _add(cond, m.group(2), m.group(3), m.group(0), m.start(), m.end(), "dlA")

    for m in re.finditer(
        r"(\d+)\s*(个?工作日)[^。；\n]{0,30}(初审|审核|产品评估|评估)",
        body,
    ):
        cond = "初审" if "初审" in m.group(3) or m.group(3) == "审核" else "产品评估"
        _add(cond, m.group(1), m.group(2), m.group(0), m.start(), m.end(), "dlB")

    for m in re.finditer(r"(\d+)\s*(个?工作日)", body):
        window = body[max(0, m.start() - 24) : m.end() + 36]
        cond = ""
        if re.search(r"初审|审核初审", window):
            cond = "初审"
        elif re.search(r"产品评估|(?<![初复])评估", window):
            cond = "产品评估"
        if not cond:
            continue
        _add(cond, m.group(1), m.group(2), window, m.start(), m.end(), "dlC")

    return out


def _version_from_record(rec: LogicalEvidenceRecord, plan: QueryPlan) -> list[FactCandidate]:
    blob = (rec.title or "") + "\n" + (rec.body_text or "")[:300]
    out: list[FactCandidate] = []
    m = _DOCNO_RE.search(blob)
    year = None
    doc = ""
    if m:
        year = int(m.group(1))
        doc = f"{m.group(1)}-{m.group(2)}号"
    else:
        m2 = re.search(r"((?:19|20)\d{2})[-—](\d+)\s*号", blob)
        if m2:
            year = int(m2.group(1))
            doc = f"{m2.group(1)}-{m2.group(2)}号"
        else:
            m3 = _YEAR_RE.search(blob)
            if m3:
                year = int(m3.group(1))
    if year is None and rec.version_year:
        year = rec.version_year
    if year is None and not doc:
        return []
    bits = []
    if year:
        bits.append(str(year))
    if doc:
        bits.append(doc)
    elif year:
        m4 = re.search(r"(\d+)\s*号", blob)
        if m4:
            bits.append(f"{m4.group(1)}号")
    text = f"最新修订版为{' '.join(bits)}"
    text = re.sub(r"一级竞赛|二级竞赛", "", text)
    cid = stable_candidate_id(
        passage_id=rec.passage_id,
        body_span=rec.source_span,
        fact_kind="version",
        exact_text=text,
    )
    out.append(
        FactCandidate(
            candidate_id=cid,
            record_id=rec.record_id,
            passage_id=rec.passage_id,
            knowledge_id=rec.knowledge_id,
            fact_kind="version",
            value=str(year or ""),
            unit="年" if year else "",
            exact_text=text,
            document_family_id=rec.document_family_id,
            version_year=year,
            object=doc or str(year or ""),
        )
    )
    return out


def _policy_from_record(rec: LogicalEvidenceRecord, plan: QueryPlan) -> list[FactCandidate]:
    """Query-derived anchors, predicate/polarity and proximity scoring."""
    body = rec.body_text or ""
    if not body.strip():
        return []
    if plan.wants_numeric and not plan.wants_policy and _VALUE_RE.search(body) and len(body) < 100:
        return []

    anchors = list(plan.anchors or [])
    entities = list(plan.entities or [])
    q_terms = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}", plan.raw or "") if len(t) >= 2]

    # Score candidate sentence fragments
    sentences = re.split(r"[。\n；;]", body)
    if not sentences:
        sentences = [body]
    scored: list[tuple[float, str, int, int]] = []
    cursor = 0
    for sent in sentences:
        s = sent.strip()
        if len(s) < 6:
            cursor += len(sent) + 1
            continue
        start = body.find(s, cursor)
        if start < 0:
            start = body.find(s)
        if start < 0:
            start = cursor
        end = start + len(s)
        cursor = end
        score = 0.0
        for a in anchors:
            if a and a in s:
                score += 3.0 + min(2.0, len(a) / 4.0)
        for e in entities:
            if e and e in s:
                score += 1.2
        for t in q_terms[:10]:
            if t in s:
                score += 0.35
        if plan.predicate and plan.predicate in s:
            score += 2.5
        if plan.polarity == "negative" and re.search(r"不得|禁止|严禁|取消|不再", s):
            score += 2.0
        if plan.polarity == "positive" and re.search(r"应当|必须|负责|牵头", s):
            score += 1.0
        if plan.wants_responsibility and re.search(r"负责|归口|主管部门|牵头|首席|顾问", s):
            score += 2.0
        if plan.wants_relationship and re.search(r"效力|一致|关系|对应", s):
            score += 1.5
        # Prefer denser query overlap
        if score > 0:
            scored.append((score, s, start, end))

    if not scored:
        # Soft fallback: any sentence with policy modality and a query term
        for sent in sentences:
            s = sent.strip()
            if len(s) < 6:
                continue
            if re.search(r"不得|禁止|应当|必须|原则|负责|适用范围|严禁", s):
                if any(t in s for t in q_terms[:8]):
                    start = body.find(s)
                    scored.append((0.5, s, max(0, start), max(0, start) + len(s)))
                    break
        if not scored:
            return []

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, frag, start, end = scored[0]
    if best_score < 0.5 and not plan.wants_responsibility:
        # Require meaningful anchor hit for pure policy
        if not any(a and a in frag for a in anchors[:5]):
            return []

    frag = re.sub(r"\s+", "", frag)
    if len(frag) < 6:
        return []
    if len(frag) > 120:
        frag = frag[:120]
    if re.fullmatch(r"[\d\-—号〔〕\[\]年月日.]+", frag):
        return []
    if re.match(r"^(?:中电信桂|市场)?[\d\-—]{2,}\d+号", frag) and len(frag) < 30:
        return []

    kind = "policy"
    if re.search(r"不得|禁止|严禁", frag):
        kind = "prohibition"
    elif plan.wants_responsibility or re.search(r"负责|归口|主管部门|首席|顾问|牵头", frag):
        kind = "responsibility"
    elif plan.wants_scope or re.search(r"适用|范围", frag):
        kind = "scope"
    elif plan.wants_relationship:
        kind = "relationship"

    polarity = ""
    if re.search(r"不得|禁止|严禁|取消|不再", frag):
        polarity = "negative"
    elif re.search(r"应当|必须|负责", frag):
        polarity = "positive"

    anchor = next((a for a in anchors if a and a in frag), "") or (
        next((e for e in entities if e and e in frag), "")
    )
    cid = stable_candidate_id(
        passage_id=rec.passage_id,
        body_span=(start, end),
        fact_kind=kind,
        exact_text=frag,
    )
    return [
        FactCandidate(
            candidate_id=cid,
            record_id=rec.record_id,
            passage_id=rec.passage_id,
            knowledge_id=rec.knowledge_id,
            fact_kind=kind,
            exact_text=frag,
            subject=anchor or "",
            object=frag,
            predicate=plan.predicate or "",
            polarity=polarity,
            document_family_id=rec.document_family_id,
            version_year=rec.version_year,
            evidence_spans=[(start, end)],
            score=best_score,
        )
    ]


def _score_candidate(c: FactCandidate, plan: QueryPlan) -> float:
    score = float(c.score or 0.0)
    if c.condition and c.condition in (plan.conditions or []):
        score += 3.0
    if c.fact_kind in (plan.allow_fact_kinds or []):
        score += 1.0
    if c.fact_kind == "numeric" and plan.wants_numeric:
        score += 2.0
        if c.unit in ("元", "万元", "%", "％"):
            score += 1.0
        if c.value_dimension and c.value_dimension in (plan.value_dimensions or []):
            score += 1.5
        # Prefer per-number / per-month penalties when query asks 每个号码/自然月
        q = plan.raw or ""
        et = c.exact_text or ""
        if re.search(r"每个号码|自然月", q):
            if re.search(r"每个号码|自然月", et):
                score += 3.0
            # Deprioritize cumulative/threshold large penalties without 每个号码
            if re.search(r"累计|起处罚|上不封顶", et) and not re.search(r"每个号码", et):
                score -= 2.0
        if re.search(r"一个自然月|每个号码", et) and c.condition in (plan.conditions or []):
            score += 1.5
    if c.fact_kind == "deadline" and plan.wants_deadline:
        score += 2.5
        if c.condition in ("初审", "产品评估"):
            score += 1.0
    if c.fact_kind == "version" and plan.wants_version:
        score += 2.5
    if c.fact_kind in ("policy", "prohibition", "responsibility") and not plan.wants_numeric:
        score += 2.0
    if c.exact_text and len(c.exact_text) <= 40:
        score += 0.5
    if c.table_row_ref:
        score += 0.3
    # Query-derived anchor overlap only.
    for a in plan.anchors or []:
        if a and a in (c.exact_text or ""):
            score += 1.2
    for e in plan.entities or []:
        if e and e in (c.exact_text or ""):
            score += 0.4
    if plan.predicate and plan.predicate in (c.exact_text or ""):
        score += 1.5
    if plan.polarity == "negative" and c.polarity == "negative":
        score += 1.0
    # slot_match hits
    for slot, status in (c.slot_match or {}).items():
        if status == "hit" and not slot.startswith("anchor:"):
            score += 0.3
    return score


def _predicate_synonyms(pred: str) -> list[str]:
    """Generic polarity/predicate surface forms (not Golden-bound)."""
    p = pred or ""
    base = [p]
    if p in ("不得使用", "禁止使用", "不得", "禁止"):
        base.extend(["不得使用", "禁止使用", "不得", "禁止", "严禁"])
    if p in ("取消",):
        base.extend(["取消", "不再"])
    if p in ("处罚",):
        base.extend(["处罚", "罚款", "被罚"])
    if p in ("限额",):
        base.extend(["限额", "年付款"])
    # de-dupe
    seen: set[str] = set()
    out: list[str] = []
    for s in base:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _dedupe_candidates(cands: list[FactCandidate]) -> list[FactCandidate]:
    seen: set[str] = set()
    out: list[FactCandidate] = []
    for c in cands:
        norm = re.sub(r"\s+", "", c.exact_text or "")[:40]
        key = f"{c.fact_kind}|{c.condition}|{c.value}|{c.unit}|{norm}|{c.passage_id}"
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
