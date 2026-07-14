"""Shadow comparison between legacy primary and unified candidate results.

Never logs full document text/content — only IDs, counts, reasons, latencies.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.models.search_execution import SearchExecution

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShadowDiff:
    source_id_overlap_top_k: float
    claim_ids_match: bool
    conflicts_match: bool
    fallbacks_match: bool
    citation_keys_match: bool
    exception_types: tuple[str, ...] = ()
    latency_ms_primary: float = 0.0
    latency_ms_candidate: float = 0.0
    notes: tuple[str, ...] = ()
    primary_source_ids: tuple[str, ...] = ()
    candidate_source_ids: tuple[str, ...] = ()


def _source_id(row: dict[str, Any]) -> str:
    claim_id = row.get("claim_id") or ""
    kid = row.get("knowledge_id") or ""
    bid = row.get("block_id") or ""
    source = row.get("source") or ""
    if claim_id:
        return f"claim:{claim_id}"
    if bid:
        return f"block:{bid}"
    if kid:
        return f"knowledge:{kid}"
    return f"source:{source}"


def _claim_ids(execution: SearchExecution) -> set[str]:
    ids: set[str] = set()
    for row in execution.results:
        if row.get("claim_id"):
            ids.add(str(row["claim_id"]))
        if row.get("source") == "verified_claim" and row.get("claim_id"):
            ids.add(str(row["claim_id"]))
    for row in execution.disclose_claims:
        cid = row.get("claim_id")
        if cid:
            ids.add(str(cid))
    for cid in (execution.trace or {}).get("disclose_claims") or []:
        if cid:
            ids.add(str(cid))
    return ids


def _conflict_keys(execution: SearchExecution) -> set[str]:
    keys: set[str] = set()
    for c in execution.conflicts or ():
        if isinstance(c, dict):
            keys.add(
                str(c.get("conflict_id") or c.get("id") or c.get("type") or sorted(c.items())),
            )
        else:
            keys.add(str(c))
    # also from trace
    for c in (execution.trace or {}).get("conflicts") or []:
        if isinstance(c, dict):
            keys.add(
                str(c.get("conflict_id") or c.get("id") or c.get("type") or sorted(c.items())),
            )
        else:
            keys.add(str(c))
    return keys


def _fallback_keys(execution: SearchExecution) -> set[str]:
    keys: set[str] = set()
    for f in execution.fallbacks or ():
        if isinstance(f, dict):
            keys.add(f"{f.get('from')}|{f.get('to')}|{f.get('reason')}")
        else:
            keys.add(str(f))
    # legacy may only put fallback in trace.stages
    stage_fb = ((execution.trace or {}).get("stages") or {}).get("fallback")
    if stage_fb:
        keys.add(f"stage|{stage_fb}")
    return keys


def _citation_keys(execution: SearchExecution) -> set[str]:
    keys: set[str] = set()
    for row in execution.results:
        cit = row.get("citation")
        if not cit:
            continue
        if isinstance(cit, dict):
            keys.add(
                str(
                    cit.get("id")
                    or cit.get("source_id")
                    or cit.get("block_id")
                    or cit.get("knowledge_id")
                    or sorted(cit.keys()),
                ),
            )
        else:
            keys.add(str(cit))
    return keys


def compare_executions(
    primary: SearchExecution,
    candidate: SearchExecution,
    *,
    top_k: int = 5,
    latency_ms_primary: float = 0.0,
    latency_ms_candidate: float = 0.0,
    exception_types: tuple[str, ...] = (),
) -> ShadowDiff:
    """Compare two SearchExecution values without retaining full text."""
    p_ids = tuple(_source_id(r) for r in primary.results[:top_k])
    c_ids = tuple(_source_id(r) for r in candidate.results[:top_k])
    p_set, c_set = set(p_ids), set(c_ids)
    if not p_set and not c_set:
        overlap = 1.0
    else:
        denom = max(len(p_set), len(c_set), 1)
        overlap = len(p_set & c_set) / denom

    notes: list[str] = []
    claim_match = _claim_ids(primary) == _claim_ids(candidate)
    conflict_match = _conflict_keys(primary) == _conflict_keys(candidate)
    fallback_match = _fallback_keys(primary) == _fallback_keys(candidate)
    citation_match = _citation_keys(primary) == _citation_keys(candidate)

    if not claim_match:
        notes.append("claim_ids_differ")
    if not conflict_match:
        notes.append("conflicts_differ")
    if not fallback_match:
        notes.append("fallbacks_differ")
    if not citation_match:
        notes.append("citations_differ")
    if overlap < 0.95:
        notes.append(f"source_overlap={overlap:.3f}")

    return ShadowDiff(
        source_id_overlap_top_k=overlap,
        claim_ids_match=claim_match,
        conflicts_match=conflict_match,
        fallbacks_match=fallback_match,
        citation_keys_match=citation_match,
        exception_types=exception_types,
        latency_ms_primary=latency_ms_primary,
        latency_ms_candidate=latency_ms_candidate,
        notes=tuple(notes),
        primary_source_ids=p_ids,
        candidate_source_ids=c_ids,
    )


def log_shadow_diff(diff: ShadowDiff, *, query_preview: str = "") -> None:
    """Log comparison summary — IDs and metrics only, never full document text."""
    logger.info(
        "retrieval_shadow query=%r overlap=%.3f claims_ok=%s conflicts_ok=%s "
        "fallbacks_ok=%s citations_ok=%s p_ms=%.1f c_ms=%.1f notes=%s "
        "primary_ids=%s candidate_ids=%s exceptions=%s",
        (query_preview or "")[:80],
        diff.source_id_overlap_top_k,
        diff.claim_ids_match,
        diff.conflicts_match,
        diff.fallbacks_match,
        diff.citation_keys_match,
        diff.latency_ms_primary,
        diff.latency_ms_candidate,
        list(diff.notes),
        list(diff.primary_source_ids),
        list(diff.candidate_source_ids),
        list(diff.exception_types),
    )


def meets_cutover_gates(diff: ShadowDiff) -> bool:
    """Spec §8 cutover thresholds for a single comparison sample."""
    if diff.exception_types:
        return False
    if diff.source_id_overlap_top_k < 0.95:
        return False
    if not diff.claim_ids_match:
        return False
    if not diff.conflicts_match:
        return False
    if not diff.fallbacks_match:
        return False
    if not diff.citation_keys_match:
        return False
    return True
