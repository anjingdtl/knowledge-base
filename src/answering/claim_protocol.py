"""Structured claim draft → ground → short render (SPEC v4 §B)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from src.answering.numeric_triples import (
    extract_numeric_triples,
    extract_query_slots,
    select_answer_triples,
    triples_from_evidence_rows,
)
from src.answering.passage_evidence import (
    PassageEvidence,
    ensure_passage_trace,
    normalize_evidence_list,
    normalize_to_passage_evidence,
    passage_map,
)

_PROCESS_MARKERS = re.compile(
    r"(问题拆解|推理过程|知识库检索|检索过程|组合推理|建议|总结|"
    r"若你实际想问|可能|按此推算|可参照但不等于|chain[- ]?of[- ]?thought)",
    re.I,
)


@dataclass
class ClaimDraft:
    text: str
    evidence_passage_ids: list[str] = field(default_factory=list)
    fact_type: str = "other"
    condition: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "evidence_passage_ids": list(self.evidence_passage_ids),
            "fact_type": self.fact_type,
            "condition": self.condition,
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
            # Strip process headings if mixed.
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
    """Keep only claims supported by their declared evidence passages."""
    pmap = passage_map(evidence)
    slots = extract_query_slots(question)
    conditions = slots.get("conditions") or []
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
        # Textual grounding: claim core tokens must appear in evidence.
        blobs = " ".join(
            (pmap[pid].text + " " + (pmap[pid].title or ""))
            for pid in d.evidence_passage_ids
        )
        blob_norm = re.sub(r"\s+", "", blobs)
        claim_norm = re.sub(r"\s+", "", d.text)
        # Numeric claims: every value+unit in claim must appear in evidence.
        nums = re.findall(
            r"\d+(?:\.\d+)?\s*(?:万元|亿|元|%|％|个工作日|工作日|天|个)",
            d.text,
        )
        ok = True
        if d.fact_type == "version":
            # Version claims may paraphrase title; require year / 文号 digits exist.
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
            # Non-numeric: require substantial character overlap (>= 4 chars continuous)
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

        # Condition filter for multi-condition questions: drop claims about other conditions.
        if conditions and d.condition and d.condition not in conditions:
            # Allow if claim text only contains wanted conditions' numbers
            audit.append({
                "claim": d.to_dict(),
                "reason": "condition_not_in_query",
            })
            continue
        if conditions:
            # If claim mentions a different exclusive condition, drop.
            exclusive = {"涉诈", "涉骚扰", "II类", "III类", "I类", "区外", "区内"}
            other = [c for c in exclusive if c in d.text and c not in conditions]
            # III类 query should not keep II类-only claims
            if other and not any(c in d.text for c in conditions):
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
    """Deterministic claim extractor from passage evidence (no free-form LLM)."""
    if not evidence:
        return []
    q = question or ""
    slots = extract_query_slots(q)
    drafts: list[ClaimDraft] = []

    # 1) Numeric triples aligned to query conditions.
    rows = [e.to_row() for e in evidence]
    triples = triples_from_evidence_rows(rows)
    selected = select_answer_triples(triples, question=q)
    for t in selected:
        cond = t.condition or ""
        # Build a short claim sentence from the condition span if possible.
        span = t.condition_span or ""
        if span and t.display() in re.sub(r"\s+", "", span).replace(" ", ""):
            # Prefer a compact reconstructed fact.
            if cond:
                text = f"{cond}：{t.display()}"
            else:
                text = t.display()
            # Prefer richer span snippet if short.
            if "处罚" in span or "限额" in span or "工作日" in span:
                # Extract sentence-like fragment containing the value.
                m = re.search(
                    rf".{{0,40}}{re.escape(t.value)}\s*{re.escape(t.unit)}.{{0,20}}",
                    span,
                )
                if m:
                    frag = re.sub(r"\s+", "", m.group(0))
                    text = frag if len(frag) <= 80 else text
        else:
            text = f"{cond}{t.display()}" if cond else t.display()
        drafts.append(ClaimDraft(
            text=text,
            evidence_passage_ids=[t.evidence_passage_id] if t.evidence_passage_id else [evidence[0].passage_id],
            fact_type="numeric",
            condition=cond,
        ))

    # 2) Deadline patterns (工作日).
    if re.search(r"时限|工作日|初审|评估", q):
        for e in evidence:
            for m in re.finditer(
                r"([^。；;\n]{0,30}(?:初审|审核|评估|产品评估)[^。；;\n]{0,30}"
                r"\d+\s*个?工作日[^。；;\n]{0,20})",
                e.text,
            ):
                drafts.append(ClaimDraft(
                    text=re.sub(r"\s+", "", m.group(1)),
                    evidence_passage_ids=[e.passage_id],
                    fact_type="policy",
                ))
            # Also standalone N个工作日 near keywords
            if "初审" in q and "初审" in e.text:
                m = re.search(r"初审[^。；]{0,20}(\d+\s*个?工作日)", e.text)
                if m:
                    drafts.append(ClaimDraft(
                        text=f"审核初审时限{re.sub(r'\s+', '', m.group(1))}",
                        evidence_passage_ids=[e.passage_id],
                        fact_type="policy",
                        condition="初审",
                    ))
            if "评估" in q and re.search(r"产品评估|评估", e.text):
                m = re.search(r"(?:产品)?评估[^。；]{0,20}(\d+\s*个?工作日)", e.text)
                if m:
                    drafts.append(ClaimDraft(
                        text=f"产品评估时限{re.sub(r'\s+', '', m.group(1))}",
                        evidence_passage_ids=[e.passage_id],
                        fact_type="policy",
                        condition="产品评估",
                    ))

    # 3) Version facts for 最新/修订版 queries.
    if re.search(r"最新|修订版|版本", q):
        for e in evidence:
            title = e.title or ""
            year = e.version_year
            m = re.search(r"(?:19|20)\d{2}", title) or re.search(
                r"[〔\\[]\s*((?:19|20)\d{2})\s*[〕\\]]\s*(\d+)\s*号",
                title + e.text[:200],
            )
            doc_no = ""
            m2 = re.search(
                r"[〔\\[]\s*((?:19|20)\d{2})\s*[〕\\]]\s*(\d+)\s*号",
                title + " " + e.text[:300],
            )
            if m2:
                doc_no = f"{m2.group(1)}年{m2.group(2)}号"
                year = year or int(m2.group(1))
            # Also 中电信桂-2026-158号 style
            m3 = re.search(r"((?:19|20)\d{2})[-—](\d+)\s*号", title)
            if m3 and not doc_no:
                doc_no = f"{m3.group(1)}-{m3.group(2)}号"
                year = year or int(m3.group(1))
            bits = []
            if year:
                bits.append(str(year))
            if doc_no:
                bits.append(doc_no if "号" in doc_no else f"{doc_no}号")
            # Prefer 158号 form from title
            m4 = re.search(r"(\d+)\s*号", title)
            if m4 and f"{m4.group(1)}号" not in " ".join(bits):
                bits.append(f"{m4.group(1)}号")
            if bits:
                text = f"最新修订版为{' '.join(bits)}"
                # Avoid echoing forbidden historical grading terms from query.
                text = re.sub(r"一级竞赛|二级竞赛|一级|二级", "", text)
                drafts.append(ClaimDraft(
                    text=text,
                    evidence_passage_ids=[e.passage_id],
                    fact_type="version",
                ))

    # 4) Policy keyword facts: if query terms co-occur in a short clause.
    if not drafts:
        q_terms = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}", q) if t not in ("什么", "多少", "如何", "怎么")]
        for e in evidence:
            for term in q_terms[:6]:
                if term in e.text:
                    # Grab surrounding sentence
                    idx = e.text.find(term)
                    frag = e.text[max(0, idx - 20): idx + 60]
                    frag = re.split(r"[。；\n]", frag)[0]
                    frag = re.sub(r"\s+", "", frag)
                    if 6 <= len(frag) <= 80:
                        drafts.append(ClaimDraft(
                            text=frag,
                            evidence_passage_ids=[e.passage_id],
                            fact_type="policy",
                        ))
                        break

    # Dedupe by text
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


def structured_answer_from_evidence(
    *,
    question: str,
    evidence_rows: list[dict[str, Any]] | None,
    llm_json: str | None = None,
    prefer_latest_family: bool = False,
    require_passage: bool = False,
) -> dict[str, Any]:
    """Full generate→ground→render path with strict no-answer contract."""
    evidence = normalize_evidence_list(evidence_rows)
    if prefer_latest_family or re.search(r"最新|修订版|现行", question or ""):
        evidence = filter_latest_family_evidence(evidence, question=question)

    warnings: list[str] = []
    numeric_audit: dict[str, Any] = {}

    # Passage trace check when caller asserts passages should be available.
    if require_passage and evidence:
        ok, reason = ensure_passage_trace([e.to_row() for e in evidence], require_passage=True)
        if not ok:
            return _no_answer(reason=f"passage_trace_failed:{reason}", warnings=warnings)

    # Core-slot support: evidence must speak to query intent.
    if not evidence or not _evidence_supports_query(question, evidence):
        return _no_answer(reason="no_direct_slot_evidence", warnings=warnings)

    drafts: list[ClaimDraft] = []
    if llm_json:
        parsed, err = parse_claim_drafts(llm_json)
        if err:
            warnings.append(f"claim_parse:{err}")
        else:
            drafts = parsed

    if not drafts:
        drafts = rule_extract_claims(question, evidence)

    # Attach numeric audit always.
    triples = triples_from_evidence_rows([e.to_row() for e in evidence])
    selected = select_answer_triples(triples, question=question)
    from src.answering.numeric_triples import filter_triples_for_query
    _, numeric_audit = filter_triples_for_query(triples, question=question)

    # If query is numeric and no selected triple, refuse rather than invent.
    slots = extract_query_slots(question)
    if "numeric" in (slots.get("fact_types") or []) and slots.get("conditions"):
        if not selected and not any(d.fact_type == "numeric" for d in drafts):
            return _no_answer(
                reason="no_matching_numeric_triple",
                warnings=warnings,
                numeric_fact_audit=numeric_audit,
            )

    kept, ground_audit = ground_claims(drafts, evidence=evidence, question=question)
    if not kept:
        # For numeric, synthesize claims from selected triples only.
        for t in selected:
            kept.append(ClaimDraft(
                text=(
                    f"{t.condition}{t.display()}"
                    if t.condition else t.display()
                ),
                evidence_passage_ids=[t.evidence_passage_id] if t.evidence_passage_id else [],
                fact_type="numeric",
                condition=t.condition,
            ))
        kept, ground_audit2 = ground_claims(kept, evidence=evidence, question=question)
        ground_audit.extend(ground_audit2)

    if not kept:
        return _no_answer(
            reason="no_grounded_claims",
            warnings=warnings,
            numeric_fact_audit=numeric_audit,
            claim_audit=ground_audit,
        )

    # Drop exclusive wrong-condition numbers from final text.
    answer = render_short_answer(kept)
    answer = _strip_cross_condition_numbers(answer, question, selected)

    if not answer.strip():
        return _no_answer(
            reason="empty_render",
            warnings=warnings,
            numeric_fact_audit=numeric_audit,
        )

    # Build sources / raw_evidence from used passages only.
    used_ids: list[str] = []
    for c in kept:
        for pid in c.evidence_passage_ids:
            if pid and pid not in used_ids:
                used_ids.append(pid)
    pmap = passage_map(evidence)
    used_evidence = [pmap[pid] for pid in used_ids if pid in pmap]
    if not used_evidence:
        used_evidence = evidence[:3]

    raw_evidence_used = []
    sources = []
    for e in used_evidence:
        row = e.to_row()
        raw_evidence_used.append({
            "knowledge_id": e.knowledge_id,
            "passage_id": e.passage_id,
            "block_id": e.block_ids[0] if e.block_ids else "",
            "block_ids": list(e.block_ids),
            "title": e.title,
            "path": "",
            "text": (e.text or "")[:2000],
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
            "text": e.text,
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
            reason=f"passage_trace_failed:{trace_reason}",
            warnings=warnings + [f"passage_trace:{trace_reason}"],
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
        "reason": "structured_claim_answer",
        "conflict_disclosed": False,
        "conflicts": [],
        "fallbacks": [],
    }


def _strip_cross_condition_numbers(
    answer: str,
    question: str,
    selected: list[Any],
) -> str:
    """Remove numeric displays that are not in the selected triple set."""
    if not answer:
        return answer
    allowed = {re.sub(r"\s+", "", f"{t.value}{t.unit}") for t in selected}
    slots = extract_query_slots(question)
    conditions = slots.get("conditions") or []
    if not conditions or not allowed:
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
    cleaned = re.sub(r"[ \\t]{2,}", " ", cleaned)
    return cleaned.strip()


def _evidence_supports_query(question: str, evidence: list[PassageEvidence]) -> bool:
    """Cheap core-slot check: at least one high-info term from query in evidence."""
    from src.answering.direct_slot_gate import evaluate_direct_slot_evidence

    q = question or ""
    # Hard out-of-scope cues: address/salary/forecast that policy corpus rarely answers.
    if re.search(r"办公楼地址|总部.*地址|工资薪级|岗位津贴|营收预测|火锅|火星", q):
        # Only accept if evidence literally contains the asked entity type.
        blob0 = "\n".join((e.title or "") + "\n" + (e.text or "") for e in evidence)
        if re.search(r"办公楼地址|总部地址|薪级表|岗位津贴|营收预测", q) and not re.search(
            r"地址|薪级|津贴|营收预测", blob0
        ):
            return False

    rows = [e.to_row() for e in evidence]
    # Strong path: multi-slot direct evidence
    ds = evaluate_direct_slot_evidence(question, rows, min_slots=2)
    if ds.get("direct_slot_evidence"):
        return True
    # Soft path: 2–4 char CJK windows (same idea as relevance_gate).
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
    blob = "\n".join((e.title or "") + "\n" + (e.text or "") for e in evidence)
    hits = sum(1 for t in terms if t in blob)
    return hits >= 2 if len(terms) >= 2 else (hits >= 1 if terms else bool(blob.strip()))


def _no_answer(
    *,
    reason: str,
    warnings: list[str] | None = None,
    numeric_fact_audit: dict | None = None,
    claim_audit: list | None = None,
) -> dict[str, Any]:
    return {
        "answer": "",
        "answer_mode": "no_answer",
        "sources": [],
        "raw_evidence_used": [],
        "claims_used": [],
        "warnings": list(warnings or []),
        "reason": reason,
        "user_notice": "知识库中未找到可直接支持该问题的证据。",
        "numeric_fact_audit": numeric_fact_audit or {},
        "claim_audit": claim_audit or [],
        "conflict_disclosed": False,
        "conflicts": [],
        "fallbacks": [],
    }
