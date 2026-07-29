"""Unified FactCandidate pipeline (SPEC v5 §2.3–2.4)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from src.answering.logical_evidence import LogicalEvidenceRecord, records_from_evidence_list
from src.answering.query_planner import QueryPlan, extract_conditions, plan_query

_VALUE_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>万元|亿|元|%|％|天|个工作日|工作日|个月|月|年|次|户|人|个|倍)",
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
            # SPEC: ambiguous table → no condition-value facts; flag only.
            out.append(
                FactCandidate(
                    candidate_id=f"{rec.record_id}:ambiguous",
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
            # Still allow non-numeric policy snippets from the same blob if text asks policy.
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


def select_fact_candidates(
    candidates: list[FactCandidate],
    *,
    plan: QueryPlan,
    max_items: int = 6,
) -> tuple[list[FactCandidate], dict[str, Any]]:
    """Rank and pick candidates covering query slots. No full-body fallback."""
    audit: dict[str, Any] = {
        "plan": plan.to_dict(),
        "input_count": len(candidates),
        "dropped": [],
        "selected": [],
        "table_structure_ambiguous": False,
    }
    usable = [c for c in candidates if not c.unstructured_rejected]
    ambiguous = [c for c in candidates if c.unstructured_rejected]
    if ambiguous and plan.wants_numeric and not usable:
        audit["table_structure_ambiguous"] = True
        audit["dropped"].append({"reason": "table_structure_ambiguous", "count": len(ambiguous)})
        return [], audit

    allow = set(plan.allow_fact_kinds or [])
    filtered: list[FactCandidate] = []
    for c in usable:
        if allow and c.fact_kind not in allow and not (
            c.fact_kind == "prohibition" and "policy" in allow
        ):
            audit["dropped"].append({"id": c.candidate_id, "reason": "kind_not_allowed"})
            continue
        # Non-numeric questions must not keep year/doc-no numeric fragments.
        if not plan.wants_numeric and c.fact_kind == "numeric":
            audit["dropped"].append({"id": c.candidate_id, "reason": "numeric_without_numeric_intent"})
            continue
        if plan.wants_numeric and c.fact_kind == "numeric":
            if c.unit not in ("元", "万元", "亿", "%", "％"):
                # Allow if question not money-focused; already gated by unit list for 处罚.
                if re.search(r"处罚|金额|限额|元|奖金|补助", plan.raw or ""):
                    audit["dropped"].append({"id": c.candidate_id, "reason": "unit_not_money"})
                    continue
            if plan.conditions:
                if c.condition and c.condition not in plan.conditions:
                    audit["dropped"].append({
                        "id": c.candidate_id,
                        "reason": "condition_mismatch",
                        "wanted": plan.conditions,
                    })
                    continue
                if not c.condition:
                    audit["dropped"].append({
                        "id": c.candidate_id,
                        "reason": "missing_condition_for_conditioned_query",
                    })
                    continue
        # Score
        c.score = _score_candidate(c, plan)
        filtered.append(c)

    filtered.sort(key=lambda x: x.score, reverse=True)

    selected: list[FactCandidate] = []
    covered_conditions: set[str] = set()
    covered_slots: set[str] = set()

    # Prefer one numeric per condition.
    if plan.wants_numeric and plan.conditions:
        for cond in plan.conditions:
            best = next(
                (c for c in filtered if c.fact_kind == "numeric" and c.condition == cond),
                None,
            )
            if best and best.candidate_id not in {s.candidate_id for s in selected}:
                selected.append(best)
                covered_conditions.add(cond)
                covered_slots.add(cond)
                covered_slots.add("value")
                covered_slots.add("unit")

    # Deadlines by label (also accept empty-label deadlines when unique).
    if plan.wants_deadline:
        labels = []
        if "初审" in (plan.raw or "") or "初审" in plan.conditions:
            labels.append("初审")
        if re.search(r"产品评估|评估", plan.raw or ""):
            labels.append("产品评估")
        if not labels:
            labels = ["初审", "产品评估"]
        for label in labels:
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
                covered_slots.add(label)
                covered_slots.add("deadline")
        # Fallback: any deadline candidates if labels not covered.
        if not any(c.fact_kind == "deadline" for c in selected):
            for c in filtered:
                if c.fact_kind == "deadline" and c.candidate_id not in {
                    s.candidate_id for s in selected
                }:
                    selected.append(c)
                    covered_slots.add("deadline")

    if plan.wants_version:
        best = next((c for c in filtered if c.fact_kind == "version"), None)
        if best:
            selected.append(best)
            covered_slots.add("version")

    # Policy / responsibility / scope text facts.
    for c in filtered:
        if len(selected) >= max_items:
            break
        if c.candidate_id in {s.candidate_id for s in selected}:
            continue
        if c.fact_kind in ("policy", "prohibition", "responsibility", "scope", "relationship"):
            selected.append(c)
            covered_slots.add("policy_fact")
            if c.fact_kind == "responsibility":
                covered_slots.add("role")

    # If numeric without explicit conditions, take top money triples.
    if plan.wants_numeric and not plan.conditions:
        for c in filtered:
            if c.fact_kind == "numeric" and c.candidate_id not in {
                s.candidate_id for s in selected
            }:
                selected.append(c)
                if len(selected) >= max_items:
                    break

    audit["selected"] = [c.to_dict() for c in selected]
    audit["covered_slots"] = sorted(covered_slots)
    audit["covered_conditions"] = sorted(covered_conditions)
    return selected[:max_items], audit


def build_answer_plan(
    *,
    plan: QueryPlan,
    selected: list[FactCandidate],
) -> dict[str, Any]:
    """List required slots and candidate ids used for rendering."""
    missing: list[str] = []
    if plan.wants_numeric and plan.conditions:
        have = {c.condition for c in selected if c.fact_kind == "numeric"}
        for cond in plan.conditions:
            if cond not in have:
                missing.append(cond)
    if plan.wants_deadline:
        text = " ".join(c.exact_text for c in selected)
        if "初审" in (plan.raw or "") and "初审" not in text and "1" not in text:
            missing.append("初审")
        if re.search(r"产品评估|评估", plan.raw or "") and "5" not in text and "评估" not in text:
            # soft — keep if any deadline selected
            if not any(c.fact_kind == "deadline" for c in selected):
                missing.append("deadline")
    if plan.wants_version and not any(c.fact_kind == "version" for c in selected):
        missing.append("version")
    if (
        plan.wants_policy
        and not plan.wants_numeric
        and not any(
            c.fact_kind in ("policy", "prohibition", "responsibility", "scope", "relationship")
            for c in selected
        )
    ):
        missing.append("policy_fact")

    complete = not missing and bool(selected)
    return {
        "required_slots": list(plan.required_slots),
        "candidate_ids": [c.candidate_id for c in selected],
        "missing_slots": missing,
        "complete": complete and bool(selected),
        "intents": list(plan.intents),
    }


def render_from_candidates(
    selected: list[FactCandidate],
    *,
    max_bullets: int = 3,
) -> str:
    if not selected:
        return ""
    bullets: list[str] = []
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
        if len(bullets) >= max_bullets:
            break
    return "\n".join(bullets)


# ----- extractors -----


def _numeric_from_record(rec: LogicalEvidenceRecord, plan: QueryPlan) -> list[FactCandidate]:
    body = rec.body_text or ""
    local_conditions = extract_conditions(body)
    out: list[FactCandidate] = []
    money_matches = list(_VALUE_RE.finditer(body))
    for m in money_matches:
        value = m.group("value")
        unit = m.group("unit")
        # Skip bare document years in body unless version intent (usually stripped already).
        if unit == "年" and not plan.wants_version:
            continue
        # Window for condition near the value — look both sides (OCR often puts
        # 涉骚扰 after the amount). Never whole-passage fallback.
        window = body[max(0, m.start() - 40) : m.end() + 48]
        conds = extract_conditions(window)
        if not conds and len(local_conditions) == 1:
            conds = list(local_conditions)
        if not conds:
            # One money value in a short clause with no exclusive multi-conditions: allow empty.
            if len(money_matches) == 1 and len(body) <= 120:
                conds = [""]
            else:
                # Cannot safely bind → skip (no cross-row guess).
                continue
        # When multiple conditions appear in the same window, prefer the nearest.
        if len(conds) > 1:
            nearest = None
            nearest_dist = 10**9
            for cond in conds:
                # find last occurrence of condition label before or after value
                for mcond in re.finditer(re.escape(cond), window):
                    # map to body coords roughly via window offset
                    dist = abs(mcond.start() - 40)
                    if dist < nearest_dist:
                        nearest_dist = dist
                        nearest = cond
            if nearest:
                conds = [nearest]
        for cond in conds:
            # Prefer exact original fragment for exact_text.
            frag = body[max(0, m.start() - 20) : m.end() + 12].strip()
            if cond and cond not in frag:
                # Include condition + value if condition is nearby after value.
                if cond in window:
                    frag = f"{cond}{value}{unit}"
                else:
                    frag = f"{cond}{value}{unit}"
            else:
                frag = re.sub(r"\s+", "", frag)
                if len(frag) > 60:
                    frag = f"{cond}{value}{unit}" if cond else f"{value}{unit}"
            cid = f"{rec.record_id}:num:{cond}:{value}{unit}"
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
                    table_row_ref=rec.table_id or (f"row:{rec.row_index}" if rec.row_index is not None else ""),
                    document_family_id=rec.document_family_id,
                    version_year=rec.version_year,
                    predicate="限额/处罚" if re.search(r"限额|处罚|罚", body) else "数值",
                    object=f"{value}{unit}",
                )
            )
    return out


def _deadline_from_record(rec: LogicalEvidenceRecord, plan: QueryPlan) -> list[FactCandidate]:
    body = rec.body_text or ""
    out: list[FactCandidate] = []

    def _add(cond: str, value: str, unit: str, exact: str, start: int, end: int, tag: str) -> None:
        if unit == "工作日":
            unit = "个工作日"
        cid = f"{rec.record_id}:{tag}:{cond}:{value}"
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
                exact_text=re.sub(r"\s+", "", exact)[:80],
                evidence_spans=[(start, end)],
                document_family_id=rec.document_family_id,
                version_year=rec.version_year,
            )
        )

    # Pattern A: label ... N 工作日
    for m in re.finditer(
        r"(初审|审核初审|产品评估|评估)[^。；\n]{0,24}(\d+)\s*(个?工作日)",
        body,
    ):
        cond = "初审" if "初审" in m.group(1) else "产品评估"
        _add(cond, m.group(2), m.group(3), m.group(0), m.start(), m.end(), "dlA")

    # Pattern B: N 工作日 ... 初审/评估 (common in policy prose)
    for m in re.finditer(
        r"(\d+)\s*(个?工作日)[^。；\n]{0,30}(初审|审核|产品评估|评估)",
        body,
    ):
        cond = "初审" if "初审" in m.group(3) or m.group(3) == "审核" else "产品评估"
        _add(cond, m.group(1), m.group(2), m.group(0), m.start(), m.end(), "dlB")

    # Pattern C: 工单后N个工作日 near 初审 in same sentence
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
    # Prefer title/section metadata for version, but exact_text must be from body or title field.
    # Title is metadata for extraction source of doc numbers (version intent only).
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
    out.append(
        FactCandidate(
            candidate_id=f"{rec.record_id}:ver:{year}:{doc}",
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
    body = rec.body_text or ""
    if not body.strip():
        return []
    # Skip pure numeric table rows for policy extraction when numeric intent dominates.
    if plan.wants_numeric and not plan.wants_policy and _VALUE_RE.search(body) and len(body) < 100:
        return []
    entities = plan.entities or []
    hits = [e for e in entities if e and e in body]
    if not hits and entities:
        # soft: at least 2-char windows
        for e in entities:
            if len(e) >= 4 and any(e[i : i + 2] in body for i in range(len(e) - 1)):
                hits.append(e)
                break
    q_terms = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}", plan.raw or "") if len(t) >= 2]
    high_value = [
        t
        for t in (
            "收支两条线",
            "小金库",
            "首席合规官",
            "总法律顾问",
            "一个主体只允许制作一个",
            "合同实体章",
        )
        if t in body
    ]
    if not hits and not plan.wants_responsibility and not high_value:
        if not re.search(r"不得|禁止|应当|必须|原则|负责|适用范围|严禁", body):
            if not any(t in body for t in q_terms[:8]):
                return []

    kind = "policy"
    if re.search(r"不得|禁止|严禁", body):
        kind = "prohibition"
    elif plan.wants_responsibility or re.search(r"负责|归口|主管部门|首席|顾问", body):
        kind = "responsibility"
    elif plan.wants_scope or re.search(r"适用|范围", body):
        kind = "scope"
    elif plan.wants_relationship:
        kind = "relationship"

    # Prefer a compact sentence containing the strongest high-value / entity hit.
    frag = body.strip()
    anchor = (high_value[0] if high_value else None) or (hits[0] if hits else None)
    if anchor:
        idx = body.find(anchor)
        if idx >= 0:
            frag = body[max(0, idx - 20) : min(len(body), idx + 90)]
    frag = re.split(r"[。\n]", frag)[0].strip()
    frag = re.sub(r"\s+", "", frag)
    if len(frag) < 6:
        frag = re.sub(r"\s+", "", body)[:80]
    if len(frag) < 6:
        return []
    # Cap length but keep high-value anchors intact.
    if len(frag) > 120:
        frag = frag[:120]
    # Never emit bare title/doc-no fragments as policy facts.
    if re.fullmatch(r"[\d\-—号〔〕\[\]年月日.]+", frag):
        return []
    if re.match(r"^(?:中电信桂|市场)?[\d\-—]{2,}\d+号", frag) and len(frag) < 30:
        return []

    score_boost = 2.0 if high_value else 0.0
    return [
        FactCandidate(
            candidate_id=f"{rec.record_id}:pol:{kind}:{abs(hash(frag)) % 10**8}",
            record_id=rec.record_id,
            passage_id=rec.passage_id,
            knowledge_id=rec.knowledge_id,
            fact_kind=kind,
            exact_text=frag,
            subject=anchor or "",
            object=frag,
            document_family_id=rec.document_family_id,
            version_year=rec.version_year,
            evidence_spans=[rec.source_span] if rec.source_span else [],
            score=score_boost,
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
        if c.unit in ("元", "万元"):
            score += 1.0
    if c.fact_kind == "deadline" and plan.wants_deadline:
        score += 2.5
        if c.condition in ("初审", "产品评估"):
            score += 1.0
    if c.fact_kind == "version" and plan.wants_version:
        score += 2.5
    if c.fact_kind in ("policy", "prohibition", "responsibility") and not plan.wants_numeric:
        score += 2.0
    # Prefer shorter exact spans (less polluted).
    if c.exact_text and len(c.exact_text) <= 40:
        score += 0.5
    if c.table_row_ref:
        score += 0.3
    # Entity / high-value phrase overlap
    for e in plan.entities or []:
        if e and e in (c.exact_text or ""):
            score += 0.4
    for phrase in ("收支两条线", "小金库", "首席合规官", "总法律顾问", "一个主体只允许制作一个"):
        if phrase in (c.exact_text or "") and phrase in (plan.raw or "" + "".join(plan.entities or [])):
            score += 1.5
        elif phrase in (c.exact_text or ""):
            # Still boost if query mentions related terms
            if any(t in (plan.raw or "") for t in phrase):
                score += 1.2
    return score


def _dedupe_candidates(cands: list[FactCandidate]) -> list[FactCandidate]:
    seen: set[str] = set()
    out: list[FactCandidate] = []
    for c in cands:
        key = f"{c.fact_kind}|{c.condition}|{c.value}|{c.unit}|{re.sub(r'\s+', '', c.exact_text or '')[:40]}|{c.passage_id}"
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
