"""ContextBuilder — limited generation context from retrieval rows.

SPEC Phase 5 (KB-019): when a hit block sits inside a clause that is split
across consecutive blocks (e.g. "II类 10万元；III类 20万元" cut between
"...III类支付账" and "户，其余额年付款限额为20万元"), the answer pipeline must
re-join adjacent blocks of the SAME knowledge item so the LLM sees the complete
clause. Without this, the III类 answer picked up the II类 "10万元" from a
truncated fragment.
"""
from __future__ import annotations

from typing import Any

from src.answering.fallbacks import build_generation_context


class ContextBuilder:
    """Build LLM context from claim/raw rows. Does not retrieve or gate."""

    def build(
        self,
        claim_rows: list[dict[str, Any]],
        raw_rows: list[dict[str, Any]],
        *,
        conflicts: list[dict[str, Any]] | None = None,
    ) -> str:
        return build_generation_context(
            claim_rows,
            raw_rows,
            conflicts=list(conflicts or []),
        )


def expand_adjacent_evidence(
    blocks: list[dict[str, Any]],
    *,
    focus_block_id: str | None = None,
    window: int = 1,
    return_audit: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the focus block plus its immediate neighbors from the SAME
    knowledge item, in ``order_idx`` order.

    Used to restore a clause that chunking split across consecutive blocks
    (SPEC Phase 5). Only joins blocks with the same ``knowledge_id`` /
    ``page_id`` — never crosses knowledge-item boundaries, and only when the
    order can be confirmed via ``order_idx``.

    Args:
        blocks: all blocks of ONE knowledge item (caller must pre-filter).
        focus_block_id: the block the retrieval hit; if None, returns ``blocks``
            unchanged (no expansion).
        window: number of neighbors on each side to include (default 1).
        return_audit: when True, also return audit dict with focus status.

    Returns:
        Ordered list [.., prev, focus, next, ..] of distinct blocks.

        SPEC v5 §2.2 / §3: if ``focus_block_id`` is set but not found, returns
        an **empty** list (fail-closed). Never returns the full page blocks.
    """
    audit: dict[str, Any] = {
        "focus_block_id": focus_block_id,
        "focus_found": False,
        "reason": "",
        "adjacent_count": 0,
    }
    if not blocks:
        audit["reason"] = "empty_blocks"
        return ([], audit) if return_audit else []
    if focus_block_id is None:
        audit["reason"] = "no_focus"
        audit["focus_found"] = True
        return (list(blocks), audit) if return_audit else list(blocks)

    # Index by block id; preserve a stable order via order_idx then input order.
    indexed: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        bid = b.get("block_id") or b.get("id") or ""
        if not bid:
            continue
        if bid not in indexed:
            indexed[bid] = b
            order.append(bid)

    if focus_block_id not in indexed:
        # SPEC v5: fail-closed — never expand to full page.
        audit["reason"] = "focus_not_found"
        audit["focus_found"] = False
        return ([], audit) if return_audit else []

    audit["focus_found"] = True
    audit["reason"] = "ok"

    # Sort by order_idx when available (stable fallback to input order).
    def _sort_key(bid: str) -> tuple:
        b = indexed[bid]
        oi = b.get("order_idx")
        try:
            return (0, int(oi))
        except (TypeError, ValueError):
            return (1, order.index(bid))

    sorted_ids = sorted(indexed.keys(), key=_sort_key)
    focus_pos = sorted_ids.index(focus_block_id)
    lo = max(0, focus_pos - window)
    hi = min(len(sorted_ids), focus_pos + window + 1)
    result = [indexed[bid] for bid in sorted_ids[lo:hi]]
    audit["adjacent_count"] = max(0, len(result) - 1)
    return (result, audit) if return_audit else result


def expand_evidence_for_hit(
    blocks_by_kid: dict[str, list[dict[str, Any]]],
    hit_block_id: str | None,
    hit_knowledge_id: str | None,
    *,
    window: int = 1,
) -> list[dict[str, Any]]:
    """Convenience wrapper: look up the focus block's knowledge item in
    ``blocks_by_kid`` and return its expanded neighbors."""
    if not hit_knowledge_id or hit_knowledge_id not in blocks_by_kid:
        return []
    return expand_adjacent_evidence(
        blocks_by_kid[hit_knowledge_id],
        focus_block_id=hit_block_id,
        window=window,
    )
