"""Claim and raw evidence citation packaging for answers."""
from __future__ import annotations

from typing import Any


def is_claim(row: dict[str, Any]) -> bool:
    return bool(
        row.get("source") == "verified_claim"
        or row.get("candidate_type") == "claim"
        or bool(row.get("claim_id"))
    )


def is_raw(row: dict[str, Any]) -> bool:
    return bool(
        not is_claim(row)
        and (
            row.get("source") in (None, "knowledge", "wiki")
            or row.get("candidate_type") in ("raw_block", "passage")
            or row.get("retrieval_unit") == "passage"
            or row.get("passage_id")
            or row.get("block_id")
            or row.get("knowledge_id")
        )
    )


def build_claim_citations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claim + evidence chain citations (Spec §8.1–§8.2)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if not is_claim(row):
            continue
        evidence = []
        for ev in row.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            evidence.append({
                "knowledge_id": ev.get("knowledge_id") or "",
                "block_id": ev.get("block_id") or "",
                "path": ev.get("path") or "",
                "location": ev.get("location") or {},
                "excerpt": ev.get("excerpt") or "",
                "evidence_stance": ev.get("stance") or ev.get("evidence_stance") or "supports",
                "stale": bool(ev.get("stale")),
            })
        cit = {
            "claim_id": row.get("claim_id") or row.get("candidate_id"),
            "statement": row.get("text") or row.get("statement") or "",
            "status": row.get("status") or "active",
            "revision": row.get("revision"),
            "page_id": row.get("page_id"),
            "validation": "passed" if row.get("eligible", True) else "disclose",
            "evidence": evidence,
        }
        if not evidence and row.get("block_id"):
            cit["evidence"] = [{
                "knowledge_id": row.get("knowledge_id") or "",
                "block_id": row.get("block_id") or "",
                "path": "",
                "location": {},
                "excerpt": (row.get("text") or "")[:200],
                "evidence_stance": "supports",
                "stale": False,
            }]
        out.append(cit)
    return out


def build_raw_evidence_used(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Package raw/passage evidence with non-lossy passage fields (SPEC v4 §A)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if is_claim(row):
            continue
        unit = str(row.get("retrieval_unit") or "").strip()
        ctype = str(row.get("candidate_type") or "").strip()
        passage_id = str(row.get("passage_id") or "").strip()
        is_passage = bool(passage_id) or unit == "passage" or ctype == "passage"
        text = row.get("text") or ""
        # Passages keep full semantic text (cap 2000); legacy blocks stay shorter.
        text_out = (text[:2000] if is_passage else text[:500])
        out.append({
            "knowledge_id": row.get("knowledge_id") or "",
            "passage_id": passage_id,
            "block_id": row.get("block_id") or "",
            "block_ids": list(row.get("block_ids") or []),
            "title": row.get("title") or "",
            "path": (
                (row.get("citation") or {}).get("path", "")
                if isinstance(row.get("citation"), dict)
                else ""
            ),
            "text": text_out,
            "score": row.get("score"),
            "document_family_id": row.get("document_family_id") or "",
            "version_year": row.get("version_year"),
            "section_path": row.get("section_path") or "",
            "retrieval_unit": "passage" if is_passage else (unit or "block"),
            "candidate_type": "passage" if is_passage else (ctype or "raw_block"),
            "retrieval_fallback": row.get("retrieval_fallback") or "",
            "citation": row.get("citation"),
        })
    return out
