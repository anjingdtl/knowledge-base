"""Evidence group selection — pick authoritative document group before facts (SPEC v6 §2.1)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from src.answering.logical_evidence import LogicalEvidenceRecord
from src.answering.passage_evidence import PassageEvidence, normalize_evidence_list


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    return [v for v in values if v and not (v in seen or seen.add(v))]


def _stable_group_id(knowledge_id: str, family: str, revision: str) -> str:
    raw = f"{knowledge_id}|{family}|{revision}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _document_identity(pe: PassageEvidence) -> tuple[str, str, str, int | None]:
    """Derive scope/family/revision from available metadata and title.

    This is an in-memory fallback for legacy passages whose indexed family and
    version metadata is absent.  It intentionally uses title structure only;
    no evaluation knowledge IDs or document-name table is involved.
    """
    title = (pe.title or "").strip()
    provided = (pe.document_family_id or "").strip().removeprefix("topic:")
    year = pe.version_year
    if year is None:
        m = re.search(r"((?:19|20)\d{2})", title)
        year = int(m.group(1)) if m else None
    scope = ""
    scope_match = re.search(r"中国电信[^，。；;（）()]{0,24}?(?:分公司|公司)", title)
    if scope_match:
        scope = scope_match.group(0)
    else:
        # Preserve a regional/company qualifier when it is present in the
        # common dispatch-number title form.
        region = re.search(r"(?:广西|南宁|柳州|桂林|贺州|号百)[\u4e00-\u9fff]{0,8}(?:分公司|公司)", title)
        if region:
            scope = region.group(0)
    normalized = provided or title
    normalized = re.sub(r"--[0-9a-fA-F-]{6,}$", "", normalized)
    normalized = re.sub(r"(?:中电信[^-—]{0,12})?[-—]?(?:19|20)\d{2}[-—]\d+号", "", normalized)
    normalized = re.sub(r"(?:关于)?印发|通知|修订版?|试行|\(?\d{4}年?\)?", "", normalized)
    normalized = re.sub(r"[\-—_（）()【】\[\]]+", "", normalized).strip()
    normalized = normalized[:80] or "unknown_document"
    family = f"{scope}:{normalized}" if scope else normalized
    return scope, family, str(year) if year is not None else "", year


def _query_wants_freshness(query: str) -> bool:
    return bool(
        re.search(
            r"最新|现行|取消|替代|版本|修订|哪一年|哪年|(?:19|20)\d{2}\s*年",
            query or "",
        )
    )


def _tokenize(text: str) -> set[str]:
    toks = re.findall(r"[\u4e00-\u9fff]{2,12}|[A-Za-z0-9]{2,}", text or "")
    stop = {
        "什么", "多少", "如何", "怎么", "是否", "哪个", "哪些", "以及", "或者",
        "一个", "公司", "关于", "印发", "通知", "中国", "电信", "广西", "集团",
        "办法", "管理", "规定", "制度", "工作",
    }
    return {t for t in toks if t not in stop}


@dataclass
class EvidenceGroup:
    group_id: str
    knowledge_id: str
    knowledge_ids: list[str] = field(default_factory=list)
    organization_scope: str = ""
    document_family_id: str = ""
    document_revision: str = ""
    effective_date: str = ""
    version_year: int | None = None
    passage_ids: list[str] = field(default_factory=list)
    retrieval_ranks: list[int] = field(default_factory=list)
    retrieval_scores: list[float] = field(default_factory=list)
    title: str = ""
    section: str = ""
    family_unknown: bool = True
    query_anchor_coverage: float = 0.0
    predicate_coverage: float = 0.0
    freshness_score: float = 0.0
    group_score: float = 0.0
    rejection_reasons: list[str] = field(default_factory=list)
    record_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceGroupResolution:
    groups: list[EvidenceGroup]
    primary_group_id: str | None
    secondary_group_ids: list[str]
    multi_subquery: bool
    ambiguous: bool
    audit: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups": [g.to_dict() for g in self.groups],
            "primary_group_id": self.primary_group_id,
            "secondary_group_ids": list(self.secondary_group_ids),
            "multi_subquery": self.multi_subquery,
            "ambiguous": self.ambiguous,
            "audit": dict(self.audit or {}),
        }


def resolve_evidence_groups(
    evidence_rows: list[Any] | None,
    *,
    question: str,
    records: list[LogicalEvidenceRecord] | None = None,
    subqueries: list[dict[str, Any]] | None = None,
    min_score_gap: float = 0.35,
) -> EvidenceGroupResolution:
    """Group accepted passages by document family + revision; pick primary group."""
    evidence = normalize_evidence_list(evidence_rows)
    q = question or ""
    q_tokens = _tokenize(q)
    wants_fresh = _query_wants_freshness(q)
    multi = bool(subqueries and len(subqueries) > 1)

    # Bucket by parsed document family + revision.  Different imported copies
    # of the same edition form one evidence group while preserving every kid.
    buckets: dict[tuple[str, str], list[tuple[int, PassageEvidence]]] = {}
    for rank, pe in enumerate(evidence):
        _scope, family, revision, _year = _document_identity(pe)
        fallback = (pe.knowledge_id or "").strip() or f"unknown:{pe.passage_id or rank}"
        buckets.setdefault((family or fallback, revision), []).append((rank, pe))

    groups: list[EvidenceGroup] = []
    for (bucket_family, bucket_revision), items in buckets.items():
        items_sorted = sorted(items, key=lambda x: x[0])
        first = items_sorted[0][1]
        scope, family, revision, year = _document_identity(first)
        family = bucket_family or family
        revision = bucket_revision or revision
        kids = _ordered_unique([
            (pe.knowledge_id or "").strip() for _, pe in items_sorted if (pe.knowledge_id or "").strip()
        ])
        kid = kids[0] if kids else f"unknown:{first.passage_id or len(groups)}"
        family_unknown = family in ("", "unknown_document")
        gid = _stable_group_id("|".join(kids) or kid, family, revision)

        passage_ids = []
        ranks = []
        scores = []
        for rank, pe in items_sorted:
            if pe.passage_id and pe.passage_id not in passage_ids:
                passage_ids.append(pe.passage_id)
            ranks.append(rank)
            try:
                scores.append(float(pe.score) if pe.score is not None else 0.0)
            except (TypeError, ValueError):
                scores.append(0.0)

        # Anchor / title coverage
        title_blob = " ".join(
            filter(None, [first.title, first.section_path, first.body_text[:200] if first.body_text else ""])
        )
        title_tokens = _tokenize(title_blob)
        body_blob = " ".join(
            (pe.body_text or pe.text or "")[:800] for _, pe in items_sorted
        )
        body_tokens = _tokenize(body_blob)
        if q_tokens:
            title_hit = len(q_tokens & title_tokens) / max(1, len(q_tokens))
            body_hit = len(q_tokens & body_tokens) / max(1, len(q_tokens))
        else:
            title_hit = 0.0
            body_hit = 0.0

        # Predicate / polarity cues from query that also appear in body
        pred_cues = [
            t
            for t in re.findall(
                r"不得|禁止|取消|限额|处罚|负责|归口|适用|占比|不少于|两条线|"
                r"邮箱|微信|报账|牵头|审核|准入|门槛|响应|奖金|保密期限",
                q,
            )
        ]
        pred_hit = 0.0
        if pred_cues:
            pred_hit = sum(1 for p in pred_cues if p in body_blob) / len(pred_cues)

        best_rank = min(ranks) if ranks else 99
        best_score = max(scores) if scores else 0.0
        freshness = 0.0
        if wants_fresh and year is not None:
            freshness = min(1.0, max(0.0, (year - 2015) / 15.0))

        # Prefer top retrieval rank heavily so correct top-1 stays primary.
        rank_score = max(0.0, 5.0 - best_rank) * 1.2
        group_score = (
            rank_score
            + best_score * 2.0
            + title_hit * 3.0
            + body_hit * 1.5
            + pred_hit * 2.0
            + (freshness * 1.5 if wants_fresh else 0.0)
        )

        rec_ids = []
        if records:
            for rec in records:
                if rec.knowledge_id in kids or rec.passage_id in passage_ids:
                    rec_ids.append(rec.record_id)

        groups.append(
            EvidenceGroup(
                group_id=gid,
                knowledge_id=kid,
                knowledge_ids=kids,
                organization_scope=scope,
                document_family_id=family,
                document_revision=revision,
                version_year=year if isinstance(year, int) else None,
                passage_ids=passage_ids,
                retrieval_ranks=ranks,
                retrieval_scores=scores,
                title=first.title or "",
                section=first.section_path or "",
                family_unknown=family_unknown,
                query_anchor_coverage=round(title_hit, 4),
                predicate_coverage=round(pred_hit, 4),
                freshness_score=round(freshness, 4),
                group_score=round(group_score, 4),
                record_ids=rec_ids,
            )
        )

    groups.sort(key=lambda g: g.group_score, reverse=True)
    primary = groups[0].group_id if groups else None
    secondary: list[str] = []
    ambiguous = False
    audit: dict[str, Any] = {
        "wants_freshness": wants_fresh,
        "query_tokens": sorted(q_tokens)[:20],
        "selection": [],
    }

    if len(groups) >= 2:
        gap = groups[0].group_score - groups[1].group_score
        audit["top_gap"] = round(gap, 4)
        # Same family different revision: allow secondary only with freshness intent.
        same_family = (
            groups[0].document_family_id
            and groups[0].document_family_id == groups[1].document_family_id
        )
        if gap < min_score_gap and not same_family and not multi:
            # Ambiguous across unrelated docs — fail-closed to primary only if gap is tiny
            # and second group has weak title match.
            if groups[1].query_anchor_coverage < 0.25:
                groups[1].rejection_reasons.append("lower_score_weak_anchor")
            else:
                ambiguous = gap < 0.15
                if ambiguous:
                    groups[1].rejection_reasons.append("ambiguous_near_tie")
        # Explicit multi-subquery: allow secondary groups that cover distinct anchors.
        if multi:
            for g in groups[1:]:
                secondary.append(g.group_id)

    for g in groups:
        audit["selection"].append({
            "group_id": g.group_id,
            "knowledge_id": g.knowledge_id,
            "knowledge_ids": list(g.knowledge_ids),
            "score": g.group_score,
            "role": (
                "primary" if g.group_id == primary
                else ("secondary" if g.group_id in secondary else "rejected")
            ),
            "rejection_reasons": list(g.rejection_reasons),
        })

    return EvidenceGroupResolution(
        groups=groups,
        primary_group_id=primary,
        secondary_group_ids=secondary,
        multi_subquery=multi,
        ambiguous=ambiguous,
        audit=audit,
    )


def filter_records_to_groups(
    records: list[LogicalEvidenceRecord],
    resolution: EvidenceGroupResolution,
    *,
    allow_secondary: bool = False,
) -> list[LogicalEvidenceRecord]:
    """Restrict logical records to primary (and optional secondary) groups."""
    if not resolution.groups:
        return list(records)
    allowed_kids: set[str] = set()
    allowed_pids: set[str] = set()
    for g in resolution.groups:
        if g.group_id == resolution.primary_group_id or (
            allow_secondary and g.group_id in resolution.secondary_group_ids
        ):
            if g.knowledge_id:
                allowed_kids.add(g.knowledge_id)
            allowed_kids.update(g.knowledge_ids)
            allowed_pids.update(g.passage_ids)
    if not allowed_kids and not allowed_pids:
        return list(records)
    out = [
        r
        for r in records
        if (r.knowledge_id and r.knowledge_id in allowed_kids)
        or (r.passage_id and r.passage_id in allowed_pids)
    ]
    return out or list(records)


def filter_candidates_to_groups(
    candidates: list[Any],
    resolution: EvidenceGroupResolution,
    *,
    allow_secondary: bool = False,
) -> list[Any]:
    if not resolution.groups or not candidates:
        return list(candidates)
    allowed_kids: set[str] = set()
    allowed_pids: set[str] = set()
    for g in resolution.groups:
        if g.group_id == resolution.primary_group_id or (
            allow_secondary and g.group_id in resolution.secondary_group_ids
        ):
            if g.knowledge_id:
                allowed_kids.add(g.knowledge_id)
            allowed_kids.update(g.knowledge_ids)
            allowed_pids.update(g.passage_ids)
    out = []
    for c in candidates:
        kid = getattr(c, "knowledge_id", None) or (c.get("knowledge_id") if isinstance(c, dict) else "")
        pid = getattr(c, "passage_id", None) or (c.get("passage_id") if isinstance(c, dict) else "")
        if (kid and kid in allowed_kids) or (pid and pid in allowed_pids):
            out.append(c)
    return out or list(candidates)
