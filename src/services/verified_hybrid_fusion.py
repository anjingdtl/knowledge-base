"""Normalize + fuse Verified Wiki Claims with Raw Block candidates (Phase 3).

Spec §7.5–7.6:
- Unified candidate schema
- Channel-internal ranking → RRF (not raw score addition)
- Gate-failed claims already excluded before entry
- Claim must carry Evidence; Evidence Blocks of served claims are de-duplicated
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from src.models.wiki_v2 import Claim
from src.services.wiki_serving_gate import ServingDecision

_RRF_K = 40
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if t.strip()}


def claim_retrieval_score(query: str, claim: Claim) -> float:
    """Deterministic lexical overlap score in [0, 1] (no embedding / LLM)."""
    q = _tokens(query)
    if not q:
        return 0.0
    stmt = _tokens(claim.statement) | _tokens(claim.normalized_statement)
    if not stmt:
        return 0.0
    overlap = len(q & stmt)
    # Prefer denser matches; cap at 1.0
    return min(1.0, overlap / max(1, len(q)) * 0.7 + overlap / max(1, len(stmt)) * 0.3)


def normalize_claim_candidate(
    claim: Claim,
    decision: ServingDecision,
    *,
    query: str = "",
    rank: int = 0,
) -> dict[str, Any]:
    """Convert a Gate-passed Claim into unified candidate schema."""
    resolved = [
        r for r in decision.resolved_evidence
        if r.ok or decision.disclose_only
    ]
    if not resolved:
        resolved = list(decision.resolved_evidence)

    evidence_payload: list[dict[str, Any]] = []
    primary_kid = ""
    primary_bid = ""
    for r in resolved:
        ev = r.evidence
        evidence_payload.append({
            "evidence_id": ev.evidence_id,
            "knowledge_id": ev.knowledge_id,
            "block_id": ev.block_id,
            "stance": ev.stance.value if hasattr(ev.stance, "value") else str(ev.stance),
            "stale": bool(ev.stale),
            "excerpt_hash": ev.excerpt_hash,
            "ok": r.ok,
            "reason_codes": list(r.reason_codes),
        })
        if not primary_kid and ev.knowledge_id:
            primary_kid = ev.knowledge_id
        if not primary_bid and ev.block_id:
            primary_bid = ev.block_id

    retrieval = claim_retrieval_score(query, claim)
    # Spec §7.6 multipliers (gate already enforced status/freshness/validation)
    status_factor = 1.0 if decision.eligible else 0.35  # disclose-only demoted
    evidence_coverage = min(1.0, len([e for e in evidence_payload if e.get("ok")]) / 1.0)
    freshness_factor = 1.0
    if any(e.get("stale") for e in evidence_payload):
        freshness_factor = 0.2
    validation_factor = 1.0
    serving_score = (
        retrieval * status_factor * max(0.15, evidence_coverage) * freshness_factor * validation_factor
    )

    warnings = list(decision.reason_codes) if decision.disclose_only else []
    if decision.disclose_only:
        warnings.append("disclose_only")

    return {
        "id": f"claim:{claim.claim_id}",
        "candidate_type": "claim",
        "candidate_id": claim.claim_id,
        "text": claim.statement,
        "score": round(serving_score, 6),
        "retrieval_score": round(retrieval, 6),
        "source_layer": "canonical",
        "source": "verified_claim",
        "knowledge_id": primary_kid,
        "block_id": primary_bid,
        "claim_id": claim.claim_id,
        "page_id": None,
        "status": claim.status.value if hasattr(claim.status, "value") else str(claim.status),
        "freshness": "current" if freshness_factor >= 1.0 else "stale_partial",
        "match_channels": ["verified_wiki"],
        "score_breakdown": {
            "retrieval": retrieval,
            "status_factor": status_factor,
            "evidence_coverage": evidence_coverage,
            "freshness_factor": freshness_factor,
            "serving_score": serving_score,
            "rank": rank,
        },
        "evidence": evidence_payload,
        "warnings": warnings,
        "title": f"Claim: {claim.statement[:80]}",
        "metadata": {
            "claim_id": claim.claim_id,
            "knowledge_id": primary_kid,
            "page_id": primary_kid,
            "candidate_type": "claim",
        },
        "disclose_only": bool(decision.disclose_only),
        "eligible": bool(decision.eligible),
    }


def normalize_raw_candidate(raw: dict[str, Any], *, rank: int = 0) -> dict[str, Any]:
    """Normalize hybrid/block search hit into unified candidate schema."""
    meta = raw.get("metadata") or {}
    bid = str(raw.get("id") or raw.get("block_id") or "")
    kid = str(
        meta.get("page_id")
        or meta.get("knowledge_id")
        or raw.get("knowledge_id")
        or ""
    )
    score = 0.0
    for key in ("rerank_score", "rrf_score", "final_score", "vector_score", "score"):
        val = raw.get(key)
        if val is not None:
            score = float(val)
            break
    channels = list(raw.get("match_channels") or [])
    if "raw" not in channels:
        channels = channels + ["raw"]

    return {
        "id": bid or f"raw:{kid}:{rank}",
        "candidate_type": "raw_block",
        "candidate_id": bid or kid,
        "text": raw.get("text") or "",
        "score": score,
        "source_layer": "evidence",
        "source": "knowledge",
        "knowledge_id": kid,
        "block_id": bid,
        "claim_id": None,
        "page_id": kid,
        "status": "raw",
        "freshness": "current",
        "match_channels": channels,
        "score_breakdown": {
            "rerank_score": raw.get("rerank_score"),
            "rrf_score": raw.get("rrf_score"),
            "vector_score": raw.get("vector_score"),
            "rank": rank,
        },
        "evidence": [],
        "warnings": list(raw.get("warnings") or []),
        "title": meta.get("title") or "",
        "metadata": meta,
        "disclose_only": False,
        "eligible": True,
        # preserve original fields used downstream
        "rerank_score": raw.get("rerank_score"),
        "rrf_score": raw.get("rrf_score"),
        "vector_score": raw.get("vector_score"),
    }


def fuse_verified_and_raw(
    claim_candidates: Sequence[dict[str, Any]],
    raw_candidates: Sequence[dict[str, Any]],
    *,
    wiki_weight: float = 0.40,
    raw_weight: float = 0.60,
    k: int = _RRF_K,
    top_n: int | None = None,
) -> list[dict[str, Any]]:
    """RRF fusion across claim and raw channels (Spec §7.6)."""
    scores: dict[str, float] = {}
    items: dict[str, dict[str, Any]] = {}
    channels: dict[str, set[str]] = {}

    def _acc(cands: Sequence[dict[str, Any]], weight: float, channel: str) -> None:
        for rank, cand in enumerate(cands):
            cid = str(cand.get("id") or "")
            if not cid:
                continue
            scores[cid] = scores.get(cid, 0.0) + weight / (k + rank + 1)
            if cid not in items:
                items[cid] = dict(cand)
            channels.setdefault(cid, set()).update(cand.get("match_channels") or [])
            channels[cid].add(channel)

    # Sort each channel by its own score first (internal normalization via rank)
    claim_sorted = sorted(
        claim_candidates, key=lambda c: float(c.get("score") or 0.0), reverse=True,
    )
    raw_sorted = sorted(
        raw_candidates, key=lambda c: float(c.get("score") or 0.0), reverse=True,
    )
    _acc(claim_sorted, wiki_weight, "verified_wiki")
    _acc(raw_sorted, raw_weight, "raw")

    for cid, item in items.items():
        item["rrf_score"] = round(scores[cid], 6)
        item["final_score"] = scores[cid]
        item["score"] = scores[cid]
        item["match_channels"] = sorted(channels.get(cid, set()))

    ranked = sorted(items.values(), key=lambda x: float(x.get("rrf_score") or 0), reverse=True)

    # Spec §7.6: Evidence Blocks of served claims should not double-occupy slots
    ranked = _dedupe_claim_evidence_blocks(ranked)

    if top_n is not None:
        ranked = ranked[:top_n]
    return ranked


def _dedupe_claim_evidence_blocks(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """If a claim is present, drop raw_block rows that are its primary evidence."""
    claim_blocks: set[str] = set()
    for c in candidates:
        if c.get("candidate_type") == "claim":
            for ev in c.get("evidence") or []:
                bid = ev.get("block_id")
                if bid:
                    claim_blocks.add(str(bid))
            bid = c.get("block_id")
            if bid:
                claim_blocks.add(str(bid))

    if not claim_blocks:
        return candidates

    out: list[dict[str, Any]] = []
    for c in candidates:
        if (
            c.get("candidate_type") == "raw_block"
            and str(c.get("block_id") or c.get("id") or "") in claim_blocks
        ):
            # Keep claim; skip duplicate evidence block as standalone hit
            continue
        out.append(c)
    return out


def claims_to_candidates(
    pairs: Sequence[tuple[Claim, ServingDecision]],
    *,
    query: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Build ranked claim candidates from Gate filter pairs."""
    scored: list[tuple[float, dict[str, Any]]] = []
    for claim, decision in pairs:
        # Primary answers only by default; disclose_only still normalized for Phase 4
        if not decision.eligible and not decision.disclose_only:
            continue
        cand = normalize_claim_candidate(claim, decision, query=query)
        if decision.eligible and not cand.get("evidence"):
            # Spec acceptance: claim must carry original evidence
            cand.setdefault("warnings", []).append("missing_packaged_evidence")
            continue
        if decision.eligible and not any(
            e.get("knowledge_id") or e.get("block_id") for e in (cand.get("evidence") or [])
        ):
            cand.setdefault("warnings", []).append("evidence_unlinked")
            continue
        scored.append((float(cand.get("score") or 0.0), cand))

    scored.sort(key=lambda x: x[0], reverse=True)
    out = [c for _, c in scored]
    if limit is not None:
        out = out[:limit]
    # Re-stamp ranks
    for i, c in enumerate(out):
        c.setdefault("score_breakdown", {})["rank"] = i
    return out


def package_fused_result(
    cand: dict[str, Any],
    *,
    db: Any = None,
    citation_builder: Any = None,
    query: str = "",
) -> dict[str, Any]:
    """Map unified candidate → SearchService output item (MCP-compatible)."""
    ctype = cand.get("candidate_type") or "raw_block"
    kid = cand.get("knowledge_id") or ""
    bid = cand.get("block_id") or ""
    title = cand.get("title") or ""

    if ctype == "claim":
        item: dict[str, Any] = {
            "source": "verified_claim",
            "candidate_type": "claim",
            "block_id": bid or None,
            "knowledge_id": kid,
            "claim_id": cand.get("claim_id"),
            "title": title,
            "text": cand.get("text") or "",
            "score": float(cand.get("score") or 0.0),
            "match_channels": list(cand.get("match_channels") or []),
            "warnings": list(cand.get("warnings") or []),
            "status": cand.get("status"),
            "freshness": cand.get("freshness"),
            "source_layer": "canonical",
            "evidence": list(cand.get("evidence") or []),
            "score_breakdown": cand.get("score_breakdown") or {},
            "disclose_only": bool(cand.get("disclose_only")),
        }
        if citation_builder is not None and hasattr(citation_builder, "build_claim_citation"):
            try:
                item["citation"] = citation_builder.build_claim_citation(item)
            except Exception:  # noqa: BLE001
                item["citation"] = {
                    "citation_layer": "claim",
                    "claim_id": cand.get("claim_id"),
                    "knowledge_id": kid,
                    "block_id": bid,
                    "evidence": item["evidence"],
                }
        elif citation_builder is not None and bid and db is not None:
            try:
                knowledge = db.get_knowledge(kid) if kid else None
                fake_raw = {
                    "id": bid,
                    "text": cand.get("text"),
                    "metadata": {"page_id": kid, "knowledge_id": kid, "title": title},
                }
                citation = citation_builder.build(fake_raw, knowledge)
                citation_payload = citation.to_dict()
                citation_payload["claim_id"] = cand.get("claim_id")
                citation_payload["citation_layer"] = "claim"
                citation_payload["evidence"] = item["evidence"]
                item["citation"] = citation_payload
            except Exception:  # noqa: BLE001
                item["citation"] = {
                    "claim_id": cand.get("claim_id"),
                    "knowledge_id": kid,
                    "block_id": bid,
                    "evidence": item["evidence"],
                }
        else:
            item["citation"] = {
                "citation_layer": "claim",
                "claim_id": cand.get("claim_id"),
                "knowledge_id": kid,
                "block_id": bid,
                "evidence": item["evidence"],
            }
        return item

    # raw_block packaging (mirrors existing SearchService fields)
    knowledge = None
    if db is not None and kid:
        try:
            knowledge = db.get_knowledge(kid)
        except Exception:  # noqa: BLE001
            knowledge = None
    if not title and knowledge:
        title = knowledge.get("title") or title
    if not title:
        title = (cand.get("metadata") or {}).get("title") or "未知"

    item = {
        "source": "knowledge",
        "candidate_type": "raw_block",
        "block_id": bid,
        "knowledge_id": kid,
        "title": title,
        "text": cand.get("text") or "",
        "score": float(cand.get("score") or 0.0),
        "match_channels": list(cand.get("match_channels") or []),
        "warnings": list(cand.get("warnings") or []),
        "source_layer": "evidence",
        "score_breakdown": cand.get("score_breakdown") or {},
    }
    if citation_builder is not None:
        try:
            fake_raw = {
                "id": bid,
                "text": cand.get("text"),
                "metadata": cand.get("metadata") or {"page_id": kid},
            }
            item["citation"] = citation_builder.build(fake_raw, knowledge).to_dict()
        except Exception:  # noqa: BLE001
            pass
    return item
