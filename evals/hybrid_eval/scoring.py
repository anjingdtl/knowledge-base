"""Score Raw / Wiki / Hybrid modes for a single offline hybrid case."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.services.verified_answer import (
    ANSWER_MODE_CONFLICT,
    ANSWER_MODE_HYBRID,
    ANSWER_MODE_NO_ANSWER,
    ANSWER_MODE_RAW,
    assemble_answer_payload,
)


@dataclass
class ModeScore:
    mode: str
    answer_mode: str
    correct: bool
    citation_ok: bool
    stale_served: bool
    unsupported_served: bool
    conflict_disclosed: bool
    evidence_ok: bool
    latency_ms: float = 0.0
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaseScore:
    case_id: str
    category: str
    raw: ModeScore
    wiki: ModeScore
    hybrid: ModeScore
    hybrid_ge_raw: bool
    failure_class: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "raw": self.raw.to_dict(),
            "wiki": self.wiki.to_dict(),
            "hybrid": self.hybrid.to_dict(),
            "hybrid_ge_raw": self.hybrid_ge_raw,
            "failure_class": self.failure_class,
        }


@dataclass
class HybridEvalReport:
    total: int = 0
    raw_correct: float = 0.0
    wiki_correct: float = 0.0
    hybrid_correct: float = 0.0
    hybrid_ge_raw: bool = False
    citation_correctness: float = 0.0
    stale_serving_rate: float = 0.0
    unsupported_serving_rate: float = 0.0
    conflict_detection_recall: float = 0.0
    raw_fallback_success: float = 0.0
    evidence_resolvability: float = 0.0
    by_category: dict[str, dict[str, float]] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)
    gates: dict[str, bool] = field(default_factory=dict)
    overall_pass: bool = False
    case_count_ok: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _filter_servable_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Simulate Serving Gate: drop draft/unsupported/retracted/stale-primary."""
    out = []
    for c in claims:
        status = str(c.get("status") or "active").lower()
        if status in ("draft", "unsupported", "retracted"):
            continue
        # stale claims still "exist" but answer assembler drops them on freshness;
        # for wiki-only / hybrid primary list we still pass them so scoring can detect leakage
        out.append(c)
    return out


def _run_mode(
    mode: str,
    query: str,
    raw: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    disclose: list[dict[str, Any]],
) -> dict[str, Any]:
    if mode == "raw":
        results = list(raw)
        disc: list[dict] = []
    elif mode == "wiki":
        results = _filter_servable_claims(claims)
        disc = list(disclose or [])
    else:
        results = _filter_servable_claims(claims) + list(raw)
        disc = list(disclose or [])
    return assemble_answer_payload(
        query,
        results,
        disclose_claims=disc,
        search_trace={"mode": mode},
    )


def _score_payload(
    mode: str,
    payload: dict[str, Any],
    expected: dict[str, Any],
    *,
    claim_pool: list[dict[str, Any]],
) -> ModeScore:
    details: list[str] = []
    answer_mode = payload.get("answer_mode") or ""
    exp_mode = expected.get("answer_mode")

    # Correctness heuristics
    correct = True
    if exp_mode and answer_mode != exp_mode:
        # Allow hybrid_verified when raw_only expected only if prefer not strict
        if not (
            expected.get("prefer_raw")
            and answer_mode == ANSWER_MODE_RAW
            and exp_mode == ANSWER_MODE_RAW
        ):
            correct = False
            details.append(f"mode_mismatch:{answer_mode}!={exp_mode}")

    if expected.get("conflict"):
        if not payload.get("conflict_disclosed") and answer_mode != ANSWER_MODE_CONFLICT:
            correct = False
            details.append("conflict_not_disclosed")

    if expected.get("no_answer"):
        if answer_mode != ANSWER_MODE_NO_ANSWER:
            correct = False
            details.append("expected_no_answer")

    # Expected knowledge / claim hits
    used_claims = {c.get("claim_id") for c in payload.get("claims_used") or []}
    used_kids = set()
    for e in payload.get("raw_evidence_used") or []:
        if e.get("knowledge_id"):
            used_kids.add(e["knowledge_id"])
    for s in payload.get("sources") or []:
        if s.get("knowledge_id"):
            used_kids.add(s["knowledge_id"])
        if s.get("claim_id"):
            used_claims.add(s["claim_id"])

    for cid in expected.get("correct_claim_ids") or []:
        if mode == "raw":
            continue
        if expected.get("conflict") and answer_mode == ANSWER_MODE_CONFLICT:
            # either side disclosed is ok if all listed appear in conflicts
            conf_ids = set()
            for conf in payload.get("conflicts") or []:
                conf_ids.add(conf.get("claim_a_id"))
                conf_ids.add(conf.get("claim_b_id"))
            if cid not in conf_ids and cid not in used_claims:
                correct = False
                details.append(f"missing_claim:{cid}")
        elif exp_mode in (ANSWER_MODE_HYBRID, ANSWER_MODE_CONFLICT) and cid not in used_claims:
            # hybrid should surface claim when eligible
            if not expected.get("prefer_raw"):
                correct = False
                details.append(f"missing_claim:{cid}")

    for kid in expected.get("correct_knowledge_ids") or []:
        if kid not in used_kids and mode != "wiki":
            # raw/hybrid should hit knowledge when only raw path
            if expected.get("prefer_raw") or exp_mode in (ANSWER_MODE_RAW, ANSWER_MODE_HYBRID):
                if kid not in used_kids:
                    # also allow claim evidence path
                    via_claim = any(
                        (ev.get("knowledge_id") == kid)
                        for c in (payload.get("claims_used") or [])
                        for ev in (c.get("evidence") or [])
                    )
                    if not via_claim:
                        correct = False
                        details.append(f"missing_knowledge:{kid}")

    # Stale / unsupported leakage
    stale_served = False
    unsupported_served = False
    for c in payload.get("claims_used") or []:
        for ev in c.get("evidence") or []:
            if ev.get("stale"):
                stale_served = True
        # status on citation
        st = str(c.get("status") or "").lower()
        if st == "unsupported":
            unsupported_served = True
    # also inspect answer text for stale claim ids when forbidden
    if expected.get("forbid_stale_in_answer") and stale_served:
        correct = False
        details.append("stale_served")
    if expected.get("forbid_unsupported_status") and unsupported_served:
        correct = False
        details.append("unsupported_served")

    # Citation / evidence
    citation_ok = True
    evidence_ok = True
    if expected.get("must_have_evidence") and answer_mode in (
        ANSWER_MODE_HYBRID, ANSWER_MODE_CONFLICT,
    ):
        claims_used = payload.get("claims_used") or []
        if claims_used and not all(c.get("evidence") for c in claims_used):
            citation_ok = False
            evidence_ok = False
            correct = False
            details.append("claim_without_evidence")
        if answer_mode == ANSWER_MODE_CONFLICT:
            for conf in payload.get("conflicts") or []:
                if not conf.get("evidence_a") or not conf.get("evidence_b"):
                    citation_ok = False
                    details.append("conflict_missing_dual_evidence")

    if expected.get("require_location") and mode != "wiki":
        # soft: raw citation path exists
        if not any(
            (s.get("citation") or {}).get("path") or s.get("block_id")
            for s in (payload.get("sources") or [])
        ):
            citation_ok = False
            details.append("missing_location_path")

    # Scope / numeric preservation soft checks
    answer = payload.get("answer") or ""
    if expected.get("scope_token") and mode != "raw":
        if expected["scope_token"] not in answer and expected["scope_token"] not in str(
            payload.get("claims_used")
        ):
            # do not hard-fail if claim statement has it
            stmt_ok = any(
                expected["scope_token"] in (c.get("statement") or "")
                for c in (payload.get("claims_used") or [])
            )
            if not stmt_ok and answer_mode == ANSWER_MODE_HYBRID:
                details.append("scope_not_preserved")

    return ModeScore(
        mode=mode,
        answer_mode=answer_mode,
        correct=correct,
        citation_ok=citation_ok,
        stale_served=stale_served,
        unsupported_served=unsupported_served,
        conflict_disclosed=bool(payload.get("conflict_disclosed")),
        evidence_ok=evidence_ok,
        details=details,
    )


def score_case(case: dict[str, Any]) -> CaseScore:
    q = case["query"]
    raw = case.get("raw_results") or []
    claims = case.get("claim_results") or []
    disclose = case.get("disclose_claims") or []
    expected = case.get("expected") or {}

    raw_p = _run_mode("raw", q, raw, claims, disclose)
    wiki_p = _run_mode("wiki", q, raw, claims, disclose)
    hyb_p = _run_mode("hybrid", q, raw, claims, disclose)

    raw_s = _score_payload("raw", raw_p, _expected_for_mode(expected, "raw"), claim_pool=claims)
    wiki_s = _score_payload("wiki", wiki_p, _expected_for_mode(expected, "wiki"), claim_pool=claims)
    hyb_s = _score_payload("hybrid", hyb_p, expected, claim_pool=claims)

    # hybrid correctness should be >= raw for the case
    hybrid_ge = (1 if hyb_s.correct else 0) >= (1 if raw_s.correct else 0)

    failure = ""
    if not hyb_s.correct:
        failure = "hybrid_incorrect"
    elif hyb_s.stale_served:
        failure = "stale_served"
    elif hyb_s.unsupported_served:
        failure = "unsupported_served"
    elif not hyb_s.citation_ok:
        failure = "citation"
    elif expected.get("conflict") and not hyb_s.conflict_disclosed:
        failure = "conflict_miss"
    elif not hybrid_ge:
        failure = "hybrid_below_raw"

    return CaseScore(
        case_id=case["id"],
        category=case.get("category") or "",
        raw=raw_s,
        wiki=wiki_s,
        hybrid=hyb_s,
        hybrid_ge_raw=hybrid_ge,
        failure_class=failure,
    )


def _expected_for_mode(expected: dict[str, Any], mode: str) -> dict[str, Any]:
    exp = dict(expected)
    if mode == "raw":
        if expected.get("no_answer") and not expected.get("correct_knowledge_ids"):
            exp["answer_mode"] = ANSWER_MODE_NO_ANSWER
        elif expected.get("prefer_raw") or expected.get("answer_mode") in (
            ANSWER_MODE_RAW, ANSWER_MODE_HYBRID, ANSWER_MODE_CONFLICT,
        ):
            if expected.get("correct_knowledge_ids") or expected.get("answer_mode") != ANSWER_MODE_NO_ANSWER:
                if expected.get("no_answer") and not expected.get("correct_knowledge_ids"):
                    exp["answer_mode"] = ANSWER_MODE_NO_ANSWER
                else:
                    exp["answer_mode"] = (
                        ANSWER_MODE_NO_ANSWER
                        if not expected.get("correct_knowledge_ids")
                        and not expected.get("prefer_raw")
                        and expected.get("answer_mode") == ANSWER_MODE_NO_ANSWER
                        else ANSWER_MODE_RAW
                    )
            if expected.get("answer_mode") == ANSWER_MODE_CONFLICT:
                # raw alone may not disclose structured conflict
                exp["answer_mode"] = ANSWER_MODE_RAW
                exp["conflict"] = False
        if expected.get("answer_mode") == ANSWER_MODE_NO_ANSWER:
            exp["answer_mode"] = ANSWER_MODE_NO_ANSWER
    if mode == "wiki":
        if expected.get("no_answer"):
            exp["answer_mode"] = ANSWER_MODE_NO_ANSWER
        elif expected.get("prefer_raw") and expected.get("answer_mode") == ANSWER_MODE_RAW:
            # wiki-only may no_answer when only raw has truth and claims are stale/unsup
            exp["answer_mode"] = ANSWER_MODE_NO_ANSWER
            exp["no_answer"] = True
        elif expected.get("conflict"):
            exp["answer_mode"] = ANSWER_MODE_CONFLICT
        elif expected.get("correct_claim_ids"):
            exp["answer_mode"] = ANSWER_MODE_HYBRID
    return exp


def summarize_report(case_scores: list[CaseScore]) -> HybridEvalReport:
    n = len(case_scores) or 1
    raw_c = sum(1 for c in case_scores if c.raw.correct) / n
    wiki_c = sum(1 for c in case_scores if c.wiki.correct) / n
    hyb_c = sum(1 for c in case_scores if c.hybrid.correct) / n
    cit = sum(1 for c in case_scores if c.hybrid.citation_ok) / n
    stale = sum(1 for c in case_scores if c.hybrid.stale_served) / n
    unsup = sum(1 for c in case_scores if c.hybrid.unsupported_served) / n

    conflict_cases = [c for c in case_scores if c.category == "conflict"]
    if conflict_cases:
        conf_recall = sum(1 for c in conflict_cases if c.hybrid.conflict_disclosed) / len(conflict_cases)
    else:
        conf_recall = 1.0

    fallback_cases = [c for c in case_scores if c.category in ("wiki_fallback", "freshness_stale", "unsupported_guard")]
    if fallback_cases:
        fb = sum(
            1 for c in fallback_cases
            if c.hybrid.answer_mode in (ANSWER_MODE_RAW, ANSWER_MODE_NO_ANSWER)
            or c.hybrid.correct
        ) / len(fallback_cases)
    else:
        fb = 1.0

    evidence = sum(1 for c in case_scores if c.hybrid.evidence_ok) / n
    hybrid_ge = all(c.hybrid_ge_raw for c in case_scores)

    by_cat: dict[str, dict[str, float]] = {}
    cats = sorted({c.category for c in case_scores})
    for cat in cats:
        rows = [c for c in case_scores if c.category == cat]
        m = len(rows) or 1
        by_cat[cat] = {
            "count": float(len(rows)),
            "raw_correct": sum(1 for c in rows if c.raw.correct) / m,
            "wiki_correct": sum(1 for c in rows if c.wiki.correct) / m,
            "hybrid_correct": sum(1 for c in rows if c.hybrid.correct) / m,
        }

    failures = [
        {
            "case_id": c.case_id,
            "category": c.category,
            "class": c.failure_class,
            "details": c.hybrid.details,
        }
        for c in case_scores
        if c.failure_class
    ]

    gates = {
        "case_count_ge_150": len(case_scores) >= 150,
        "hybrid_ge_raw_correctness": hyb_c + 1e-9 >= raw_c,
        "stale_serving_rate_zero": stale == 0.0,
        "unsupported_serving_rate_zero": unsup == 0.0,
        "citation_correctness_ge_0_95": cit >= 0.95,
        "conflict_detection_recall_ge_0_90": conf_recall >= 0.90,
        "raw_fallback_success_one": fb >= 0.99,
        "evidence_resolvability_ge_0_99": evidence >= 0.99,
        "all_cases_hybrid_ge_raw": hybrid_ge,
    }
    overall = all(gates.values())

    return HybridEvalReport(
        total=len(case_scores),
        raw_correct=round(raw_c, 4),
        wiki_correct=round(wiki_c, 4),
        hybrid_correct=round(hyb_c, 4),
        hybrid_ge_raw=hyb_c + 1e-9 >= raw_c,
        citation_correctness=round(cit, 4),
        stale_serving_rate=round(stale, 4),
        unsupported_serving_rate=round(unsup, 4),
        conflict_detection_recall=round(conf_recall, 4),
        raw_fallback_success=round(fb, 4),
        evidence_resolvability=round(evidence, 4),
        by_category=by_cat,
        failures=failures[:50],
        gates=gates,
        overall_pass=overall,
        case_count_ok=len(case_scores) >= 150,
        notes=[
            "Offline deterministic hybrid eval (no embedding/LLM).",
            "Modes: raw_only vs wiki_only vs hybrid_verified via assemble_answer_payload.",
        ],
    )
