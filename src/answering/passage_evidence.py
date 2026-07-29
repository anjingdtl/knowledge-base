"""PassageEvidence DTO — end-to-end non-lossy evidence contract (SPEC v4 §A / v5 §2.1)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# Passage builder prefixes metadata as 【文档】/【章节】 lines before body.
_META_LINE_RE = re.compile(
    r"^(?:【文档】|【章节】|【页码】|【文号】|【标签】).*$",
    re.MULTILINE,
)
_META_PREFIX_BLOCK = re.compile(
    r"^(?:(?:【文档】|【章节】|【页码】|【文号】|【标签】)[^\n]*\n?)+",
)


def split_metadata_and_body(text: str) -> tuple[str, int, str]:
    """Split composed passage text into (body_text, body_char_start, meta_prefix).

    Auditable rule: strip only leading lines that match known metadata tags
    produced by ``passage_builder._compose_passage_text``. Never silently
    delete mid-body content.
    """
    raw = text or ""
    m = _META_PREFIX_BLOCK.match(raw)
    if not m:
        return raw, 0, ""
    meta = m.group(0)
    body_start = m.end()
    body = raw[body_start:]
    return body, body_start, meta


@dataclass
class PassageEvidence:
    passage_id: str
    knowledge_id: str
    text: str
    title: str = ""
    document_family_id: str = ""
    version_year: int | None = None
    source_version: str = ""
    section_path: str = ""
    block_ids: list[str] = field(default_factory=list)
    block_ranges: list[dict[str, Any]] = field(default_factory=list)
    score: float | None = None
    match_channels: list[str] = field(default_factory=list)
    is_family_newest: bool | None = None
    is_adjacent_extension: bool = False
    accepted: bool = True
    retrieval_unit: str = "passage"
    candidate_type: str = "passage"
    retrieval_fallback: str = ""
    # SPEC v5: fact extraction must use body_text only (no title/doc-no headers).
    body_text: str = ""
    body_char_start: int = 0
    body_char_end: int | None = None
    metadata_prefix: str = ""

    def ensure_body(self) -> "PassageEvidence":
        """Populate body_text from composed ``text`` when not already set."""
        if self.body_text:
            return self
        body, start, meta = split_metadata_and_body(self.text or "")
        self.body_text = body
        self.body_char_start = start
        self.body_char_end = start + len(body)
        self.metadata_prefix = meta
        return self

    def to_row(self) -> dict[str, Any]:
        self.ensure_body()
        primary_block = self.block_ids[0] if self.block_ids else ""
        return {
            "source": "knowledge",
            "passage_id": self.passage_id,
            "knowledge_id": self.knowledge_id,
            "block_id": primary_block,
            "block_ids": list(self.block_ids),
            "block_ranges": list(self.block_ranges),
            "title": self.title,
            "text": self.text,
            "body_text": self.body_text,
            "body_char_start": self.body_char_start,
            "body_char_end": self.body_char_end,
            "metadata_prefix": self.metadata_prefix,
            "document_family_id": self.document_family_id,
            "version_year": self.version_year,
            "source_version": self.source_version,
            "section_path": self.section_path,
            "score": self.score,
            "match_channels": list(self.match_channels),
            "is_family_newest": self.is_family_newest,
            "is_adjacent_extension": self.is_adjacent_extension,
            "accepted": self.accepted,
            "retrieval_unit": "passage",
            "candidate_type": "passage",
            "retrieval_fallback": self.retrieval_fallback or "",
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PassageEvidence":
        return normalize_to_passage_evidence(row)


def normalize_to_passage_evidence(row: dict[str, Any] | PassageEvidence) -> PassageEvidence:
    if isinstance(row, PassageEvidence):
        return row
    if not isinstance(row, dict):
        return PassageEvidence(passage_id="", knowledge_id="", text=str(row or ""))

    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    passage_id = str(
        row.get("passage_id")
        or meta.get("passage_id")
        or ""
    ).strip()
    # When retrieval_unit is passage but id is stored as generic "id".
    unit = str(row.get("retrieval_unit") or meta.get("retrieval_unit") or "").strip()
    ctype = str(row.get("candidate_type") or meta.get("candidate_type") or "").strip()
    if not passage_id and (unit == "passage" or ctype == "passage"):
        passage_id = str(row.get("id") or "").strip()

    knowledge_id = str(
        row.get("knowledge_id")
        or meta.get("knowledge_id")
        or meta.get("page_id")
        or row.get("page_id")
        or ""
    ).strip()

    block_ids = row.get("block_ids") or meta.get("block_ids") or []
    if isinstance(block_ids, str):
        try:
            import json
            block_ids = json.loads(block_ids)
        except Exception:
            block_ids = [block_ids] if block_ids else []
    if not block_ids:
        bid = row.get("block_id") or meta.get("block_id") or ""
        if bid:
            block_ids = [str(bid)]

    version_year = row.get("version_year")
    if version_year is None:
        version_year = meta.get("version_year") or row.get("effective_year")
    if isinstance(version_year, str) and version_year.isdigit():
        version_year = int(version_year)

    score = row.get("score")
    if score is None:
        score = row.get("final_relevance_score") or row.get("rrf_score")
    try:
        score_f = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_f = None

    channels = row.get("match_channels") or meta.get("match_channels") or []
    if isinstance(channels, str):
        channels = [channels]

    # Block-only fallback path when no passage id can be recovered.
    if not passage_id:
        return PassageEvidence(
            passage_id="",
            knowledge_id=knowledge_id,
            text=str(row.get("text") or row.get("content") or ""),
            title=str(row.get("title") or meta.get("title") or ""),
            document_family_id=str(
                row.get("document_family_id") or meta.get("document_family_id") or ""
            ),
            version_year=version_year if isinstance(version_year, int) else None,
            source_version=str(row.get("source_version") or meta.get("source_version") or ""),
            section_path=str(row.get("section_path") or meta.get("section_path") or ""),
            block_ids=[str(b) for b in block_ids if b],
            block_ranges=list(row.get("block_ranges") or meta.get("block_ranges") or []),
            score=score_f,
            match_channels=[str(c) for c in channels],
            is_family_newest=row.get("is_family_newest"),
            is_adjacent_extension=bool(row.get("is_adjacent_extension")),
            accepted=bool(row.get("accepted", True)),
            retrieval_unit="block",
            candidate_type="raw_block",
            retrieval_fallback="block",
        )

    pe = PassageEvidence(
        passage_id=passage_id,
        knowledge_id=knowledge_id,
        text=str(row.get("text") or row.get("content") or ""),
        title=str(row.get("title") or meta.get("title") or ""),
        document_family_id=str(
            row.get("document_family_id") or meta.get("document_family_id") or ""
        ),
        version_year=version_year if isinstance(version_year, int) else None,
        source_version=str(row.get("source_version") or meta.get("source_version") or ""),
        section_path=str(row.get("section_path") or meta.get("section_path") or ""),
        block_ids=[str(b) for b in block_ids if b],
        block_ranges=list(row.get("block_ranges") or meta.get("block_ranges") or []),
        score=score_f,
        match_channels=[str(c) for c in channels],
        is_family_newest=row.get("is_family_newest"),
        is_adjacent_extension=bool(row.get("is_adjacent_extension")),
        accepted=bool(row.get("accepted", True)),
        retrieval_unit="passage",
        candidate_type="passage",
        retrieval_fallback="",
        body_text=str(row.get("body_text") or ""),
        body_char_start=int(row.get("body_char_start") or 0),
        body_char_end=row.get("body_char_end"),
        metadata_prefix=str(row.get("metadata_prefix") or ""),
    )
    return pe.ensure_body()


def normalize_evidence_list(rows: list[dict[str, Any]] | None) -> list[PassageEvidence]:
    out: list[PassageEvidence] = []
    for r in rows or []:
        if not isinstance(r, dict) and not isinstance(r, PassageEvidence):
            continue
        pe = normalize_to_passage_evidence(r)
        if pe.knowledge_id or pe.passage_id or pe.text:
            out.append(pe)
    return out


def ensure_passage_trace(
    rows: list[dict[str, Any]] | None,
    *,
    require_passage: bool = True,
) -> tuple[bool, str]:
    """Return (ok, reason). When require_passage, every row must have passage_id
    and must not be labelled raw_block without fallback justification.
    """
    items = list(rows or [])
    if not items:
        return (True, "empty") if not require_passage else (False, "empty_evidence")
    for r in items:
        if not isinstance(r, dict):
            return False, "non_dict_row"
        pid = str(r.get("passage_id") or "").strip()
        ctype = str(r.get("candidate_type") or "").strip()
        unit = str(r.get("retrieval_unit") or "").strip()
        if require_passage:
            if not pid:
                return False, "missing_passage_id"
            if ctype == "raw_block" or unit == "block":
                if r.get("retrieval_fallback") != "block":
                    return False, "raw_block_without_fallback_flag"
    return True, "ok"


def passage_map(evidence: list[PassageEvidence]) -> dict[str, PassageEvidence]:
    return {e.passage_id: e for e in evidence if e.passage_id}
