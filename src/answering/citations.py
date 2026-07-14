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
            or row.get("candidate_type") == "raw_block"
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
    out: list[dict[str, Any]] = []
    for row in rows:
        if is_claim(row):
            continue
        out.append({
            "knowledge_id": row.get("knowledge_id") or "",
            "block_id": row.get("block_id") or "",
            "title": row.get("title") or "",
            "path": (
                (row.get("citation") or {}).get("path", "")
                if isinstance(row.get("citation"), dict)
                else ""
            ),
            "text": (row.get("text") or "")[:500],
            "score": row.get("score"),
            "citation": row.get("citation"),
        })
    return out
