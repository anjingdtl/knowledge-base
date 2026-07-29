"""Logical Evidence Record layer between passages and FactCandidates (SPEC v5 §2.2)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from src.answering.passage_evidence import PassageEvidence, split_metadata_and_body

_SENTENCE_SPLIT = re.compile(r"(?<=[。；;！？\n])")
_LIST_ITEM = re.compile(r"(?:^|\n)\s*(?:[-•·]|\d+[\.、]|[（(][一二三四五六七八九十0-9]+[）)])\s*")
_TABLE_ROWISH = re.compile(
    r"(?:处罚|限额|标准|时限|奖金).{0,40}\d|"
    r"\d+(?:\.\d+)?\s*(?:万元|元|%|％|个工作日|工作日)"
)
# Heuristic: many short lines with multiple numbers look like flattened OCR tables.
_DIGIT_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class LogicalEvidenceRecord:
    record_id: str
    passage_id: str
    knowledge_id: str
    type: str  # paragraph | list_item | table_row | table_cell_group | unstructured_table
    body_text: str
    normalized_text: str = ""
    source_span: tuple[int, int] | None = None  # offsets into passage body_text
    table_id: str = ""
    row_index: int | None = None
    column_labels: list[str] = field(default_factory=list)
    document_family_id: str = ""
    version_year: int | None = None
    section_path: str = ""
    unstructured_table: bool = False
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.source_span is not None:
            d["source_span"] = list(self.source_span)
        return d


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _is_ambiguous_table_blob(text: str) -> bool:
    """Detect flattened OCR tables where row/column binding is unreliable.

    Prefer local condition windows (±40 chars around money). Only mark
    unstructured when most money values lack a local exclusive condition.
    """
    body = text or ""
    if len(body) < 80:
        return False
    money = list(re.finditer(r"\d+(?:\.\d+)?\s*(?:万元|元)", body))
    if len(money) < 3:
        return False
    local_cond = 0
    for m in money:
        window = body[max(0, m.start() - 40) : m.end() + 40]
        if re.search(
            r"涉诈|涉骚扰|III\s*类|Ⅲ\s*类|(?<![IⅠ])II\s*类|Ⅱ\s*类|"
            r"(?<![IⅠ二三])I\s*类|团体|个人|区[内外]|处罚|限额",
            window,
        ):
            local_cond += 1
    # Majority of money values have local cues → treat as bindable clauses.
    if local_cond >= max(2, (len(money) + 1) // 2):
        return False
    dash_cells = len(re.findall(r"\s[-—]\s|-\s*-", body))
    sentence_ends = len(re.findall(r"[。；;]", body))
    newlines = body.count("\n")
    if sentence_ends <= 1 and (dash_cells >= 4 or newlines >= 8):
        return True
    return local_cond == 0


def _soft_join_ocr_lines(body: str) -> str:
    """Join PDF/OCR soft wraps so condition and value stay in one clause.

    Newlines that do not follow sentence punctuation are treated as wraps.
    Hard breaks after 。！？；; remain clause boundaries.
    """
    if not body:
        return body
    lines = body.split("\n")
    if len(lines) <= 1:
        return body
    out: list[str] = []
    buf = lines[0]
    for line in lines[1:]:
        prev = buf.rstrip()
        nxt = line.lstrip()
        if not nxt:
            out.append(buf)
            buf = ""
            continue
        if not prev:
            buf = nxt
            continue
        # Hard break after sentence/clause punctuation.
        if re.search(r"[。！？；;：:]$", prev):
            out.append(buf)
            buf = nxt
            continue
        # Soft wrap: glue without introducing a space for CJK continuity.
        if re.search(r"[\u4e00-\u9fffA-Za-z0-9、，,）)]$", prev) and re.match(
            r"[\u4e00-\u9fffA-Za-z0-9（(]", nxt
        ):
            buf = prev + nxt
        else:
            buf = prev + " " + nxt
    if buf:
        out.append(buf)
    return "\n".join(out)


def records_from_passage(evidence: PassageEvidence | dict[str, Any]) -> list[LogicalEvidenceRecord]:
    """Split one passage into logical records. Metadata never enters body_text."""
    if isinstance(evidence, dict):
        from src.answering.passage_evidence import normalize_to_passage_evidence

        pe = normalize_to_passage_evidence(evidence)
    else:
        pe = evidence

    body, body_start, _meta = split_metadata_and_body(pe.text or "")
    # Prefer explicit body_text when already set.
    if getattr(pe, "body_text", None):
        body = pe.body_text or body
        body_start = int(getattr(pe, "body_char_start", None) or body_start or 0)

    if not body.strip():
        return []

    # Rejoin OCR wraps before clause split so "II类支付\\n账户…10万元" stays bound.
    body = _soft_join_ocr_lines(body)

    pid = pe.passage_id or ""
    kid = pe.knowledge_id or ""
    common = dict(
        passage_id=pid,
        knowledge_id=kid,
        document_family_id=pe.document_family_id or "",
        version_year=pe.version_year,
        section_path=pe.section_path or "",
        title=pe.title or "",
    )

    if _is_ambiguous_table_blob(body):
        rid = f"{pid}:unstructured:0"
        return [
            LogicalEvidenceRecord(
                record_id=rid,
                type="unstructured_table",
                body_text=body,
                normalized_text=_norm(body),
                source_span=(0, len(body)),
                unstructured_table=True,
                **common,
            )
        ]

    records: list[LogicalEvidenceRecord] = []
    # Prefer list items when present.
    parts: list[tuple[str, str]] = []  # (type, text)
    if _LIST_ITEM.search(body) and body.count("\n") >= 1:
        chunks = _LIST_ITEM.split(body)
        for i, ch in enumerate(chunks):
            ch = ch.strip()
            if not ch:
                continue
            parts.append(("list_item" if i > 0 else "paragraph", ch))
    else:
        # Clause / sentence split — keep condition+value co-located.
        raw_parts = [p.strip() for p in _SENTENCE_SPLIT.split(body) if p and p.strip()]
        if not raw_parts:
            raw_parts = [body.strip()]
        for p in raw_parts:
            # Further split long clauses that contain both II/III class limits.
            if re.search(r"II\s*类|II类|Ⅱ类", p) and re.search(r"III\s*类|III类|Ⅲ类", p):
                sub = re.split(r"[；;]", p)
                for s in sub:
                    s = s.strip()
                    if s:
                        rtype = "table_row" if _TABLE_ROWISH.search(s) else "paragraph"
                        parts.append((rtype, s))
            else:
                rtype = "table_row" if _TABLE_ROWISH.search(p) else "paragraph"
                parts.append((rtype, p))

    cursor = 0
    for i, (rtype, text) in enumerate(parts):
        idx = body.find(text, cursor)
        if idx < 0:
            idx = body.find(text)
        if idx < 0:
            idx = cursor
        start = idx
        end = idx + len(text)
        cursor = end
        records.append(
            LogicalEvidenceRecord(
                record_id=f"{pid}:r{i}",
                type=rtype,
                body_text=text,
                normalized_text=_norm(text),
                source_span=(start, end),
                row_index=i if rtype == "table_row" else None,
                table_id=f"{pid}:t0" if rtype == "table_row" else "",
                **common,
            )
        )
    return records


def records_from_evidence_list(
    evidence: list[PassageEvidence | dict[str, Any]] | None,
) -> list[LogicalEvidenceRecord]:
    out: list[LogicalEvidenceRecord] = []
    for e in evidence or []:
        out.extend(records_from_passage(e))
    return out
