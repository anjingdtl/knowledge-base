"""Deterministic retrieval-passage builder (SPEC v3 §A).

Merges over-fragmented graph blocks (or full document content) into semantic
passages of target length 400–1000 Chinese characters with 100–180 char overlap.
Graph blocks are preserved for provenance only — they are not the retrieval unit.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from src.services.document_family import assign_document_family

# Target body length (Chinese chars ≈ codepoints).
TARGET_MIN = 400
TARGET_MAX = 1000
OVERLAP_MIN = 100
OVERLAP_MAX = 180
# Standalone short headings / page markers must not form a passage alone.
SHORT_STANDALONE_MAX = 40
# Soft floor for short_passage flag (quality stats).
SHORT_PASSAGE_THRESHOLD = 200

_HEADING_RE = re.compile(
    r"^(#{1,6}\s+|"
    r"第[一二三四五六七八九十百千0-9]+[章节条款篇部分]|"
    r"[（(]?[一二三四五六七八九十0-9]+[）).、]\s*|"
    r"附件\s*[0-9一二三四五六七八九十]*|"
    r"【[^】]{1,40}】|"
    r"\[[第页0-9\-—]+\]"
    r")"
)
_PAGE_MARKER_RE = re.compile(r"^\[第\d+页\]$|^—+\s*\d+\s*—+$|^[-—]\s*\d+\s*[-—]$")
_BOUNDARY_RE = re.compile(
    r"(?<=[。！？；\n])|"
    r"(?=第[一二三四五六七八九十百千0-9]+[条款项])|"
    r"(?=#{1,6}\s)"
)


@dataclass
class PassageDraft:
    knowledge_id: str
    passage_index: int
    text: str
    title_prefix: str = ""
    section_path: str = ""
    block_ids: list[str] = field(default_factory=list)
    block_ranges: list[dict[str, Any]] = field(default_factory=list)
    short_passage: bool = False
    document_family_id: str = ""
    family_confidence: float = 0.0
    family_basis: str = ""
    source_version: str = ""
    version_year: int | None = None
    effective_year: int | None = None
    text_hash: str = ""
    char_count: int = 0
    id: str = ""

    def finalize(self) -> "PassageDraft":
        body = self.text.strip()
        self.text = body
        self.char_count = len(body)
        self.short_passage = len(body) < SHORT_PASSAGE_THRESHOLD
        self.text_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        # Deterministic id: knowledge + index + content hash prefix
        material = f"{self.knowledge_id}|{self.passage_index}|{self.text_hash}"
        self.id = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
        return self

    def to_row(self) -> dict[str, Any]:
        now = datetime.now().isoformat()
        self.finalize()
        return {
            "id": self.id,
            "knowledge_id": self.knowledge_id,
            "document_family_id": self.document_family_id or "",
            "family_confidence": float(self.family_confidence or 0.0),
            "family_basis": self.family_basis or "",
            "source_version": self.source_version or "",
            "version_year": self.version_year,
            "passage_index": int(self.passage_index),
            "text": self.text,
            "text_hash": self.text_hash,
            "char_count": len(self.text),
            "short_passage": 1 if self.short_passage else 0,
            "title_prefix": self.title_prefix or "",
            "section_path": self.section_path or "",
            "block_ids_json": json.dumps(self.block_ids, ensure_ascii=False),
            "block_ranges_json": json.dumps(self.block_ranges, ensure_ascii=False),
            "effective_year": self.effective_year if self.effective_year is not None else self.version_year,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }


def _is_heading_like(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _PAGE_MARKER_RE.match(t):
        return True
    if len(t) <= SHORT_STANDALONE_MAX and _HEADING_RE.match(t):
        return True
    if t.startswith("#"):
        return True
    return False


def _is_noise_alone(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if _PAGE_MARKER_RE.match(t):
        return True
    if re.fullmatch(r"[—\-\s\d]+", t):
        return True
    return False


def _normalize_block_rows(blocks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, b in enumerate(blocks or []):
        if not isinstance(b, dict):
            continue
        content = str(b.get("content") or b.get("text") or b.get("chunk_text") or "")
        bid = str(b.get("id") or b.get("block_id") or "")
        try:
            order = int(b.get("order_idx", i))
        except (TypeError, ValueError):
            order = i
        rows.append({
            "id": bid or f"anon-{i}",
            "content": content,
            "order_idx": order,
        })
    rows.sort(key=lambda r: r["order_idx"])
    return rows


def _units_from_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert ordered blocks into mergeable units with provenance."""
    units: list[dict[str, Any]] = []
    for b in blocks:
        text = (b.get("content") or "").strip()
        if not text:
            continue
        units.append({
            "text": text,
            "block_id": b["id"],
            "order_idx": b["order_idx"],
            "heading": _is_heading_like(text),
            "noise": _is_noise_alone(text),
        })
    return units


def _units_from_content(content: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer full document content when it is substantially longer than blocks."""
    body = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    if not body.strip():
        return _units_from_blocks(blocks)

    block_chars = sum(len((b.get("content") or "").strip()) for b in blocks)
    if block_chars >= max(200, int(len(body) * 0.6)):
        # Blocks already cover most content — merge from blocks for precise IDs.
        return _units_from_blocks(blocks)

    # Split content into paragraphs / list rows, then map block ids by substring.
    parts = re.split(r"\n{2,}|(?=\n#{1,6}\s)|(?=\n第[一二三四五六七八九十百千0-9]+[章节条款])", body)
    flat_blocks = [(b["id"], (b.get("content") or "").strip()) for b in blocks if (b.get("content") or "").strip()]
    cursor = 0
    units: list[dict[str, Any]] = []
    for i, part in enumerate(parts):
        text = part.strip()
        if not text:
            continue
        matched: list[str] = []
        for bid, btxt in flat_blocks:
            if btxt and btxt in text:
                matched.append(bid)
            elif btxt and text in btxt and len(text) > 8:
                matched.append(bid)
        if not matched and flat_blocks:
            # Fallback: assign by sequential consumption of block order.
            n = max(1, len(flat_blocks) // max(1, len(parts)))
            matched = [bid for bid, _ in flat_blocks[cursor:cursor + n]]
            cursor = min(len(flat_blocks), cursor + n)
        units.append({
            "text": text,
            "block_id": matched[0] if matched else f"content-{i}",
            "block_ids": matched,
            "order_idx": i,
            "heading": _is_heading_like(text.split("\n", 1)[0]),
            "noise": _is_noise_alone(text),
        })
    return units or _units_from_blocks(blocks)


def _overlap_suffix(text: str, target: int = 140) -> str:
    if not text:
        return ""
    n = max(OVERLAP_MIN, min(OVERLAP_MAX, target))
    if len(text) <= n:
        return text
    chunk = text[-n:]
    # Prefer starting at a sentence / clause boundary inside the overlap window.
    m = re.search(r"[。！？；\n]", chunk)
    if m and m.end() < len(chunk) - 20:
        return chunk[m.end():]
    return chunk


def _compose_passage_text(
    title: str,
    section_path: str,
    body_parts: list[str],
) -> str:
    header_bits = []
    if title:
        header_bits.append(f"【文档】{title}")
    if section_path:
        header_bits.append(f"【章节】{section_path}")
    header = "\n".join(header_bits)
    body = "\n".join(p for p in body_parts if p and p.strip())
    if header:
        return f"{header}\n{body}".strip()
    return body.strip()


def build_passages_for_document(
    *,
    knowledge_id: str,
    title: str = "",
    content: str = "",
    blocks: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    target_min: int = TARGET_MIN,
    target_max: int = TARGET_MAX,
) -> list[PassageDraft]:
    """Build deterministic passages for one knowledge item.

    Same inputs ⇒ same passage IDs, counts, and text hashes.
    """
    kid = (knowledge_id or "").strip()
    if not kid:
        return []

    block_rows = _normalize_block_rows(blocks or [])
    units = _units_from_content(content, block_rows)
    if not units:
        # Last resort: whole content as one passage if any.
        raw = (content or "").strip()
        if not raw:
            return []
        units = [{
            "text": raw,
            "block_id": "content-0",
            "block_ids": [],
            "order_idx": 0,
            "heading": False,
            "noise": False,
        }]

    family = assign_document_family(
        title=title,
        text=content or (units[0]["text"] if units else ""),
        knowledge_id=kid,
        metadata=metadata,
    )

    section_stack: list[str] = []
    drafts: list[PassageDraft] = []
    buf_parts: list[str] = []
    buf_blocks: list[str] = []
    buf_ranges: list[dict[str, Any]] = []
    buf_chars = 0
    pending_heading = ""

    def _flush(*, force: bool = False) -> None:
        nonlocal buf_parts, buf_blocks, buf_ranges, buf_chars, pending_heading
        if not buf_parts:
            return
        body_len = sum(len(p) for p in buf_parts)
        # Merge trailing short leftovers with previous if possible.
        if (
            not force
            and drafts
            and body_len < target_min
            and (len(drafts[-1].text) + body_len) <= target_max + 200
        ):
            prev = drafts.pop()
            # Drop previous header when re-merging body.
            merged_body = prev.text
            if "【文档】" in merged_body:
                # Keep previous full text and append new body parts.
                extra = "\n".join(buf_parts)
                text = f"{merged_body}\n{extra}".strip()
            else:
                text = _compose_passage_text(title, prev.section_path, [merged_body] + buf_parts)
            block_ids = list(dict.fromkeys(prev.block_ids + buf_blocks))
            ranges = list(prev.block_ranges) + list(buf_ranges)
            d = PassageDraft(
                knowledge_id=kid,
                passage_index=len(drafts),
                text=text,
                title_prefix=title or "",
                section_path=prev.section_path,
                block_ids=block_ids,
                block_ranges=ranges,
                document_family_id=family["document_family_id"],
                family_confidence=family["family_confidence"],
                family_basis=family["family_basis"],
                source_version=str(family.get("source_version") or ""),
                version_year=family.get("version_year"),
                effective_year=family.get("version_year"),
            ).finalize()
            drafts.append(d)
            buf_parts, buf_blocks, buf_ranges, buf_chars = [], [], [], 0
            pending_heading = ""
            return

        section = " > ".join(section_stack) if section_stack else (pending_heading or "")
        text = _compose_passage_text(title, section, buf_parts)
        # Never emit pure noise / pure short heading as a passage.
        body_only = "\n".join(buf_parts).strip()
        if not body_only or (_is_noise_alone(body_only) and len(body_only) < SHORT_STANDALONE_MAX):
            buf_parts, buf_blocks, buf_ranges, buf_chars = [], [], [], 0
            return
        d = PassageDraft(
            knowledge_id=kid,
            passage_index=len(drafts),
            text=text,
            title_prefix=title or "",
            section_path=section,
            block_ids=list(dict.fromkeys(buf_blocks)),
            block_ranges=list(buf_ranges),
            document_family_id=family["document_family_id"],
            family_confidence=family["family_confidence"],
            family_basis=family["family_basis"],
            source_version=str(family.get("source_version") or ""),
            version_year=family.get("version_year"),
            effective_year=family.get("version_year"),
        ).finalize()
        drafts.append(d)

        # Overlap into next buffer.
        overlap = _overlap_suffix(body_only, target=140)
        buf_parts = [overlap] if overlap else []
        buf_blocks = list(dict.fromkeys(buf_blocks[-3:])) if buf_blocks else []
        buf_ranges = list(buf_ranges[-3:]) if buf_ranges else []
        buf_chars = sum(len(p) for p in buf_parts)
        pending_heading = ""

    for unit in units:
        text = unit["text"]
        bids = unit.get("block_ids") or ([unit["block_id"]] if unit.get("block_id") else [])
        if unit.get("heading") and len(text) <= 80:
            # Update section path; keep heading attached to following body.
            clean = re.sub(r"^#+\s*", "", text).strip()
            if clean and not _PAGE_MARKER_RE.match(clean):
                if len(section_stack) >= 3:
                    section_stack = section_stack[-2:]
                if not section_stack or section_stack[-1] != clean:
                    section_stack.append(clean)
                pending_heading = clean
            # Do not flush on heading alone — attach to next body.
            if buf_chars >= target_min:
                _flush()
            # Include heading text in buffer so it is searchable with body.
            if not unit.get("noise"):
                buf_parts.append(text)
                buf_blocks.extend(bids)
                for bid in bids:
                    buf_ranges.append({
                        "block_id": bid,
                        "order_idx": unit.get("order_idx"),
                        "char_start": 0,
                        "char_end": len(text),
                    })
                buf_chars = sum(len(p) for p in buf_parts)
            continue

        if unit.get("noise") and buf_chars == 0:
            continue

        # Soft-split oversized units on natural boundaries.
        pieces = _split_oversized(text, target_max)
        for piece in pieces:
            buf_parts.append(piece)
            buf_blocks.extend(bids)
            for bid in bids:
                buf_ranges.append({
                    "block_id": bid,
                    "order_idx": unit.get("order_idx"),
                    "char_start": 0,
                    "char_end": len(piece),
                })
            buf_chars = sum(len(p) for p in buf_parts)
            if buf_chars >= target_min:
                # Prefer flush near max; allow slightly over target_min.
                if buf_chars >= target_max or _ends_on_boundary(piece):
                    _flush()

    _flush(force=True)

    # Re-index and re-finalize for stable sequential indices after merges.
    final: list[PassageDraft] = []
    for i, d in enumerate(drafts):
        d.passage_index = i
        d.finalize()
        final.append(d)

    # If everything collapsed to nothing but we had content, emit one passage.
    if not final and units:
        body = "\n".join(u["text"] for u in units if not u.get("noise"))
        if body.strip():
            d = PassageDraft(
                knowledge_id=kid,
                passage_index=0,
                text=_compose_passage_text(title, "", [body]),
                title_prefix=title or "",
                block_ids=[u.get("block_id") for u in units if u.get("block_id")],
                document_family_id=family["document_family_id"],
                family_confidence=family["family_confidence"],
                family_basis=family["family_basis"],
                source_version=str(family.get("source_version") or ""),
                version_year=family.get("version_year"),
                effective_year=family.get("version_year"),
            ).finalize()
            final.append(d)
    return final


def _ends_on_boundary(text: str) -> bool:
    t = (text or "").rstrip()
    return bool(t) and t[-1] in "。！？；\n"


def _split_oversized(text: str, target_max: int) -> list[str]:
    t = text or ""
    if len(t) <= target_max:
        return [t]
    parts: list[str] = []
    # Split by sentences first.
    sentences = re.split(r"(?<=[。！？；])", t)
    buf = ""
    for s in sentences:
        if not s:
            continue
        if len(buf) + len(s) <= target_max:
            buf += s
        else:
            if buf:
                parts.append(buf)
            if len(s) <= target_max:
                buf = s
            else:
                # Hard wrap long run-ons.
                for i in range(0, len(s), target_max):
                    chunk = s[i:i + target_max]
                    if i == 0 and not parts:
                        buf = chunk
                    else:
                        parts.append(chunk)
                buf = ""
    if buf:
        parts.append(buf)
    return parts or [t]


def passages_to_rows(passages: list[PassageDraft]) -> list[dict[str, Any]]:
    return [p.to_row() for p in passages]
