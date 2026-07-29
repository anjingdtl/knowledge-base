"""Structured claim / FactCandidate draft → ground → short render (SPEC v4 §B + v5 §2 + v6)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from src.answering.evidence_groups import (
    filter_candidates_to_groups,
    filter_records_to_groups,
    resolve_evidence_groups,
)
from src.answering.fact_candidates import (
    FactCandidate,
    build_answer_plan,
    extract_candidates_from_evidence,
    extract_candidates_from_records,
    render_from_candidates,
    select_fact_candidates,
    validate_render_coverage,
)
from src.answering.numeric_triples import (
    extract_query_slots,
    select_answer_triples,
    triples_from_evidence_rows,
)
from src.answering.passage_evidence import (
    PassageEvidence,
    ensure_passage_trace,
    normalize_evidence_list,
    passage_map,
)
from src.answering.query_planner import plan_query

_PROCESS_MARKERS = re.compile(
    r"(问题拆解|推理过程|知识库检索|检索过程|组合推理|建议|总结|"
    r"若你实际想问|可能|按此推算|可参照但不等于|chain[- ]?of[- ]?thought)",
    re.I,
)

ANSWER_VALIDATION_REASONS = frozenset({
    "retrieval_gate_rejected",
    "direct_slot_not_satisfied",
    "no_fact_candidate",
    "table_structure_ambiguous",
    "answer_plan_incomplete",
    "claim_grounding_failed",
    "passage_trace_failed",
    "citation_allowlist_failed",
    "no_direct_slot_evidence",
    "no_matching_numeric_triple",
    "no_grounded_claims",
    "empty_render",
    "render_validation_failed",
    "structured_claim_answer",
})


@dataclass
class ClaimDraft:
    text: str
    evidence_passage_ids: list[str] = field(default_factory=list)
    fact_type: str = "other"
    condition: str = ""
    candidate_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "evidence_passage_ids": list(self.evidence_passage_ids),
            "fact_type": self.fact_type,
            "condition": self.condition,
            "candidate_id": self.candidate_id,
        }


def parse_claim_drafts(raw: str | dict | list | None) -> tuple[list[ClaimDraft], str | None]:
    """Parse structured claim drafts. Reject process prose."""
    if raw is None or raw == "":
        return [], "empty"
    data: Any = raw
    if isinstance(raw, str):
        s = raw.strip()
        if _PROCESS_MARKERS.search(s) and not s.startswith("{"):
            return [], "process_prose"
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return [], "invalid_json"

    claims_raw: list[Any]
    if isinstance(data, dict):
        claims_raw = data.get("claims") if isinstance(data.get("claims"), list) else []
        if not claims_raw and data.get("text"):
            claims_raw = [data]
    elif isinstance(data, list):
        claims_raw = data
    else:
        return [], "invalid_schema"

    drafts: list[ClaimDraft] = []
    for c in claims_raw:
        if not isinstance(c, dict):
            continue
        text = str(c.get("text") or "").strip()
        if not text:
            continue
        if _PROCESS_MARKERS.search(text):
            text = _PROCESS_MARKERS.sub("", text).strip()
            if not text:
                continue
        eids = c.get("evidence_passage_ids") or c.get("passage_ids") or []
        if isinstance(eids, str):
            eids = [eids]
        eids = [str(x).strip() for x in eids if str(x).strip()]
        if not eids:
            continue
        drafts.append(ClaimDraft(
            text=text,
            evidence_passage_ids=eids,
            fact_type=str(c.get("fact_type") or "other"),
            condition=str(c.get("condition") or ""),
            candidate_id=str(c.get("candidate_id") or ""),
        ))
    if not drafts:
        return [], "no_valid_claims"
    return drafts, None


def ground_claims(
    drafts: list[ClaimDraft],
    *,
    evidence: list[PassageEvidence],
    question: str,
) -> tuple[list[ClaimDraft], list[dict[str, Any]]]:
    """Keep only claims supported by their declared evidence passages (body only)."""
    pmap = passage_map(evidence)
    for e in evidence:
        e.ensure_body()
    # SPEC v6: use typed exclusive conditions only (scope words like 个人 are not conditions).
    plan = plan_query(question)
    conditions = list(plan.conditions or [])
    kept: list[ClaimDraft] = []
    audit: list[dict[str, Any]] = []

    for d in drafts:
        if not d.evidence_passage_ids:
            audit.append({"claim": d.to_dict(), "reason": "missing_evidence_ids"})
            continue
        unknown = [pid for pid in d.evidence_passage_ids if pid not in pmap]
        if unknown:
            audit.append({
                "claim": d.to_dict(),
                "reason": "evidence_id_not_in_snapshot",
                "unknown": unknown,
            })
            continue
        blobs = " ".join(
            (pmap[pid].body_text or pmap[pid].text or "")
            for pid in d.evidence_passage_ids
        )
        if d.fact_type == "version":
            blobs = " ".join(
                ((pmap[pid].title or "") + " " + (pmap[pid].body_text or pmap[pid].text or ""))
                for pid in d.evidence_passage_ids
            )
        blob_norm = re.sub(r"\s+", "", blobs)
        claim_norm = re.sub(r"\s+", "", d.text)
        nums = re.findall(
            r"\d+(?:\.\d+)?\s*(?:万元|亿|元|%|％|个工作日|工作日|天|个)",
            d.text,
        )
        ok = True
        if d.fact_type == "version":
            years = re.findall(r"(?:19|20)\d{2}", d.text)
            nos = re.findall(r"(\d+)\s*号", d.text)
            if years and not any(y in blob_norm for y in years):
                ok = False
                audit.append({"claim": d.to_dict(), "reason": "version_year_not_in_evidence"})
            if nos and not any(n + "号" in blob_norm or n in blob_norm for n in nos):
                ok = False
                audit.append({"claim": d.to_dict(), "reason": "version_docno_not_in_evidence"})
            if not years and not nos:
                ok = False
                audit.append({"claim": d.to_dict(), "reason": "version_claim_empty"})
        elif nums:
            for n in nums:
                nn = re.sub(r"\s+", "", n)
                if nn not in blob_norm and nn.replace("元", "") not in blob_norm.replace("元", ""):
                    ok = False
                    audit.append({
                        "claim": d.to_dict(),
                        "reason": "numeric_not_in_evidence",
                        "value": n,
                    })
                    break
        else:
            if len(claim_norm) >= 4:
                hits = sum(
                    1 for i in range(0, max(1, len(claim_norm) - 3))
                    if claim_norm[i:i + 4] in blob_norm
                )
                if hits < max(1, (len(claim_norm) - 3) // 4):
                    ok = False
                    audit.append({"claim": d.to_dict(), "reason": "text_not_grounded"})
        if not ok:
            continue

        # Only exclusive typed conditions gate drafts (not scope/selector).
        exclusive = {"涉诈", "涉骚扰", "II类", "III类", "I类", "区外", "区内"}
        if conditions and d.condition and d.condition in exclusive and d.condition not in conditions:
            # Allow class limits when query asked for account limits without naming class.
            if not (
                d.condition in ("I类", "II类", "III类")
                and re.search(r"年付款|支付账户|账户余额", question or "")
            ):
                audit.append({
                    "claim": d.to_dict(),
                    "reason": "condition_not_in_query",
                })
                continue
        if conditions:
            other = [c for c in exclusive if c in d.text and c not in conditions]
            # Multi-class account answers may mention both II/III even if query only said 个人.
            if other and not any(c in d.text for c in conditions):
                if not (
                    set(other) <= {"I类", "II类", "III类"}
                    and re.search(r"年付款|支付账户|账户余额", question or "")
                ):
                    audit.append({
                        "claim": d.to_dict(),
                        "reason": "exclusive_condition_mismatch",
                        "other": other,
                    })
                    continue
        kept.append(d)
        audit.append({"claim": d.to_dict(), "reason": "kept"})
    return kept, audit


def render_short_answer(claims: list[ClaimDraft], *, max_bullets: int = 3) -> str:
    if not claims:
        return ""
    bullets: list[str] = []
    for c in claims[:max_bullets]:
        t = re.sub(r"\s+", " ", (c.text or "").strip())
        t = _PROCESS_MARKERS.sub("", t).strip(" ：:-\n")
        if not t:
            continue
        if not t.startswith("-"):
            t = f"- {t}"
        bullets.append(t)
    return "\n".join(bullets)


def rule_extract_claims(
    question: str,
    evidence: list[PassageEvidence],
) -> list[ClaimDraft]:
    """Deterministic claim extractor via FactCandidate pipeline (SPEC v5/v6)."""
    if not evidence:
        return []
    cands, plan, records = extract_candidates_from_evidence(
        [e.to_row() for e in evidence],
        question=question,
    )
    resolution = resolve_evidence_groups(
        [e.to_row() for e in evidence],
        question=question,
        records=records,
        subqueries=plan.subqueries,
    )
    group_kids = set()
    for g in resolution.groups:
        if g.group_id == resolution.primary_group_id:
            if g.knowledge_id:
                group_kids.add(g.knowledge_id)
    selected, _audit = select_fact_candidates(
        cands,
        plan=plan,
        primary_group_id=resolution.primary_group_id,
        group_knowledge_ids=group_kids or None,
    )
    drafts: list[ClaimDraft] = []
    for c in selected:
        if c.unstructured_rejected or not (c.exact_text or c.display()):
            continue
        drafts.append(ClaimDraft(
            text=c.exact_text or c.display(),
            evidence_passage_ids=[c.passage_id] if c.passage_id else [],
            fact_type=c.fact_kind if c.fact_kind != "prohibition" else "policy",
            condition=c.condition,
            candidate_id=c.candidate_id,
        ))
    seen: set[str] = set()
    uniq: list[ClaimDraft] = []
    for d in drafts:
        key = re.sub(r"\s+", "", d.text)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(d)
    return uniq[:5]


def filter_latest_family_evidence(
    evidence: list[PassageEvidence],
    *,
    question: str,
) -> list[PassageEvidence]:
    if not re.search(r"最新|修订版|现行", question or ""):
        return evidence
    from src.services.version_rank import family_key_of, extract_version_year

    rows = [e.to_row() for e in evidence]
    family_newest: dict[str, int] = {}
    for r in rows:
        fk = family_key_of(r) or str(r.get("document_family_id") or "")
        y = extract_version_year(r)
        if fk and y is not None:
            family_newest[fk] = max(family_newest.get(fk, 0), y)
    kept: list[PassageEvidence] = []
    for e, r in zip(evidence, rows):
        fk = family_key_of(r) or e.document_family_id
        y = extract_version_year(r)
        newest = family_newest.get(fk)
        if fk and y is not None and newest is not None and y < newest:
            continue
        kept.append(e)
    return kept or evidence


def repair_passage_ids(
    candidates: list[FactCandidate],
    evidence: list[PassageEvidence],
) -> tuple[list[FactCandidate], list[dict[str, Any]]]:
    """Fill missing passage_id from unique snapshot source when possible (SPEC v6 §2.2)."""
    pmap = passage_map(evidence)
    by_kid: dict[str, list[PassageEvidence]] = {}
    for e in evidence:
        if e.knowledge_id:
            by_kid.setdefault(e.knowledge_id, []).append(e)
    audit: list[dict[str, Any]] = []
    out: list[FactCandidate] = []
    for c in candidates:
        if c.passage_id and c.passage_id in pmap:
            out.append(c)
            continue
        if c.passage_id and c.passage_id not in pmap:
            # Try repair via knowledge_id unique mapping
            opts = by_kid.get(c.knowledge_id or "", [])
            if len(opts) == 1 and opts[0].passage_id:
                c.passage_id = opts[0].passage_id
                c.trace_repaired = True
                audit.append({
                    "candidate_id": c.candidate_id,
                    "action": "trace_repaired",
                    "passage_id": c.passage_id,
                })
                out.append(c)
                continue
            audit.append({
                "candidate_id": c.candidate_id,
                "action": "missing_passage_id",
                "reason": "passage_not_in_snapshot",
            })
            continue
        # Empty passage_id
        opts = by_kid.get(c.knowledge_id or "", [])
        if len(opts) == 1 and opts[0].passage_id:
            c.passage_id = opts[0].passage_id
            c.trace_repaired = True
            audit.append({
                "candidate_id": c.candidate_id,
                "action": "trace_repaired",
                "passage_id": c.passage_id,
            })
            out.append(c)
        else:
            audit.append({
                "candidate_id": c.candidate_id,
                "action": "missing_passage_id",
                "reason": "no_unique_source_mapping",
            })
    return out, audit


def structured_answer_from_evidence(
    *,
    question: str,
    evidence_rows: list[dict[str, Any]] | None,
    llm_json: str | None = None,
    prefer_latest_family: bool = False,
    require_passage: bool = False,
) -> dict[str, Any]:
    """Full FactCandidate → group → cover → render → validate path."""
    evidence = normalize_evidence_list(evidence_rows)
    for e in evidence:
        e.ensure_body()
    if prefer_latest_family or re.search(r"最新|修订版|现行", question or ""):
        evidence = filter_latest_family_evidence(evidence, question=question)

    warnings: list[str] = []
    numeric_audit: dict[str, Any] = {}
    fact_audit: dict[str, Any] = {}
    answer_plan: dict[str, Any] = {}
    group_audit: dict[str, Any] = {}
    render_validation: dict[str, Any] = {}

    if require_passage and evidence:
        ok, reason = ensure_passage_trace([e.to_row() for e in evidence], require_passage=True)
        if not ok:
            return _no_answer(
                reason="passage_trace_failed",
                warnings=warnings + [f"passage_trace:{reason}"],
                answer_validation_decision="passage_trace_failed",
            )

    if not evidence or not _evidence_supports_query(question, evidence):
        return _no_answer(
            reason="direct_slot_not_satisfied",
            warnings=warnings,
            answer_validation_decision="direct_slot_not_satisfied",
        )

    plan = plan_query(question)
    from src.answering.logical_evidence import records_from_evidence_list

    records = records_from_evidence_list([e.to_row() for e in evidence])
    resolution = resolve_evidence_groups(
        [e.to_row() for e in evidence],
        question=question,
        records=records,
        subqueries=plan.subqueries,
    )
    group_audit = resolution.to_dict()
    records_g = filter_records_to_groups(records, resolution, allow_secondary=bool(plan.subqueries))
    cands = extract_candidates_from_records(records_g, plan=plan)

    # Tag group_id on candidates
    kid_to_gid = {g.knowledge_id: g.group_id for g in resolution.groups if g.knowledge_id}
    for c in cands:
        c.group_id = kid_to_gid.get(c.knowledge_id, "")

    cands, repair_audit = repair_passage_ids(cands, evidence)
    if repair_audit:
        warnings.append(f"passage_repair:{len(repair_audit)}")

    group_kids = {
        g.knowledge_id
        for g in resolution.groups
        if g.group_id == resolution.primary_group_id and g.knowledge_id
    }
    # Multi-subquery may include secondary
    if plan.subqueries:
        for g in resolution.groups:
            if g.group_id in resolution.secondary_group_ids and g.knowledge_id:
                group_kids.add(g.knowledge_id)

    selected, fact_audit = select_fact_candidates(
        cands,
        plan=plan,
        primary_group_id=resolution.primary_group_id,
        group_knowledge_ids=group_kids or None,
    )
    fact_audit["evidence_groups"] = group_audit
    fact_audit["passage_repair"] = repair_audit

    numeric_audit = {
        "query_slots": extract_query_slots(question),
        "fact_plan": plan.to_dict(),
        "kept": [
            {
                "condition": c.condition,
                "value": c.value,
                "unit": c.unit,
                "evidence_passage_id": c.passage_id,
                "record_id": c.record_id,
                "exact_text": c.exact_text,
                "table_row_ref": c.table_row_ref,
                "value_dimension": c.value_dimension,
                "group_id": c.group_id,
            }
            for c in selected
            if c.fact_kind in ("numeric", "deadline")
        ],
        "dropped": fact_audit.get("dropped") or [],
        "table_structure_ambiguous": bool(fact_audit.get("table_structure_ambiguous")),
        "logical_record_count": len(records_g),
        "candidate_count": len(cands),
    }

    if fact_audit.get("table_structure_ambiguous") and plan.wants_numeric:
        return _no_answer(
            reason="table_structure_ambiguous",
            warnings=warnings,
            numeric_fact_audit=numeric_audit,
            fact_candidate_audit=fact_audit,
            answer_validation_decision="table_structure_ambiguous",
            evidence_groups=group_audit,
        )

    drafts: list[ClaimDraft] = []
    if llm_json:
        parsed, err = parse_claim_drafts(llm_json)
        if err:
            warnings.append(f"claim_parse:{err}")
        else:
            drafts = parsed

    if not drafts:
        for c in selected:
            if c.unstructured_rejected:
                continue
            text = c.exact_text or c.display()
            if not text:
                continue
            drafts.append(ClaimDraft(
                text=text,
                evidence_passage_ids=[c.passage_id] if c.passage_id else [],
                fact_type=c.fact_kind if c.fact_kind != "prohibition" else "policy",
                condition=c.condition,
                candidate_id=c.candidate_id,
            ))

    if plan.wants_numeric and plan.conditions:
        if not any(d.fact_type == "numeric" for d in drafts) and not selected:
            return _no_answer(
                reason="no_matching_numeric_triple",
                warnings=warnings,
                numeric_fact_audit=numeric_audit,
                fact_candidate_audit=fact_audit,
                answer_validation_decision="no_fact_candidate",
                evidence_groups=group_audit,
            )

    kept, ground_audit = ground_claims(drafts, evidence=evidence, question=question)

    if not kept and selected:
        for c in selected:
            if c.unstructured_rejected:
                continue
            if not c.passage_id:
                continue
            kept.append(ClaimDraft(
                text=c.exact_text or c.display(),
                evidence_passage_ids=[c.passage_id],
                fact_type=c.fact_kind if c.fact_kind != "prohibition" else "policy",
                condition=c.condition,
                candidate_id=c.candidate_id,
            ))
        kept, ground_audit2 = ground_claims(kept, evidence=evidence, question=question)
        ground_audit.extend(ground_audit2)

    if not kept:
        return _no_answer(
            reason="no_fact_candidate",
            warnings=warnings,
            numeric_fact_audit=numeric_audit,
            claim_audit=ground_audit,
            fact_candidate_audit=fact_audit,
            answer_validation_decision="no_fact_candidate",
            evidence_groups=group_audit,
        )

    answer_plan = build_answer_plan(
        plan=plan,
        selected=selected or [
            FactCandidate(
                candidate_id=d.candidate_id or f"draft:{i}",
                record_id="",
                passage_id=d.evidence_passage_ids[0] if d.evidence_passage_ids else "",
                knowledge_id="",
                fact_kind=d.fact_type,
                condition=d.condition,
                exact_text=d.text,
            )
            for i, d in enumerate(kept)
        ],
        coverage_matrix=fact_audit.get("coverage_matrix"),
    )

    # Incomplete exclusive multi-condition → refuse
    if plan.wants_numeric and plan.conditions and answer_plan.get("missing_slots"):
        missing_conds = [s for s in answer_plan["missing_slots"] if s in plan.conditions]
        if missing_conds and not any(d.condition in plan.conditions for d in kept):
            return _no_answer(
                reason="answer_plan_incomplete",
                warnings=warnings,
                numeric_fact_audit=numeric_audit,
                claim_audit=ground_audit,
                fact_candidate_audit=fact_audit,
                answer_plan=answer_plan,
                answer_validation_decision="answer_plan_incomplete",
                evidence_groups=group_audit,
            )

    # Prefer candidate renderer (coverage-ordered selection already done)
    answer = ""
    bullet_audit: list[dict[str, Any]] = []
    if selected:
        answer, bullet_audit = render_from_candidates(selected)
    if not answer:
        answer = render_short_answer(kept)

    allowed_nums = {
        re.sub(r"\s+", "", f"{c.value}{c.unit}")
        for c in selected
        if c.value and c.unit
    }
    # Also keep any number already present in selected exact_text (policy spans).
    for c in selected:
        for m in re.finditer(
            r"\d+(?:\.\d+)?\s*(?:万元|亿|元|%|％|个工作日|工作日|天|个)",
            c.exact_text or "",
        ):
            allowed_nums.add(re.sub(r"\s+", "", m.group(0)))
    if allowed_nums:
        answer = _strip_disallowed_numbers(answer, allowed_nums)

    if not (answer or "").strip():
        return _no_answer(
            reason="empty_render",
            warnings=warnings,
            numeric_fact_audit=numeric_audit,
            answer_validation_decision="empty_render",
            evidence_groups=group_audit,
        )

    # SPEC v6 §3.3: render validation
    render_validation = validate_render_coverage(
        plan=plan,
        answer_text=answer,
        selected=selected,
        bullet_audit=bullet_audit,
    )
    if not render_validation.get("ok"):
        # Hard fail for exclusive numeric / polarity / multi-dim when missing critical slots
        hard_missing = [
            s for s in (render_validation.get("missing_slots") or [])
            if s in (plan.conditions or [])
            or s.startswith("dim:")
            or s in ("polarity_negative", "policy_anchor", "version")
            or s.startswith("predicate:")
            or s.startswith("value:")
        ]
        if hard_missing and plan.wants_policy and not plan.wants_numeric:
            # Policy without anchors → incomplete rather than wrong generic text
            return _no_answer(
                reason="render_validation_failed",
                warnings=warnings + [f"render_missing:{hard_missing}"],
                numeric_fact_audit=numeric_audit,
                claim_audit=ground_audit,
                fact_candidate_audit=fact_audit,
                answer_plan=answer_plan,
                answer_validation_decision="render_validation_failed",
                evidence_groups=group_audit,
                render_validation=render_validation,
            )
        if hard_missing and plan.wants_numeric and any(
            s in plan.conditions or s.startswith("dim:") for s in hard_missing
        ):
            # Allow partial multi-dim only when at least one dim covered and answer non-empty;
            # for exclusive conditions still refuse if none present.
            if plan.conditions and not any(c in answer for c in plan.conditions):
                return _no_answer(
                    reason="render_validation_failed",
                    warnings=warnings + [f"render_missing:{hard_missing}"],
                    numeric_fact_audit=numeric_audit,
                    claim_audit=ground_audit,
                    fact_candidate_audit=fact_audit,
                    answer_plan=answer_plan,
                    answer_validation_decision="render_validation_failed",
                    evidence_groups=group_audit,
                    render_validation=render_validation,
                )

    used_ids: list[str] = []
    for c in kept:
        for pid in c.evidence_passage_ids:
            if pid and pid not in used_ids:
                used_ids.append(pid)
    for b in bullet_audit:
        pid = b.get("passage_id")
        if pid and pid not in used_ids:
            used_ids.append(pid)
    pmap = passage_map(evidence)
    used_evidence = [pmap[pid] for pid in used_ids if pid in pmap]
    if not used_evidence:
        # Prefer primary group evidence
        primary_kids = group_kids
        used_evidence = [
            e for e in evidence
            if e.knowledge_id in primary_kids
        ][:3] or evidence[:3]

    raw_evidence_used = []
    sources = []
    for e in used_evidence:
        e.ensure_body()
        if not e.passage_id:
            continue
        raw_evidence_used.append({
            "knowledge_id": e.knowledge_id,
            "passage_id": e.passage_id,
            "block_id": e.block_ids[0] if e.block_ids else "",
            "block_ids": list(e.block_ids),
            "title": e.title,
            "path": "",
            "text": (e.body_text or e.text or "")[:2000],
            "body_text": e.body_text,
            "score": e.score,
            "document_family_id": e.document_family_id,
            "version_year": e.version_year,
            "retrieval_unit": "passage",
            "candidate_type": "passage",
            "citation": None,
        })
        sources.append({
            "source": "knowledge",
            "knowledge_id": e.knowledge_id,
            "passage_id": e.passage_id,
            "block_id": e.block_ids[0] if e.block_ids else "",
            "block_ids": list(e.block_ids),
            "title": e.title,
            "text": e.body_text or e.text,
            "score": e.score,
            "document_family_id": e.document_family_id,
            "version_year": e.version_year,
            "retrieval_unit": "passage",
            "candidate_type": "passage",
            "source_layer": "evidence",
            "citation": None,
        })

    ok_trace, trace_reason = ensure_passage_trace(sources + raw_evidence_used, require_passage=True)
    if not ok_trace:
        return _no_answer(
            reason="passage_trace_failed",
            warnings=warnings + [f"passage_trace:{trace_reason}"],
            answer_validation_decision="passage_trace_failed",
            evidence_groups=group_audit,
            render_validation=render_validation,
        )

    return {
        "answer": answer,
        "answer_mode": "raw_only",
        "sources": sources,
        "raw_evidence_used": raw_evidence_used,
        "claims_used": [c.to_dict() for c in kept],
        "warnings": warnings,
        "numeric_fact_audit": numeric_audit,
        "claim_audit": ground_audit,
        "fact_candidate_audit": fact_audit,
        "answer_plan": answer_plan,
        "query_plan": plan.to_dict(),
        "evidence_groups": group_audit,
        "primary_group_id": resolution.primary_group_id,
        "render_validation": render_validation,
        "reason": "structured_claim_answer",
        "answer_validation_decision": "structured_claim_answer",
        "conflict_disclosed": False,
        "conflicts": [],
        "fallbacks": [],
    }


def _strip_disallowed_numbers(answer: str, allowed: set[str]) -> str:
    if not answer or not allowed:
        return answer

    def repl(m: re.Match) -> str:
        raw = re.sub(r"\s+", "", m.group(0))
        if raw in allowed or raw.replace("元", "") in {a.replace("元", "") for a in allowed}:
            return m.group(0)
        return ""

    cleaned = re.sub(
        r"\d+(?:\.\d+)?\s*(?:万元|亿|元|%|％|个工作日|工作日|天|个)",
        repl,
        answer,
    )
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _evidence_supports_query(question: str, evidence: list[PassageEvidence]) -> bool:
    """Core-slot check via typed plan matcher + term overlap (SPEC v6 §4.1)."""
    from src.answering.direct_slot_gate import evaluate_direct_slot_evidence

    q = question or ""
    if re.search(r"办公楼地址|总部.*地址|工资薪级|岗位津贴|营收预测|火锅|火星", q):
        blob0 = "\n".join(
            (e.title or "") + "\n" + (e.body_text or e.text or "") for e in evidence
        )
        if re.search(r"办公楼地址|总部地址|薪级表|岗位津贴|营收预测", q) and not re.search(
            r"地址|薪级|津贴|营收预测", blob0
        ):
            return False

    rows = [e.to_row() for e in evidence]
    ds = evaluate_direct_slot_evidence(question, rows, min_slots=2)
    if ds.get("direct_slot_evidence"):
        return True
    # Typed plan anchors: any anchor or entity hit is enough with one more term
    plan = plan_query(q)
    blob = "\n".join((e.title or "") + "\n" + (e.body_text or e.text or "") for e in evidence)
    anchor_hits = sum(1 for a in (plan.anchors or []) if a and a in blob)
    if anchor_hits >= 1 and plan.predicate and plan.predicate in blob:
        return True
    if anchor_hits >= 2:
        return True
    terms: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", q):
        if len(run) <= 4:
            terms.add(run)
        else:
            for n in (2, 3, 4):
                for i in range(0, len(run) - n + 1):
                    terms.add(run[i:i + n])
    stop = {
        "什么", "多少", "如何", "怎么", "是否", "哪个", "哪些", "以及", "或者",
        "一个", "公司", "取消", "最新", "修订", "版本", "一级", "二级",
        "中国", "电信", "广西", "集团", "总部", "北京",
    }
    terms = {t for t in terms if t not in stop}
    hits = sum(1 for t in terms if t in blob)
    return hits >= 2 if len(terms) >= 2 else (hits >= 1 if terms else bool(blob.strip()))


def _no_answer(
    *,
    reason: str,
    warnings: list[str] | None = None,
    numeric_fact_audit: dict | None = None,
    claim_audit: list | None = None,
    fact_candidate_audit: dict | None = None,
    answer_plan: dict | None = None,
    answer_validation_decision: str | None = None,
    evidence_groups: dict | None = None,
    render_validation: dict | None = None,
) -> dict[str, Any]:
    return {
        "answer": "",
        "answer_mode": "no_answer",
        "sources": [],
        "raw_evidence_used": [],
        "claims_used": [],
        "warnings": list(warnings or []),
        "reason": reason,
        "answer_validation_decision": answer_validation_decision or reason,
        "user_notice": "知识库中未找到可直接支持该问题的证据。",
        "numeric_fact_audit": numeric_fact_audit or {},
        "claim_audit": claim_audit or [],
        "fact_candidate_audit": fact_candidate_audit or {},
        "answer_plan": answer_plan or {},
        "evidence_groups": evidence_groups or {},
        "render_validation": render_validation or {},
        "conflict_disclosed": False,
        "conflicts": [],
        "fallbacks": [],
    }
