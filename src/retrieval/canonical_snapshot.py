"""Canonical retrieval snapshot shared by MCP ``search`` and ``ask``.

SPEC v2 Phase 1: search and ask must share one serializable evidence object —
the same candidates, the same gate decision, and the same accepted ID set.
AnswerService must not silently re-retrieve unconstrained evidence for the
same question.
"""
from __future__ import annotations

from typing import Any, Callable

from src.models.search_execution import SearchExecution
from src.services.relevance_gate import classify_query_intent, evaluate_evidence_unified
from src.services.version_rank import (
    extract_version_year,
    filter_to_latest_versions,
    rank_with_freshness,
)


def build_adjacent_allowlist(
    accepted_items: list[dict[str, Any]],
    *,
    list_blocks_fn: Callable[[str], list[dict[str, Any]]] | None = None,
    window: int = 1,
) -> list[dict[str, Any]]:
    """Build allowlist entries for accepted hits + same-doc adjacent blocks.

    Each entry:
      knowledge_id, block_id, is_adjacent_extension, parent_hit_block_id,
      order_idx, relevance_score (from parent hit when adjacent)
    """
    from src.answering.context_builder import expand_adjacent_evidence

    if list_blocks_fn is None:
        return _allowlist_from_hits_only(accepted_items)

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for hit in accepted_items:
        if not isinstance(hit, dict):
            continue
        kid = str(hit.get("knowledge_id") or "").strip()
        bid = str(hit.get("block_id") or "").strip()
        if not kid:
            continue
        score = hit.get("final_relevance_score", hit.get("score"))
        if bid and (kid, bid) not in seen:
            seen.add((kid, bid))
            out.append({
                "knowledge_id": kid,
                "block_id": bid,
                "is_adjacent_extension": False,
                "parent_hit_block_id": bid,
                "order_idx": hit.get("order_idx"),
                "relevance_score": score,
                "channel": hit.get("match_channel") or hit.get("source") or "",
            })
        try:
            page_blocks = list_blocks_fn(kid) or []
        except Exception:  # noqa: BLE001
            page_blocks = []
        if not page_blocks or not bid:
            continue
        # Normalize page blocks to expand_adjacent_evidence shape.
        normalized = []
        for b in page_blocks:
            if not isinstance(b, dict):
                continue
            nb = dict(b)
            nb.setdefault("block_id", nb.get("id") or "")
            nb.setdefault("knowledge_id", kid)
            if "text" not in nb:
                nb["text"] = nb.get("content") or ""
            normalized.append(nb)
        expanded = expand_adjacent_evidence(
            normalized, focus_block_id=bid, window=window,
        )
        for b in expanded:
            ebid = str(b.get("block_id") or b.get("id") or "").strip()
            if not ebid or (kid, ebid) in seen:
                continue
            seen.add((kid, ebid))
            out.append({
                "knowledge_id": kid,
                "block_id": ebid,
                "is_adjacent_extension": ebid != bid,
                "parent_hit_block_id": bid,
                "order_idx": b.get("order_idx"),
                "relevance_score": score,
                "channel": "adjacent_extension",
                "text": (b.get("text") or b.get("content") or "")[:500],
                "title": hit.get("title") or "",
            })
    return out


def _allowlist_from_hits_only(accepted_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for hit in accepted_items:
        if not isinstance(hit, dict):
            continue
        kid = str(hit.get("knowledge_id") or "").strip()
        bid = str(hit.get("block_id") or "").strip()
        if not kid:
            continue
        key = (kid, bid)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "knowledge_id": kid,
            "block_id": bid,
            "is_adjacent_extension": False,
            "parent_hit_block_id": bid,
            "order_idx": hit.get("order_idx"),
            "relevance_score": hit.get("final_relevance_score", hit.get("score")),
            "channel": hit.get("match_channel") or hit.get("source") or "",
        })
    return out


def apply_post_relevance_freshness(
    query: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply version freshness AFTER final relevance ranking (SPEC v2 §5.1.5).

    For local_version intents, also prefer newest year within the same
    regulation family so a slightly lower-relevance 2026 doc outranks a
    higher-lexical 2023 doc that would otherwise pollute answers.
    """
    if not items:
        return []
    ranked = rank_with_freshness(items)
    intent = classify_query_intent(query)
    if intent == "local_version":
        # Stable secondary key: newer year first within comparable scores.
        ranked = sorted(
            ranked,
            key=lambda r: (
                bool((r.get("version_rank") or {}).get("deprecated")),
                # Prefer higher relevance first, but break ties / near-ties by year.
                -_effective_rank_score(r),
                -(extract_version_year(r) or 0),
            ),
        )
    return ranked


def _effective_rank_score(item: dict[str, Any]) -> float:
    for key in ("ranking_score", "final_relevance_score", "score", "fts_score"):
        v = item.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def snapshot_to_search_execution(snapshot: dict[str, Any]) -> SearchExecution:
    """Project a canonical snapshot onto SearchExecution (no parallel model)."""
    results = tuple(snapshot.get("accepted_items") or ())
    trace = {
        "mode": "canonical_snapshot",
        "query": snapshot.get("query") or "",
        "expanded_queries": list(snapshot.get("expanded_queries") or []),
        "gate": {
            "accept": bool(snapshot.get("accept")),
            "reason": snapshot.get("reason"),
            "top_score": snapshot.get("top_score"),
            "threshold": snapshot.get("threshold"),
            "intent": snapshot.get("intent"),
        },
        "accepted_knowledge_ids": list(snapshot.get("accepted_knowledge_ids") or []),
        "accepted_block_ids": list(snapshot.get("accepted_block_ids") or []),
        "adjacent_allowlist": list(snapshot.get("adjacent_allowlist") or []),
        "stages": dict(snapshot.get("stages") or {}),
    }
    return SearchExecution(results=results, trace=trace)


def build_canonical_snapshot(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    threshold: float = 0.35,
    top_k: int = 5,
    expanded_queries: list[str] | None = None,
    list_blocks_fn: Callable[[str], list[dict[str, Any]]] | None = None,
    adjacent_window: int = 1,
) -> dict[str, Any]:
    """Gate + freshness + allowlist over a candidate list.

    ``candidates`` must already be the shared retrieval output (same path
    used by search and ask probes).
    """
    q = query or ""
    pool = list(candidates or [])
    decision = evaluate_evidence_unified(
        q, pool[: max(top_k, 10)], threshold=threshold,
    )
    intent = decision.get("intent") or classify_query_intent(q)
    accepted = list(decision.get("items") or [])
    # Freshness AFTER relevance ranking (SPEC v2).
    accepted = apply_post_relevance_freshness(q, accepted)
    # For generation / ask, keep a generation_items view that drops superseded
    # versions of the same regulation when the user asked for the latest.
    generation_items = list(accepted)
    if intent == "local_version" and accepted:
        generation_items = filter_to_latest_versions(accepted)

    accepted_kids: list[str] = []
    accepted_blocks: list[str] = []
    seen_k: set[str] = set()
    seen_b: set[str] = set()
    for r in accepted:
        kid = str(r.get("knowledge_id") or "").strip()
        bid = str(r.get("block_id") or "").strip()
        if kid and kid not in seen_k:
            seen_k.add(kid)
            accepted_kids.append(kid)
        if bid and bid not in seen_b:
            seen_b.add(bid)
            accepted_blocks.append(bid)

    allowlist = build_adjacent_allowlist(
        accepted[:top_k],
        list_blocks_fn=list_blocks_fn,
        window=adjacent_window,
    )
    # Ensure generation_items blocks are also allowlisted.
    gen_kids = {
        str(r.get("knowledge_id") or "").strip()
        for r in generation_items
        if r.get("knowledge_id")
    }

    return {
        "query": q,
        "expanded_queries": list(expanded_queries or [q]),
        "raw_candidates": pool,
        "accept": bool(decision.get("accept")),
        "reason": decision.get("reason"),
        "top_score": decision.get("top_score", 0.0),
        "threshold": threshold,
        "intent": intent,
        "accepted_items": accepted[:top_k],
        "generation_items": generation_items[:top_k],
        "accepted_knowledge_ids": accepted_kids,
        "accepted_block_ids": accepted_blocks,
        "generation_knowledge_ids": sorted(gen_kids),
        "adjacent_allowlist": allowlist,
        "gate_evidence": list(decision.get("evidence") or []),
        "stages": {
            "gate": {
                "accept": bool(decision.get("accept")),
                "top_score": decision.get("top_score"),
                "threshold": threshold,
                "reason": decision.get("reason"),
            },
            "freshness_applied_after_relevance": True,
            "local_version_filtered": intent == "local_version",
        },
    }


def source_in_allowlist(
    source: dict[str, Any],
    *,
    accepted_knowledge_ids: set[str],
    accepted_block_ids: set[str],
    adjacent_allowlist: list[dict[str, Any]] | None = None,
) -> bool:
    """True if source is pre-accepted or an explicit adjacent extension."""
    if not isinstance(source, dict):
        return False
    kid = str(source.get("knowledge_id") or "").strip()
    bid = str(source.get("block_id") or "").strip()
    if bid and bid in accepted_block_ids:
        return True
    if kid and kid in accepted_knowledge_ids and not bid:
        # Knowledge-level citation without block is acceptable only when the
        # knowledge item itself was pre-accepted.
        return True
    if kid and bid and kid in accepted_knowledge_ids and bid in accepted_block_ids:
        return True
    for entry in adjacent_allowlist or []:
        if (
            str(entry.get("knowledge_id") or "").strip() == kid
            and str(entry.get("block_id") or "").strip() == bid
        ):
            return True
    return False


def expand_results_with_adjacent(
    hits: list[dict[str, Any]],
    *,
    list_blocks_fn: Callable[[str], list[dict[str, Any]]],
    window: int = 1,
) -> list[dict[str, Any]]:
    """Expand hit blocks with same-knowledge adjacent blocks for LLM context.

    Returns a new list of raw-row shaped dicts. Adjacent rows are tagged
    ``is_adjacent_extension=True`` and keep ``parent_hit_block_id``.
    """
    from src.answering.context_builder import expand_adjacent_evidence

    if not hits:
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for hit in hits:
        if not isinstance(hit, dict):
            continue
        kid = str(hit.get("knowledge_id") or "").strip()
        bid = str(hit.get("block_id") or "").strip()
        title = hit.get("title") or ""
        score = hit.get("final_relevance_score", hit.get("score"))

        # Always include the hit itself first.
        key = (kid, bid or id(hit))
        if key not in seen:
            seen.add(key)
            row = dict(hit)
            row.setdefault("is_adjacent_extension", False)
            row.setdefault("parent_hit_block_id", bid)
            out.append(row)

        if not kid or not bid:
            continue
        try:
            page_blocks = list_blocks_fn(kid) or []
        except Exception:  # noqa: BLE001
            continue
        normalized = []
        for b in page_blocks:
            if not isinstance(b, dict):
                continue
            nb = dict(b)
            nb.setdefault("block_id", nb.get("id") or "")
            nb.setdefault("knowledge_id", kid)
            if "text" not in nb:
                nb["text"] = nb.get("content") or ""
            normalized.append(nb)
        expanded = expand_adjacent_evidence(
            normalized, focus_block_id=bid, window=window,
        )
        for b in expanded:
            ebid = str(b.get("block_id") or b.get("id") or "").strip()
            if not ebid:
                continue
            ekey = (kid, ebid)
            if ekey in seen:
                continue
            seen.add(ekey)
            text = b.get("text") or b.get("content") or ""
            out.append({
                "source": hit.get("source") or "knowledge",
                "knowledge_id": kid,
                "block_id": ebid,
                "title": title,
                "text": text,
                "score": score,
                "final_relevance_score": score,
                "candidate_type": "raw_block",
                "source_layer": "evidence",
                "is_adjacent_extension": ebid != bid,
                "parent_hit_block_id": bid,
                "order_idx": b.get("order_idx"),
                "match_channel": "adjacent_extension",
            })
    return out
