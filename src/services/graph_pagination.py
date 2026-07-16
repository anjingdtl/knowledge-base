"""Shared bounded pagination for graph traversal tool results.

Both ``graph_traverse`` and ``execute_query(type=graph)`` MUST use
``paginate_graph_result`` so nodes/edges/paths/meta stay consistent.
"""
from __future__ import annotations

from typing import Any


def _node_id(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("id") or node.get("source_id") or node.get("block_id") or "")
    return str(node)


def _edge_source(edge: dict) -> str:
    return str(edge.get("source") or edge.get("from") or "")


def _edge_target(edge: dict) -> str:
    return str(edge.get("target") or edge.get("to") or "")


def _normalize_id(value: str) -> str:
    """Strip known prefixes so page:/block: ids match public ids."""
    if ":" not in value:
        return value
    prefix, rest = value.split(":", 1)
    if prefix in ("page", "block", "tag", "entity", "knowledge"):
        return rest
    return value


def _id_set(nodes: list) -> set[str]:
    ids: set[str] = set()
    for n in nodes:
        raw = _node_id(n)
        if not raw:
            continue
        ids.add(raw)
        ids.add(_normalize_id(raw))
        # keep prefixed forms if present
        if ":" not in raw:
            ids.add(f"page:{raw}")
            ids.add(f"block:{raw}")
    return ids


def compute_graph_fetch_limit(*, limit: int, offset: int, max_graph_nodes: int) -> int:
    """Service-layer max_nodes: at least offset+limit+1, capped by config."""
    limit = max(1, int(limit))
    offset = max(0, int(offset))
    cap = max(1, int(max_graph_nodes))
    return min(cap, offset + limit + 1)


def paginate_graph_result(
    result: dict,
    *,
    limit: int,
    offset: int,
    max_graph_nodes: int | None = None,
) -> dict:
    """Slice graph payload to a page-local subgraph.

    Invariants:
      - ``next_offset is None`` OR ``next_offset > current offset``
      - never returns a self-loop next_offset on an empty page
      - when the hard ``max_graph_nodes`` ceiling is reached, pagination stops
        with ``hard_limit_reached=True`` instead of inventing infinite pages

    Returns::

        {
          "nodes": [...],
          "edges": [...],  # only endpoints in page nodes
          "paths": [...],  # only fully contained paths
          "truncated": bool,  # more pages available within hard cap
          "meta": {
            "limit", "offset", "next_offset",
            "total_estimate", "total_estimate_is_exact",
            "hard_limit_reached", "max_graph_nodes"
          }
        }
    """
    limit = max(0, int(limit))
    offset = max(0, int(offset))
    hard_cap = int(max_graph_nodes) if max_graph_nodes is not None else None
    if hard_cap is not None:
        hard_cap = max(1, hard_cap)

    all_nodes = list(result.get("nodes") or [])
    all_edges = list(result.get("edges") or [])
    all_paths = list(result.get("paths") or [])
    service_truncated = bool(result.get("truncated"))

    total_fetched = len(all_nodes)
    hard_limit_reached = False

    # Offset past the hard ceiling → empty terminal page (no self-loop).
    if hard_cap is not None and offset >= hard_cap:
        return {
            "nodes": [],
            "edges": [],
            "paths": [],
            "truncated": False,
            "meta": {
                "limit": limit,
                "offset": offset,
                "next_offset": None,
                "total_estimate": min(total_fetched, hard_cap),
                "total_estimate_is_exact": False,
                "hard_limit_reached": True,
                "max_graph_nodes": hard_cap,
            },
        }

    # Always apply offset (even when total_fetched <= limit)
    page_nodes = all_nodes[offset : offset + limit] if limit > 0 else []
    if hard_cap is not None:
        # Never return nodes beyond the configured hard ceiling.
        page_nodes = [n for i, n in enumerate(page_nodes) if offset + i < hard_cap]
    page_ids = _id_set(page_nodes)

    def _in_page(raw: str) -> bool:
        return raw in page_ids or _normalize_id(raw) in page_ids

    page_edges = [
        e
        for e in all_edges
        if _in_page(_edge_source(e)) and _in_page(_edge_target(e))
    ]

    page_paths: list[list] = []
    for path in all_paths:
        if not path:
            continue
        if all(
            (str(nid) in page_ids or _normalize_id(str(nid)) in page_ids) for nid in path
        ):
            page_paths.append(path)

    end = offset + len(page_nodes)
    has_more = end < total_fetched and len(page_nodes) > 0

    # Service-level truncation: more graph nodes may exist beyond the fetch
    # window, but only advertise another page when the cursor would advance
    # AND we are still strictly below the hard cap.
    if (
        service_truncated
        and len(page_nodes) > 0
        and end >= total_fetched
    ):
        if hard_cap is None or end < hard_cap:
            has_more = True
        else:
            has_more = False
            hard_limit_reached = True

    # Empty page after a truncated fetch (offset at/past window) → stop.
    if len(page_nodes) == 0:
        has_more = False
        if service_truncated or (hard_cap is not None and offset >= total_fetched):
            hard_limit_reached = True

    # Consumed up to hard cap on this page.
    if hard_cap is not None and end >= hard_cap:
        has_more = False
        hard_limit_reached = True

    next_offset = end if has_more and len(page_nodes) > 0 else None
    # Absolute invariant: next_offset must strictly increase or be null.
    if next_offset is not None and next_offset <= offset:
        next_offset = None
        has_more = False
        hard_limit_reached = True

    total_estimate = total_fetched
    # exact only when service did not truncate the underlying traversal
    total_estimate_is_exact = not service_truncated and not hard_limit_reached

    return {
        "nodes": page_nodes,
        "edges": page_edges,
        "paths": page_paths,
        "truncated": has_more,
        "meta": {
            "limit": limit,
            "offset": offset,
            "next_offset": next_offset,
            "total_estimate": total_estimate,
            "total_estimate_is_exact": total_estimate_is_exact,
            "hard_limit_reached": hard_limit_reached,
            "max_graph_nodes": hard_cap,
        },
    }
