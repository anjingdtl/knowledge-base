"""Answer payload assembly (conflict, citations, modes).

Canonical home for ask payload assembly (maintainability closure WP2).

SPEC Phase 5 (KB-019): numeric answers must be anchored to the evidence — a
generated value that does not appear in the accepted evidence is stripped to
prevent the II类/III类 substitution bug.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from src.answering.citations import (
    build_claim_citations,
    build_raw_evidence_used,
    is_claim,
    is_raw,
)
from src.answering.fact_guard import strip_unanchored_numeric_assertions
from src.answering.fallbacks import (
    build_generation_context,
    fallback_hybrid_text,
    fallback_raw_text,
    format_conflict_answer,
    format_no_answer,
)
from src.services.verified_conflict import (
    detect_claim_conflicts,
    filter_stale_claims,
    is_freshness_sensitive_query,
)

logger = logging.getLogger(__name__)

ANSWER_MODE_HYBRID = "hybrid_verified"
ANSWER_MODE_RAW = "raw_only"
ANSWER_MODE_CONFLICT = "conflict_disclosure"
ANSWER_MODE_NO_ANSWER = "no_answer"


def assemble_answer_payload(
    question: str,
    search_results: list[dict[str, Any]],
    *,
    llm_answer: str | None = None,
    search_trace: dict[str, Any] | None = None,
    disclose_claims: list[dict[str, Any]] | None = None,
    generate_fn: Callable[[str, str], str] | None = None,
) -> dict[str, Any]:
    """Build Spec §13.2 ask result fields from search results."""
    trace = dict(search_trace or {})
    warnings: list[str] = []
    fallbacks: list[dict[str, Any]] = list(trace.get("fallbacks") or [])

    freshness_q = is_freshness_sensitive_query(question)
    results = list(search_results or [])
    side_claims = list(disclose_claims or [])

    claim_rows = [r for r in results if is_claim(r)]
    raw_rows = [r for r in results if is_raw(r) and not is_claim(r)]

    claim_rows, dropped_stale = filter_stale_claims(claim_rows, drop_stale=True)
    if dropped_stale:
        warnings.append(f"excluded_stale_claims:{len(dropped_stale)}")
        for d in dropped_stale:
            warnings.append(f"stale_claim:{d.get('claim_id')}")

    conflict_pool = claim_rows + [
        c for c in side_claims
        if c.get("disclose_only") or c.get("candidate_type") == "claim"
    ]
    conflicts = detect_claim_conflicts(conflict_pool)

    claims_used = build_claim_citations(claim_rows)
    raw_evidence_used = build_raw_evidence_used(raw_rows)

    for cit in claims_used:
        for ev in cit.get("evidence") or []:
            if not (ev.get("knowledge_id") or ev.get("block_id")):
                continue
            raw_evidence_used.append({
                "knowledge_id": ev.get("knowledge_id") or "",
                "block_id": ev.get("block_id") or "",
                "title": "",
                "path": ev.get("path") or "",
                "text": ev.get("excerpt") or "",
                "score": None,
                "via_claim": cit.get("claim_id"),
                "evidence_stance": ev.get("evidence_stance"),
            })

    seen_ev: set[tuple[str, str]] = set()
    deduped_raw: list[dict[str, Any]] = []
    for e in raw_evidence_used:
        key = (str(e.get("knowledge_id") or ""), str(e.get("block_id") or ""))
        if key in seen_ev and key != ("", ""):
            continue
        seen_ev.add(key)
        deduped_raw.append(e)
    raw_evidence_used = deduped_raw

    stage_fb = (trace.get("stages") or {}).get("fallback")
    if stage_fb:
        fallbacks.append({
            "from": "verified_wiki",
            "to": "raw_retrieval",
            "reason": str(stage_fb),
        })
    wiki_err = (trace.get("stages") or {}).get("verified_wiki", {}).get("error")
    if wiki_err:
        fallbacks.append({
            "from": "verified_wiki",
            "to": "raw_retrieval",
            "reason": str(wiki_err),
        })
        warnings.append(f"wiki_degraded:{wiki_err}")

    if conflicts:
        answer_mode = ANSWER_MODE_CONFLICT
        answer = format_conflict_answer(conflicts, question)
        extra = build_claim_citations(side_claims)
        for c in extra:
            if c not in claims_used and c.get("claim_id") not in {
                x.get("claim_id") for x in claims_used
            }:
                claims_used.append(c)
    elif not claim_rows and not raw_rows:
        answer_mode = ANSWER_MODE_NO_ANSWER
        answer = format_no_answer(question, freshness=freshness_q)
        warnings.append("no_answer")
    elif claim_rows:
        answer_mode = ANSWER_MODE_HYBRID
        if llm_answer and llm_answer.strip():
            answer = llm_answer.strip()
        elif generate_fn is not None:
            context = build_generation_context(claim_rows, raw_rows, conflicts=[])
            try:
                answer = (generate_fn(question, context) or "").strip()
            except Exception as e:  # noqa: BLE001
                from src.services.deadline import DeadlineTimeout

                if isinstance(e, DeadlineTimeout):
                    raise
                logger.warning("verified answer LLM failed: %s", e)
                answer = fallback_hybrid_text(question, claim_rows, raw_rows)
                warnings.append(f"generate_failed:{e}")
        else:
            answer = fallback_hybrid_text(question, claim_rows, raw_rows)
        bare = [c for c in claims_used if not c.get("evidence")]
        if bare:
            warnings.append(f"claims_missing_evidence:{len(bare)}")
            claims_used = [c for c in claims_used if c.get("evidence")]
            if not claims_used and raw_rows:
                answer_mode = ANSWER_MODE_RAW
    else:
        answer_mode = ANSWER_MODE_RAW
        if llm_answer and llm_answer.strip():
            answer = llm_answer.strip()
        elif generate_fn is not None:
            context = build_generation_context([], raw_rows, conflicts=[])
            try:
                answer = (generate_fn(question, context) or "").strip()
            except Exception as e:  # noqa: BLE001
                from src.services.deadline import DeadlineTimeout

                if isinstance(e, DeadlineTimeout):
                    raise
                logger.warning("raw-only answer LLM failed: %s", e)
                answer = fallback_raw_text(question, raw_rows)
                warnings.append(f"generate_failed:{e}")
        else:
            answer = fallback_raw_text(question, raw_rows)

    if freshness_q and answer_mode == ANSWER_MODE_HYBRID and dropped_stale:
        warnings.append("freshness_sensitive_stale_excluded")

    # SPEC Phase 5 / v2 Phase 3 (KB-019): strip numeric assertions that are
    # not subject-anchored in the accepted evidence. Prevents the II类/III类
    # substitution where a truncated context made the LLM cite "10万元" (II类)
    # for a III类 question, including inferred phrasing like "超过10万元".
    # Only strips values with explicit amount/percent units — never touches
    # category labels, dates, or prose. If stripping would empty the answer,
    # downgrade to no_answer rather than show an unverifiable number.
    if answer and answer_mode in (ANSWER_MODE_HYBRID, ANSWER_MODE_RAW):
        evidence_blob = " ".join(
            str(r.get("text") or "") for r in (claim_rows + raw_rows)
        )
        cleaned, stripped_any = strip_unanchored_numeric_assertions(
            answer, evidence=evidence_blob, question=question,
        )
        if stripped_any:
            warnings.append("numeric_fact_guard_stripped_unanchored_value")
            answer = cleaned
            if not answer.strip() and answer_mode != ANSWER_MODE_NO_ANSWER:
                answer_mode = ANSWER_MODE_NO_ANSWER
                answer = format_no_answer(question, freshness=freshness_q)
                warnings.append("numeric_fact_guard_emptied_answer")
        else:
            answer = cleaned

        # SPEC v3 numeric guard: do NOT refuse when the answer's numeric
        # assertions are already verifiable in passage evidence, even if a
        # single "subject" heuristic fails to parse (KB-010 multi-condition).
        from src.answering.fact_guard import (
            extract_query_conditions,
            extract_query_subjects,
            select_numeric_fact_for_subject,
            answer_numerics_supported_by_evidence,
        )
        subjects = extract_query_subjects(question)
        conditions = extract_query_conditions(question)
        numeric_intent = bool(
            re.search(r"限额|金额|处罚|占比|比例|标准|多少|元|%|％", question or "")
        )
        if (
            numeric_intent
            and answer_mode in (ANSWER_MODE_HYBRID, ANSWER_MODE_RAW)
        ):
            # Prefer multi-condition anchoring (涉诈/涉骚扰) over single subject.
            anchors = conditions or subjects
            if anchors:
                all_ok = True
                audit: list[str] = []
                for anchor in anchors:
                    needed = select_numeric_fact_for_subject(
                        subject=anchor, evidence=evidence_blob,
                    )
                    if not needed:
                        # No numeric clause for this condition — skip, do not refuse.
                        audit.append(f"no_value_for:{anchor}")
                        continue
                    ans_norm = re.sub(r"\s+", "", answer or "")
                    need_norm = re.sub(r"\s+", "", needed)
                    if (
                        need_norm not in ans_norm
                        and need_norm.replace("元", "") not in ans_norm
                    ):
                        # Only refuse if answer asserts a conflicting number
                        # that cannot be verified for this condition.
                        if answer_numerics_supported_by_evidence(
                            answer=answer, evidence=evidence_blob, condition=anchor,
                        ):
                            audit.append(f"verified_alt:{anchor}")
                            continue
                        all_ok = False
                        audit.append(f"missing:{anchor}->{need_norm}")
                        break
                    audit.append(f"ok:{anchor}->{need_norm}")
                if not all_ok and subjects:
                    # Legacy single-subject hard fail only when primary subject
                    # value is missing AND answer has unsupported numbers.
                    answer_mode = ANSWER_MODE_NO_ANSWER
                    answer = format_no_answer(question, freshness=freshness_q)
                    warnings.append("numeric_fact_guard_missing_subject_value")
                    warnings.append("numeric_fact_guard_audit:" + ",".join(audit[:8]))
                elif audit:
                    warnings.append("numeric_fact_guard_audit:" + ",".join(audit[:8]))
            elif answer and not answer_numerics_supported_by_evidence(
                answer=answer, evidence=evidence_blob, condition=None,
            ):
                # No subject/condition parsed: still strip is enough; do not
                # empty-answer refuse when evidence already supports numbers.
                pass

    sources = build_sources(results, claim_rows, raw_rows)

    return {
        "answer": answer,
        "answer_mode": answer_mode,
        "conflict_disclosed": answer_mode == ANSWER_MODE_CONFLICT,
        "claims_used": claims_used,
        "raw_evidence_used": raw_evidence_used,
        "conflicts": conflicts,
        "fallbacks": fallbacks,
        "warnings": warnings,
        "sources": sources,
        "freshness_sensitive": freshness_q,
        "trace_id": trace.get("trace_id") or "",
        "search_trace": {
            "mode": trace.get("mode"),
            "route": trace.get("route"),
            "stages": trace.get("stages"),
            "sources": trace.get("sources"),
        },
    }


def build_sources(
    all_results: list[dict[str, Any]],
    claim_rows: list[dict[str, Any]],
    raw_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Backward-compatible sources list for MCP ask."""
    sources: list[dict[str, Any]] = []
    for c in claim_rows:
        primary_ev = (c.get("evidence") or [{}])[0]
        sources.append({
            "source": "verified_claim",
            "claim_id": c.get("claim_id"),
            "knowledge_id": c.get("knowledge_id") or primary_ev.get("knowledge_id") or "",
            "block_id": c.get("block_id") or primary_ev.get("block_id") or "",
            "title": c.get("title") or f"Claim: {(c.get('text') or '')[:60]}",
            "text": c.get("text") or "",
            "score": c.get("score"),
            "evidence": c.get("evidence") or [],
            "citation": c.get("citation"),
            "candidate_type": "claim",
            "source_layer": "canonical",
        })
    for r in raw_rows:
        sources.append({
            "source": r.get("source") or "knowledge",
            "knowledge_id": r.get("knowledge_id") or "",
            "block_id": r.get("block_id") or "",
            "title": r.get("title") or "",
            "text": r.get("text") or "",
            "score": r.get("score"),
            "citation": r.get("citation"),
            "candidate_type": "raw_block",
            "source_layer": "evidence",
        })
    return sources
