"""Single hit-rate scoring authority (metric_contract_version=2.0).

All CLI entrypoints (hit_rate_score / hit_rate_finalize) must delegate here.
No duplicated core scoring logic is allowed outside this module.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from evals.hit_rate_v2 import METRIC_CONTRACT_VERSION
from evals.hit_rate_v2.models import AggregateMetrics, CaseScore

# Explicit refusal / insufficient-evidence markers (Chinese + common English).
REFUSAL_PATTERNS: tuple[str, ...] = (
    r"证据不足",
    r"未检索到",
    r"未收录",
    r"没有足够",
    r"无法回答",
    r"知识库中不",
    r"未包含",
    r"不存在",
    r"无法提供",
    r"不在知识库",
    r"未找到",
    r"没有相关",
    r"超出.*范围",
    r"与.*无关",
    r"未能确认",
    r"未找到可回答",
    r"不能确定",
    r"无法确认",
    r"暂无",
    r"no\s*answer",
    r"not\s+found",
    r"insufficient\s+evidence",
    r"cannot\s+answer",
    r"out\s+of\s+scope",
)

# Modes that assert a substantive answer rather than refuse.
ANSWER_MODES: frozenset[str] = frozenset(
    {
        "raw_only",
        "verified",
        "hybrid",
        "answer",
        "extractive",
        "generative",
        "template",
        "fact_candidate",
        "evidence_group",
    }
)

NO_ANSWER_MODES: frozenset[str] = frozenset(
    {
        "no_answer",
        "refuse",
        "refusal",
        "clarification",
        "clarify",
        "insufficient_evidence",
    }
)

# Heuristic substantive-fact signals for no-answer false positives when
# forbidden_facts do not literally appear in the answer text.
SUBSTANTIVE_ASSERTION_PATTERNS: tuple[str, ...] = (
    r"地址[是为：:]",
    r"位于",
    r"坐落",
    r"办公[地址楼]",
    r"\d{1,5}\s*元",
    r"人民币",
    r"部门[是为：:]",
    r"负责人[是为：:]",
    r"品牌[是为：:]",
    r"进展[是为：:]",
    r"电话[：:]\s*\d",
    r"1[3-9]\d{9}",
    r"\d{6,}",  # long numeric identifiers / phone-like
    r"具体[为是]",
    r"明确[为是]",
    r"[省市县区].{0,8}[路街道巷号楼]",
)


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def contains_fact(haystack: Any, needle: Any) -> bool:
    n = normalize_text(needle)
    if not n:
        return False
    return n in normalize_text(haystack)


def extract_ask_payload(case_result: dict[str, Any]) -> dict[str, Any]:
    """Normalize ask payload from harness case JSON or already-flat dict."""
    if not isinstance(case_result, dict):
        return {
            "ok": False,
            "answer": "",
            "mode": None,
            "warnings": [],
            "sources": [],
            "raw_ev": [],
            "snap": {},
            "claims": [],
            "citation_integrity": {},
        }

    # Already-flat scoring input
    if "answer" in case_result and "envelope" not in (case_result.get("ask") or {}):
        if "ask" not in case_result:
            return {
                "ok": True,
                "answer": str(case_result.get("answer") or ""),
                "mode": case_result.get("answer_mode") or case_result.get("mode"),
                "warnings": case_result.get("warnings") or [],
                "sources": case_result.get("sources") or [],
                "raw_ev": case_result.get("raw_evidence_used") or [],
                "snap": case_result.get("evidence_snapshot") or {},
                "claims": case_result.get("claims_used") or case_result.get("claims") or [],
                "citation_integrity": case_result.get("citation_integrity") or {},
            }

    aenv = (case_result.get("ask") or {}).get("envelope") or {}
    data = aenv.get("data") if aenv.get("ok") else None
    if not isinstance(data, dict):
        # Some fixtures put payload at ask.data
        data = (case_result.get("ask") or {}).get("data")
    if not isinstance(data, dict):
        return {
            "ok": False,
            "answer": "",
            "mode": None,
            "warnings": [],
            "sources": [],
            "raw_ev": [],
            "snap": {},
            "claims": [],
            "citation_integrity": {},
        }
    return {
        "ok": True,
        "answer": str(data.get("answer") or ""),
        "mode": data.get("answer_mode") or data.get("mode"),
        "warnings": data.get("warnings") or [],
        "sources": data.get("sources") or [],
        "raw_ev": data.get("raw_evidence_used") or [],
        "snap": data.get("evidence_snapshot") or {},
        "claims": data.get("claims_used") or data.get("claims") or [],
        "citation_integrity": data.get("citation_integrity") or {},
    }


def extract_candidates(case_result: dict[str, Any]) -> list[dict[str, Any]]:
    cands = case_result.get("candidates")
    if isinstance(cands, list):
        return [c for c in cands if isinstance(c, dict)]
    search = case_result.get("search") or {}
    if isinstance(search, dict):
        env = search.get("envelope") or {}
        data = env.get("data") if isinstance(env, dict) else None
        if isinstance(data, dict):
            items = data.get("candidates") or data.get("results") or data.get("hits") or []
            if isinstance(items, list):
                return [c for c in items if isinstance(c, dict)]
        items = search.get("candidates") or search.get("results") or []
        if isinstance(items, list):
            return [c for c in items if isinstance(c, dict)]
    return []


def _has_refusal_marker(answer: str) -> bool:
    text = answer or ""
    if not text.strip():
        return True
    return any(re.search(p, text, flags=re.I) for p in REFUSAL_PATTERNS)


def _has_affirmative_fact_pattern(answer: str) -> bool:
    """True when answer asserts a concrete fact (not merely refuses to provide one)."""
    text = answer or ""
    # Affirmative address / identity forms (not "无法提供地址")
    affirmative = (
        r"地址[是为：:]",
        r"位于",
        r"坐落于?",
        r"(办公楼|总部).{0,6}(在|于)",
        r"\d{1,5}\s*元",
        r"人民币\s*\d",
        r"部门[是为：:]",
        r"负责人[是为：:]",
        r"品牌[是为：:]",
        r"进展[是为：:]",
        r"电话[：:]\s*\d",
        r"1[3-9]\d{9}",
        r"具体[为是：:]",
        r"明确[为是：:]",
        r"[省市县区].{0,12}[路街道巷号楼].{0,8}\d",
    )
    return any(re.search(p, text) for p in affirmative)


def _is_refusal_only(answer: str) -> bool:
    text = (answer or "").strip()
    if not text:
        return True
    if not _has_refusal_marker(text):
        return False
    # Refusal + concrete affirmative fact = not refusal-only
    if _has_affirmative_fact_pattern(text):
        return False
    # Soft residual after removing refusal markers / fillers
    residual = text
    for p in REFUSAL_PATTERNS:
        residual = re.sub(p, " ", residual, flags=re.I)
    residual = re.sub(
        r"(抱歉|对不起|因此|所以|目前|暂时|建议|请|您|该问题|相关|信息|内容|证据|"
        r"依据|资料|答案|结果|办公楼?地址|具体地址|详细地址)",
        " ",
        residual,
    )
    residual = re.sub(r"[\s，。！？、；：:.!?,;\"'“”‘’\-—…（）()【】\[\]]+", "", residual)
    # Allow short topic restatement after refusal
    return len(residual) < 20


def _has_substantive_assertion(answer: str) -> bool:
    text = (answer or "").strip()
    if not text:
        return False
    if _is_refusal_only(text):
        return False
    if _has_affirmative_fact_pattern(text):
        return True
    # Long non-refusal content is treated as substantive for no-answer cases.
    if len(normalize_text(text)) >= 24:
        # Bullet / list style deterministic dumps are substantive.
        if re.search(r"[-•·]\s*\S{2,}", text):
            return True
        if re.search(r"[：:]\s*\S{2,}", text) and not _has_refusal_marker(text):
            return True
        # Generic: non-refusal text of meaningful length
        return True
    if any(re.search(p, text) for p in SUBSTANTIVE_ASSERTION_PATTERNS):
        # Guard: "无法提供…地址" style should not trip 办公[地址楼]
        if _has_refusal_marker(text) and not _has_affirmative_fact_pattern(text):
            return False
        return True
    return False


def _forbidden_assertion_hit(answer: str, forbidden: Iterable[str]) -> bool:
    ans = answer or ""
    if not ans.strip():
        return False
    for fact in forbidden or []:
        if not fact:
            continue
        if contains_fact(ans, fact):
            # Leading pure refusal does not count as asserting the forbidden phrase
            # when the phrase is only mentioned as "does not exist".
            head = normalize_text(ans)[:80]
            if re.search(r"(未|没有|无法|不含|不应|不存在|未检索到|未收录)", head):
                # still count if assertion appears after refusal
                if re.search(
                    rf"(未找到|证据不足|无法).{{0,40}}{re.escape(normalize_text(fact))}",
                    normalize_text(ans),
                ):
                    continue
            return True
    return False


def _expected_passage_ids(case: dict[str, Any]) -> set[str]:
    """Golden expected / supporting / acceptable passage ids."""
    out: set[str] = set()
    for src in case.get("expected_sources") or []:
        if not isinstance(src, dict):
            continue
        role = str(src.get("source_role") or "primary")
        if role in {"primary", "supporting", "acceptable"}:
            pid = str(src.get("passage_id") or "").strip()
            if pid:
                out.add(pid)
    for g in case.get("required_fact_groups") or []:
        if not isinstance(g, dict):
            continue
        for key in ("evidence_passage_id", "passage_id"):
            pid = str(g.get(key) or "").strip()
            if pid:
                out.add(pid)
    # Legacy flat fields
    for key in ("expected_passage_ids", "supporting_passage_ids"):
        for pid in case.get(key) or []:
            if str(pid).strip():
                out.add(str(pid).strip())
    return out


def _citation_bucket(
    source: dict[str, Any],
    snap: dict[str, Any],
    raw_evidence: list[Any],
    *,
    expected_pids: set[str] | None = None,
    require_golden_passage: bool = False,
) -> str:
    """Classify a citation.

    Pass requires (Task 2.0.5):
    1. passage in Snapshot allowlist (accepted or adjacent extension);
    2. passage in raw_evidence_used;
    3. when require_golden_passage and Golden has expected passages — match them;
    4. fact-group support is checked separately via _fact_group_supported_by_citation.
    """
    pid = str(source.get("passage_id") or "").strip()
    raw_pids = {
        str(item.get("passage_id") or "")
        for item in raw_evidence
        if isinstance(item, dict)
    }
    accepted = {
        str(p) for p in (snap.get("accepted_passage_ids") or []) if str(p).strip()
    }
    adjacent = {
        str(p) for p in (snap.get("adjacent_passage_ids") or []) if str(p).strip()
    }
    for item in snap.get("adjacent_allowlist") or []:
        if isinstance(item, dict) and str(item.get("passage_id") or "").strip():
            adjacent.add(str(item.get("passage_id")))
    if not pid or pid not in raw_pids:
        return "rejected"
    if require_golden_passage and expected_pids is not None and expected_pids:
        if pid not in expected_pids:
            return "rejected"
    if pid in accepted:
        return "preaccepted"
    if source.get("is_adjacent_extension") and pid in adjacent:
        return "adjacent_extension"
    return "rejected"


def score_answerable_case(
    case: dict[str, Any],
    case_result: dict[str, Any],
) -> CaseScore:
    case_id = str(case.get("case_id") or case.get("id") or "")
    expected_ids = {
        str(x)
        for x in (
            case.get("expected_knowledge_ids")
            or case.get("expected_ids")
            or []
        )
        if str(x)
    }
    required = list(case.get("required_facts") or case.get("required_fact_texts") or [])
    # V2 required_fact_groups → text objects for legacy substring matching
    for group in case.get("required_fact_groups") or []:
        if not isinstance(group, dict):
            continue
        obj = group.get("object_text") or group.get("value")
        if obj is not None and str(obj).strip():
            required.append(str(obj))
        for v in group.get("acceptable_variants") or []:
            if str(v).strip():
                required.append(str(v))
    # Dedup while preserving order for coverage (all-of required texts):
    # For V2 groups we treat each group's object_text as one required unit;
    # variants are alternatives — handled below via group-aware path if present.
    forbidden = list(
        case.get("forbidden_facts")
        or case.get("forbidden_assertions")
        or []
    )
    forbidden = [str(f) for f in forbidden if str(f).strip()]

    # Prefer structured fact groups when present (Phase 1 V2)
    fact_groups = [
        g for g in (case.get("required_fact_groups") or []) if isinstance(g, dict)
    ]

    cands = extract_candidates(case_result)
    cand_ids = [str(c.get("knowledge_id") or "") for c in cands if c.get("knowledge_id")]
    top1_id = cand_ids[0] if cand_ids else None
    top1_hit = bool(top1_id and top1_id in expected_ids)
    recall5 = any(cid in expected_ids for cid in cand_ids[:5])

    ask = extract_ask_payload(case_result)
    ans = ask["answer"]
    mode = str(ask.get("mode") or "") or None

    if fact_groups:
        ans_has_required = all(
            _fact_group_covered(ans, g) for g in fact_groups if g.get("required", True)
        )
    else:
        ans_has_required = (
            all(contains_fact(ans, f) for f in required) if required else bool(ans.strip())
        )
    ans_has_forbidden = _forbidden_assertion_hit(ans, forbidden)
    ask_fact_correct = bool(ans.strip()) and ans_has_required and not ans_has_forbidden

    srcs = [s for s in (ask.get("sources") or []) if isinstance(s, dict)]
    snap = ask.get("snap") or {}
    raw_ev = ask.get("raw_ev") or []
    expected_pids = _expected_passage_ids(case)
    # When Golden declares expected passages, citations must bind to them.
    require_golden = bool(expected_pids)
    buckets = {"preaccepted": 0, "adjacent_extension": 0, "expected_id": 0, "rejected": 0}
    for s in srcs:
        b = _citation_bucket(
            s,
            snap,
            raw_ev,
            expected_pids=expected_pids,
            require_golden_passage=require_golden,
        )
        buckets[b] = buckets.get(b, 0) + 1
    if srcs:
        valid_n = buckets["preaccepted"] + buckets["adjacent_extension"] + buckets["expected_id"]
        ask_citation_valid = valid_n == len(srcs)
        citation_valid_num = sum(1 for s in srcs if str(s.get("knowledge_id") or "") in expected_ids)
        citation_valid_den = len(srcs)
    else:
        ask_citation_valid = False
        citation_valid_num = 0
        citation_valid_den = 0

    # Fact-group citation support (point 4): each required group with an
    # evidence_passage_id must be cited via that passage.
    if fact_groups and ask_citation_valid:
        for g in fact_groups:
            if not g.get("required", True):
                continue
            if not _fact_group_supported_by_citation(g, srcs, raw_ev):
                ask_citation_valid = False
                break

    # Unsupported assertions: N/A unless structured claims are present.
    claims = [c for c in (ask.get("claims") or []) if isinstance(c, dict)]
    unsupported_assertion_rate: float | None
    if not claims:
        unsupported_assertion_rate = None  # not measurable yet
    else:
        unsupported = 0
        for claim in claims:
            c_pids = {
                str(p).strip()
                for p in (claim.get("evidence_passage_ids") or claim.get("passage_ids") or [])
                if str(p).strip()
            }
            if not c_pids:
                unsupported += 1
        unsupported_assertion_rate = round(unsupported / len(claims), 4)

    hallucination = ans_has_forbidden  # proxy only
    e2e_pass = (
        recall5 and ask_fact_correct and ask_citation_valid and not hallucination
    )
    grounded = ask_fact_correct and ask_citation_valid

    score = 0
    if top1_hit:
        score += 3
    if recall5:
        score += 2
    if ask_fact_correct:
        score += 2
    if ask_citation_valid:
        score += 2
    if not hallucination:
        score += 1

    # Wrong-version diagnostic
    search_blob = "\n".join(
        f"{c.get('title', '')} {c.get('text', '')}" for c in cands
    )
    wrong_version = any(contains_fact(search_blob, f) for f in forbidden) if forbidden else False

    cs = CaseScore(
        case_id=case_id,
        case_type="answerable",
        metric_contract_version=METRIC_CONTRACT_VERSION,
        retrieval_top1_hit=top1_hit,
        retrieval_recall_at_5=recall5,
        answer_fact_coverage=ask_fact_correct,
        answer_forbidden_assertion=ans_has_forbidden,
        citation_lineage_valid=ask_citation_valid,
        answer_supported=grounded,
        e2e_pass=e2e_pass,
        top1_hit=top1_hit,
        recall5=recall5,
        ask_fact_correct=ask_fact_correct,
        ask_citation_valid=ask_citation_valid,
        citation_valid=ask_citation_valid,
        facts_correct=ask_fact_correct,
        grounded=grounded,
        no_hallucination=not hallucination,
        forbidden_assertion=ans_has_forbidden,
        score=score,
        top1_id=top1_id,
        cand_ids=cand_ids,
        cand_count=len(cands),
        ask_has_answer=bool(ans.strip()),
        ask_source_count=len(srcs),
        answer_mode=mode,
        citation_buckets=buckets,
        citation_valid_num=citation_valid_num,
        citation_valid_den=citation_valid_den,
        extra={
            "wrong_version_in_evidence": wrong_version,
            "search_citation_valid": recall5,
            "forbidden_violated": ans_has_forbidden,
            "citation_valid_ratio_num": citation_valid_num,
            "citation_valid_ratio_den": citation_valid_den,
            "answer_fact_correct": ask_fact_correct,
            "source_trace_valid": ask_citation_valid,
            "expected_doc_recalled": recall5,
            "unsupported_assertion_rate": unsupported_assertion_rate,
        },
    )
    sev, cat, reason = classify_defect(case, case_result, cs)
    cs.defect_severity = sev
    cs.defect_category = cat
    cs.defect_reason = reason
    return cs


def _slot_present(answer: str, slot: Any) -> bool:
    """Whether a non-empty slot value appears in the answer (normalized)."""
    text = str(slot or "").strip()
    if not text:
        return True
    return contains_fact(answer, text)


def _extract_numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", normalize_text(text))


def _fact_group_covered(answer: str, group: dict[str, Any]) -> bool:
    """Fact-group coverage with match_policy semantics (Task 2.0.5).

    - exact: normalized exact phrase of object_text / value
    - normalized: object_text or any acceptable_variants
    - numeric_unit: value + unit + condition/scope/version when provided
    - semantic_review: never auto-pass via substring; requires explicit
      human/judge flag on the case result (or fails closed)

    Subject / predicate / object / condition / scope / version all participate
    when present on the group.
    """
    policy = str(group.get("match_policy") or "normalized")
    ans = answer or ""

    # Structural slots always participate when present.
    for slot_key in ("subject", "predicate", "condition", "scope", "version"):
        if not _slot_present(ans, group.get(slot_key)):
            return False

    obj = group.get("object_text")
    val = group.get("value")
    unit = group.get("unit")
    variants: list[str] = []
    if obj is not None and str(obj).strip():
        variants.append(str(obj))
    if val is not None and str(val).strip():
        if unit:
            variants.append(f"{val}{unit}")
            variants.append(f"{val} {unit}")
        variants.append(str(val))
    for v in group.get("acceptable_variants") or []:
        if str(v).strip():
            variants.append(str(v))

    if policy == "semantic_review":
        # Auto substring pass is forbidden. Only an external judge mark may pass.
        if group.get("semantic_review_passed") is True:
            return True
        if group.get("human_verified") is True:
            return True
        return False

    if policy == "exact":
        if not variants:
            return True
        # Exact: primary object_text / value(+unit) only — not free variants.
        primary = []
        if obj is not None and str(obj).strip():
            primary.append(str(obj))
        if val is not None and str(val).strip():
            if unit:
                primary.append(f"{val}{unit}")
            primary.append(str(val))
        needles = primary or variants
        return any(contains_fact(ans, v) for v in needles)

    if policy == "numeric_unit":
        # Value (as number) and unit must both bind; condition already checked.
        if val is None or not str(val).strip():
            # Fall back to object_text if no structured value
            if not variants:
                return True
            return any(contains_fact(ans, v) for v in variants)
        val_s = str(val).strip()
        nums_ans = _extract_numbers(ans)
        nums_val = _extract_numbers(val_s)
        if nums_val and not any(n in nums_ans for n in nums_val):
            return False
        if unit and str(unit).strip():
            if not contains_fact(ans, unit):
                return False
        # Prefer full value+unit phrase when possible; number+unit already checked.
        return True

    # normalized (default): object or any acceptable variant
    if not variants:
        return True
    return any(contains_fact(ans, v) for v in variants)


def _fact_group_supported_by_citation(
    group: dict[str, Any],
    sources: list[dict[str, Any]],
    raw_evidence: list[Any],
) -> bool:
    """Citation must support the fact group, not merely share a knowledge_id."""
    evidence_pid = str(
        group.get("evidence_passage_id") or group.get("passage_id") or ""
    ).strip()
    if not evidence_pid:
        # No binding required when Golden itself lacks evidence passage.
        return True
    src_pids = {
        str(s.get("passage_id") or "").strip()
        for s in sources
        if isinstance(s, dict)
    }
    raw_pids = {
        str(r.get("passage_id") or "").strip()
        for r in raw_evidence
        if isinstance(r, dict)
    }
    return evidence_pid in src_pids and evidence_pid in raw_pids


def score_no_answer_case(
    case: dict[str, Any],
    case_result: dict[str, Any],
) -> CaseScore:
    """Score expected-no-answer cases with fail-closed false-positive detection.

    Pass requires ALL of:
    1. expected_no_answer / answerability=no_answer
    2. answer_mode in no-answer family OR empty/clear refusal only
    3. no substantive deterministic assertion
    4. sources/raw evidence not presented as supporting a definitive answer
    5. no forbidden assertion hit
    """
    case_id = str(case.get("case_id") or case.get("id") or "")
    forbidden = list(
        case.get("forbidden_facts")
        or case.get("forbidden_assertions")
        or []
    )
    forbidden = [str(f) for f in forbidden if str(f).strip()]

    ask = extract_ask_payload(case_result)
    ans = ask["answer"]
    mode = str(ask.get("mode") or "").strip() or None
    srcs = [s for s in (ask.get("sources") or []) if isinstance(s, dict)]
    raw_ev = [r for r in (ask.get("raw_ev") or []) if isinstance(r, dict)]
    cands = extract_candidates(case_result)

    reason_codes: list[str] = []
    false_positive = False

    mode_l = (mode or "").lower()
    if mode_l in ANSWER_MODES:
        false_positive = True
        reason_codes.append("unexpected_answer_mode")

    if _forbidden_assertion_hit(ans, forbidden):
        false_positive = True
        reason_codes.append("forbidden_assertion")

    substantive = _has_substantive_assertion(ans)
    refusal_only = _is_refusal_only(ans)
    has_refusal = _has_refusal_marker(ans)

    if substantive and not refusal_only:
        false_positive = True
        reason_codes.append("substantive_answer_on_no_answer")

    if has_refusal and substantive and not refusal_only:
        false_positive = True
        if "refusal_with_assertion" not in reason_codes:
            reason_codes.append("refusal_with_assertion")

    # Sources/raw evidence used to back a non-refusal answer
    if (srcs or raw_ev) and not refusal_only and (substantive or mode_l in ANSWER_MODES):
        false_positive = True
        if "sources_present_on_no_answer" not in reason_codes:
            reason_codes.append("sources_present_on_no_answer")

    # Mode missing but non-empty non-refusal content
    if mode_l not in NO_ANSWER_MODES and mode_l not in ANSWER_MODES:
        if ans.strip() and not refusal_only:
            false_positive = True
            if "substantive_answer_on_no_answer" not in reason_codes:
                reason_codes.append("substantive_answer_on_no_answer")

    expressed = refusal_only or (not ans.strip()) or (
        mode_l in NO_ANSWER_MODES and not substantive
    )
    no_fab = not false_positive
    score = (4 if not false_positive else 0) + (3 if expressed else 0) + (3 if no_fab else 0)

    cs = CaseScore(
        case_id=case_id,
        case_type="no_answer",
        metric_contract_version=METRIC_CONTRACT_VERSION,
        false_positive=false_positive,
        expressed_insufficient=expressed,
        no_fabrication=no_fab,
        reason_codes=sorted(set(reason_codes)),
        score=score,
        top1_id=(str(cands[0].get("knowledge_id")) if cands else None),
        cand_ids=[str(c.get("knowledge_id")) for c in cands if c.get("knowledge_id")],
        cand_count=len(cands),
        ask_has_answer=bool(ans.strip()),
        ask_source_count=len(srcs),
        answer_mode=mode,
        defect_severity="P1" if false_positive else None,
        defect_category="false_positive" if false_positive else None,
        defect_reason=(
            "no-answer 用例给出确定性错误答案: " + ",".join(sorted(set(reason_codes)))
            if false_positive
            else None
        ),
    )
    return cs


def _score_field(sc: CaseScore | dict[str, Any], name: str, default: Any = None) -> Any:
    if isinstance(sc, CaseScore):
        if name in sc.extra:
            return sc.extra.get(name, default)
        return getattr(sc, name, default)
    if isinstance(sc, dict):
        return sc.get(name, default)
    return default


def classify_defect(
    case: dict[str, Any],
    case_result: dict[str, Any],
    sc: CaseScore | dict[str, Any],
) -> tuple[str | None, str | None, str | None]:
    """Return (severity, category, reason) for failed/partial answerable cases.

    Accepts CaseScore or a legacy dict score payload (CLI/regression compat).
    """
    ask = extract_ask_payload(case_result)
    expected = case.get("expected_knowledge_ids") or case.get("expected_ids") or []
    n_cand = int(_score_field(sc, "cand_count", 0) or 0)
    recall5 = bool(_score_field(sc, "recall5"))
    ask_fact_correct = bool(_score_field(sc, "ask_fact_correct"))
    ask_citation_valid = bool(_score_field(sc, "ask_citation_valid"))
    no_hallucination = _score_field(sc, "no_hallucination", True)
    forbidden_assertion = bool(_score_field(sc, "forbidden_assertion", False))
    top1_hit = bool(_score_field(sc, "top1_hit"))
    wrong_version = bool(_score_field(sc, "wrong_version_in_evidence", False))

    if any("requires_current_external_data" in str(w) for w in ask.get("warnings") or []):
        return (
            "P1",
            "routing",
            "意图误判为 requires_current_external_data，未执行检索即返回 no_answer。",
        )
    if n_cand == 0 and expected:
        return ("P1", "retrieval_recall", "search 返回 0 候选；相关文档未被召回。")
    if expected and not recall5:
        extra = ""
        if ask_fact_correct:
            extra = "（答案文本碰巧覆盖 required_facts，仍计检索召回失败）"
        return ("P1", "retrieval_recall", f"search Top-5 未命中 expected 文档{extra}。")
    if recall5 and ask.get("mode") == "no_answer" and any(
        "evidence gate" in str(w) for w in ask.get("warnings") or []
    ):
        return (
            "P1",
            "answer_pipeline",
            "search 已命中正确文档，但 ask 的 evidence gate 拦截生成。",
        )
    if forbidden_assertion or no_hallucination is False:
        return ("P1", "hallucination", "回答包含 forbidden assertion。")
    if recall5 and not ask_fact_correct:
        return (
            "P1",
            "answer_fact",
            "search 召回正确文档，但 ask.answer 未覆盖 required_facts 或含 forbidden。",
        )
    if ask_fact_correct and not ask_citation_valid:
        return (
            "P2",
            "citation_integrity",
            "最终答案事实正确，但 ask.sources 含不可追溯引用。",
        )
    if not top1_hit and recall5 and not wrong_version:
        return ("P2", "ranking", "正确文档进入 Top5 但非 Top1。")
    if wrong_version and not top1_hit:
        return (
            "P2",
            "version_ranking",
            "检索优先返回旧版/易混淆事实文档，正确版本靠后或未召回。",
        )
    return (None, None, None)


def score_clarification_case(
    case: dict[str, Any],
    case_result: dict[str, Any],
) -> CaseScore:
    """Score answerability=clarification_required independently (Task 2.0.5).

    Pass: answer raises Golden-defined clarification dimension(s).
    Fail: definitive answer without clarifying, or pure refusal with no clarify.
    """
    case_id = str(case.get("case_id") or case.get("id") or "")
    ask = extract_ask_payload(case_result)
    ans = ask["answer"]
    mode = str(ask.get("mode") or "").strip() or None
    mode_l = (mode or "").lower()

    # Golden-defined clarification dimensions / question keywords.
    amb = case.get("ambiguity") if isinstance(case.get("ambiguity"), dict) else {}
    dimensions: list[str] = []
    for key in ("clarification_dimensions", "clarify_dimensions"):
        for d in case.get(key) or []:
            if str(d).strip():
                dimensions.append(str(d).strip())
    cq = str(
        (amb or {}).get("clarifying_question")
        or case.get("clarifying_question")
        or ""
    ).strip()
    if cq:
        dimensions.append(cq)
    for d in (amb or {}).get("dimensions") or []:
        if str(d).strip():
            dimensions.append(str(d).strip())

    reason_codes: list[str] = []
    # Definitive answer mode without clarify → fail
    if mode_l in ANSWER_MODES:
        reason_codes.append("definitive_answer_without_clarification")
    # Pure refusal with no clarify dimension raised
    refusal_only = _is_refusal_only(ans)
    raised = False
    if dimensions:
        raised = any(contains_fact(ans, d) for d in dimensions)
    else:
        # Soft heuristic: question mark / 请确认 / 需要澄清
        raised = bool(
            re.search(r"[？?]|请确认|需要澄清|哪[个一]|是否指", ans or "")
        ) or mode_l in {"clarification", "clarify", "clarification_required"}

    if mode_l in {"clarification", "clarify", "clarification_required"}:
        raised = True

    if not raised:
        if refusal_only or not (ans or "").strip():
            reason_codes.append("refusal_without_clarification")
        else:
            reason_codes.append("missing_clarification_dimension")

    # Substantive assertion that picks one interpretation → fail
    if _has_substantive_assertion(ans) and mode_l in ANSWER_MODES:
        if "definitive_answer_without_clarification" not in reason_codes:
            reason_codes.append("definitive_answer_without_clarification")

    passed = not reason_codes
    cs = CaseScore(
        case_id=case_id,
        case_type="clarification_required",
        metric_contract_version=METRIC_CONTRACT_VERSION,
        false_positive=not passed and "definitive_answer_without_clarification" in reason_codes,
        expressed_insufficient=raised,
        no_fabrication=passed,
        reason_codes=sorted(set(reason_codes)),
        score=10 if passed else 0,
        ask_has_answer=bool((ans or "").strip()),
        answer_mode=mode,
        e2e_pass=passed,
        defect_severity="P1" if not passed else None,
        defect_category="clarification" if not passed else None,
        defect_reason=(
            "clarification_required 未正确澄清: " + ",".join(sorted(set(reason_codes)))
            if not passed
            else None
        ),
        extra={"clarification_raised": raised},
    )
    return cs


def score_case(case: dict[str, Any], case_result: dict[str, Any]) -> CaseScore:
    if case.get("expected_no_answer") or case.get("answerability") == "no_answer":
        return score_no_answer_case(case, case_result)
    if case.get("answerability") == "clarification_required":
        return score_clarification_case(case, case_result)
    return score_answerable_case(case, case_result)


def aggregate_scores(scores: list[CaseScore]) -> AggregateMetrics:
    answerable = [s for s in scores if s.case_type == "answerable"]
    no_answer = [s for s in scores if s.case_type == "no_answer"]
    n_ans = len(answerable)
    n_no = len(no_answer)
    top1 = sum(1 for s in answerable if s.top1_hit)
    recall5 = sum(1 for s in answerable if s.recall5)
    ask_fact = sum(1 for s in answerable if s.ask_fact_correct)
    ask_cite = sum(1 for s in answerable if s.ask_citation_valid)
    e2e = sum(1 for s in answerable if s.e2e_pass)
    grounded = sum(1 for s in answerable if s.grounded)
    forbidden_n = sum(1 for s in answerable if s.forbidden_assertion)
    fp = sum(1 for s in no_answer if s.false_positive)
    cite_num = sum(s.citation_valid_num for s in answerable)
    cite_den = sum(s.citation_valid_den for s in answerable)
    buckets: dict[str, int] = {
        "preaccepted": 0,
        "adjacent_extension": 0,
        "expected_id": 0,
        "rejected": 0,
    }
    for s in answerable:
        for k, v in (s.citation_buckets or {}).items():
            buckets[k] = buckets.get(k, 0) + int(v)

    def rate(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    return AggregateMetrics(
        metric_contract_version=METRIC_CONTRACT_VERSION,
        answerable_total=n_ans,
        no_answer_total=n_no,
        top1_correct=top1,
        recall5_correct=recall5,
        ask_fact_correct_count=ask_fact,
        ask_citation_valid_count=ask_cite,
        e2e_pass_count=e2e,
        grounded_count=grounded,
        forbidden_assertion_count=forbidden_n,
        false_positive_count=fp,
        citation_valid=cite_num,
        citation_total=cite_den,
        citation_buckets=buckets,
        top1_accuracy=rate(top1, n_ans),
        recall_at_5=rate(recall5, n_ans),
        ask_fact_correctness=rate(ask_fact, n_ans),
        ask_citation_validity=rate(ask_cite, n_ans),
        e2e_pass_rate=rate(e2e, n_ans),
        answer_groundedness=rate(grounded, n_ans),
        citation_validity=rate(cite_num, cite_den) if cite_den else 0.0,
        forbidden_assertion_rate=rate(forbidden_n, n_ans) if n_ans else 0.0,
        false_positive_rate=rate(fp, n_no) if n_no else 0.0,
        hallucination_rate=None,
        hallucination_status="not_fully_measurable",
    )


def evaluate_gate(metrics: AggregateMetrics) -> dict[str, Any]:
    """SPEC gates — thresholds are not lowered in Phase 0–1."""
    report = metrics.to_report_dict()
    # Hallucination Rate is not fully measurable; gate on Forbidden Assertion Rate.
    gates = {
        "Top-1 Accuracy": (0.75, True, report["Top-1 Accuracy"]),
        "Recall@5": (0.88, True, report["Recall@5"]),
        "Ask Fact Correctness": (0.90, True, report["Ask Fact Correctness"]),
        "Ask Citation Validity": (0.95, True, report["Ask Citation Validity"]),
        "E2E Pass Rate": (0.90, True, report["E2E Pass Rate"]),
        "Forbidden Assertion Rate": (
            0.05,
            False,
            report["Forbidden Assertion Rate"] or 0.0,
        ),
        "False Positive Rate": (0.05, False, report["False Positive Rate"]),
    }
    results: dict[str, Any] = {}
    all_pass = True
    for key, (thr, higher, val) in gates.items():
        ok = (val >= thr) if higher else (val <= thr)
        results[key] = {"value": val, "threshold": thr, "pass": ok}
        if not ok:
            all_pass = False
    # Compatibility: expose Hallucination Rate as N/A gate (informational)
    results["Hallucination Rate"] = {
        "value": None,
        "threshold": 0.05,
        "pass": None,
        "status": "not_fully_measurable",
        "note": "Use Forbidden Assertion Rate for the substring proxy gate.",
    }
    return {
        "gates": results,
        "release_verdict": "通过放行" if all_pass else "不通过放行",
        "all_pass": all_pass,
    }


def score_artifact_dir(
    golden_cases: list[dict[str, Any]],
    artifacts_dir: Any,
    *,
    selected: set[str] | None = None,
) -> tuple[list[CaseScore], AggregateMetrics, dict[str, Any]]:
    """Score a harness output directory against golden cases."""
    from pathlib import Path

    out = Path(artifacts_dir)
    scores: list[CaseScore] = []
    for case in golden_cases:
        cid = str(case.get("case_id") or case.get("id") or "")
        if selected and cid not in selected:
            continue
        path = out / f"{cid}.json"
        if not path.exists():
            continue
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        scores.append(score_case(case, payload))
    metrics = aggregate_scores(scores)
    gate = evaluate_gate(metrics)
    return scores, metrics, gate
