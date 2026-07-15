"""Phase 0/1 — execute_query(type=graph) limit/offset/next_offset（P0）。"""
from __future__ import annotations

import pytest

from tests.stability.conftest import (
    assert_no_dangling_edges,
    assert_paths_in_nodes,
    node_id,
)


def _execute_graph(start_ids: list[str], *, limit: int, offset: int = 0, max_depth: int = 3):
    from src.mcp.tools.retrieval import execute_query

    return execute_query(
        query_spec={
            "start_ids": start_ids,
            "start_type": "knowledge",
            "max_depth": max_depth,
        },
        type="graph",
        limit=limit,
        offset=offset,
    )


def test_execute_query_graph_limit_is_enforced(patch_container, graph_ids):
    result = _execute_graph([graph_ids[0]], limit=5, offset=0)
    assert result["ok"] is True
    nodes = result["data"]["nodes"]
    assert len(nodes) <= 5
    assert len(nodes) == 5 or result["data"].get("truncated") is False
    # 参照全量
    full = _execute_graph([graph_ids[0]], limit=200, offset=0)
    total = len(full["data"]["nodes"])
    if total > 5:
        assert result["data"]["truncated"] is True or (result.get("meta") or {}).get("truncated") is True
        assert (result.get("meta") or {}).get("next_offset") == 5
    assert_no_dangling_edges(nodes, result["data"]["edges"])
    assert_paths_in_nodes(nodes, result["data"].get("paths") or [])


@pytest.mark.parametrize("limit,offset", [(5, 0), (5, 5), (5, 10), (5, 20), (10, 5)])
def test_execute_query_graph_pagination_matches_slice(
    patch_container, graph_ids, limit, offset
):
    full = _execute_graph([graph_ids[0]], limit=200, offset=0)
    all_nodes = full["data"]["nodes"]
    total = len(all_nodes)
    page = _execute_graph([graph_ids[0]], limit=limit, offset=offset)
    assert page["ok"] is True
    expected = all_nodes[offset : offset + limit]
    assert [node_id(n) for n in page["data"]["nodes"]] == [node_id(n) for n in expected]
    meta = page.get("meta") or {}
    assert meta.get("limit") == limit
    assert meta.get("offset") == offset
    has_more = offset + len(expected) < total
    if has_more:
        assert meta.get("next_offset") == offset + len(expected)
        assert page["data"].get("truncated") is True or meta.get("truncated") is True
    assert_no_dangling_edges(page["data"]["nodes"], page["data"]["edges"])


def test_graph_traverse_and_execute_query_agree(patch_container, graph_ids):
    from src.mcp.tools.graph import graph_traverse
    import json

    limit, offset = 5, 5
    gt = graph_traverse(
        start_ids=json.dumps([graph_ids[0]]),
        max_depth=3,
        limit=limit,
        offset=offset,
    )
    eq = _execute_graph([graph_ids[0]], limit=limit, offset=offset)
    assert gt["ok"] and eq["ok"]
    assert [node_id(n) for n in gt["data"]["nodes"]] == [
        node_id(n) for n in eq["data"]["nodes"]
    ]
    assert len(gt["data"]["nodes"]) <= limit
    assert len(eq["data"]["nodes"]) <= limit
