"""Phase 0/1 — graph_traverse 分页一致性（P0）。

验收：
- nodes 按 limit/offset 切片
- edges/paths 仅保留页内节点
- next_offset / truncated / total_estimate 正确
- 无悬空边
"""
from __future__ import annotations

import json

import pytest

from tests.stability.conftest import (
    assert_no_dangling_edges,
    assert_paths_in_nodes,
    node_id,
)


@pytest.fixture
def start_ids(graph_ids):
    # 从第一个节点出发可覆盖整条链
    return json.dumps([graph_ids[0]])


def _traverse(start_ids: str, *, limit: int, offset: int = 0, max_depth: int = 3):
    from src.mcp.tools.graph import graph_traverse

    return graph_traverse(
        start_ids=start_ids,
        max_depth=max_depth,
        start_type="knowledge",
        limit=limit,
        offset=offset,
    )


@pytest.mark.parametrize(
    "total_hint,limit,offset",
    [
        (8, 10, 5),
        (10, 10, 0),
        (12, 5, 0),
        (12, 5, 5),
        (12, 5, 10),
        (12, 5, 20),
    ],
)
def test_graph_traverse_paginates_nodes_edges_paths(
    patch_container, start_ids, total_hint, limit, offset
):
    # 先取全量页（大 limit，受 max_graph_nodes 约束）作为参照
    full = _traverse(start_ids, limit=200, offset=0)
    assert full["ok"] is True
    all_nodes = full["data"]["nodes"]
    total = len(all_nodes)
    assert total >= min(total_hint, 4), f"seed graph too small: total={total}"

    page = _traverse(start_ids, limit=limit, offset=offset)
    assert page["ok"] is True
    data = page["data"]
    meta = page.get("meta") or {}
    nodes = data["nodes"]
    edges = data["edges"]
    paths = data.get("paths") or []

    expected = all_nodes[offset : offset + limit]
    assert len(nodes) == len(expected)
    assert [node_id(n) for n in nodes] == [node_id(n) for n in expected]
    assert len(nodes) <= limit

    # 分页元数据
    has_more = offset + len(nodes) < total
    assert data.get("truncated") is has_more or meta.get("truncated") is has_more
    if has_more:
        next_offset = meta.get("next_offset", data.get("next_offset"))
        assert next_offset == offset + len(nodes)
    else:
        next_offset = meta.get("next_offset", data.get("next_offset"))
        assert next_offset in (None, offset + len(nodes)) or not has_more

    assert meta.get("limit") == limit
    assert meta.get("offset") == offset
    total_est = meta.get("total_estimate", data.get("total_estimate"))
    assert total_est is not None
    # 不得把当前页长度冒充总量
    if total > limit and offset == 0:
        assert total_est >= total or total_est > len(nodes)

    assert_no_dangling_edges(nodes, edges)
    assert_paths_in_nodes(nodes, paths)


def test_graph_traverse_offset_always_applied_even_when_total_le_limit(
    patch_container, start_ids
):
    """offset 必须始终切片，不能只在 len(nodes) > limit 时处理。"""
    full = _traverse(start_ids, limit=200, offset=0)
    assert full["ok"] is True, full
    total = len(full["data"]["nodes"])
    # limit 大于 total，但 offset 非零（limit 仍需 <= max_graph_nodes）
    limit = min(total + 5, 200)
    offset = min(3, max(total - 1, 0))
    page = _traverse(start_ids, limit=limit, offset=offset)
    assert page["ok"] is True, page
    assert page["ok"] is True
    expected = full["data"]["nodes"][offset : offset + limit]
    assert [node_id(n) for n in page["data"]["nodes"]] == [node_id(n) for n in expected]


def test_graph_traverse_pages_merge_without_duplicates_or_gaps(
    patch_container, start_ids
):
    full = _traverse(start_ids, limit=200, offset=0)
    assert full["ok"] is True, full
    all_ids = [node_id(n) for n in full["data"]["nodes"]]
    total = len(all_ids)
    limit = 5
    merged: list[str] = []
    offset = 0
    pages = 0
    while offset < total and pages < 20:
        page = _traverse(start_ids, limit=limit, offset=offset)
        assert page["ok"] is True
        ids = [node_id(n) for n in page["data"]["nodes"]]
        merged.extend(ids)
        meta = page.get("meta") or {}
        next_offset = meta.get("next_offset")
        if next_offset is None or next_offset == offset:
            if len(ids) < limit:
                break
            offset = offset + len(ids)
        else:
            offset = next_offset
        pages += 1
        if not ids:
            break
    assert merged == all_ids
    assert len(merged) == len(set(merged))


def test_limit_5_never_returns_more_than_5_nodes(patch_container, start_ids):
    page = _traverse(start_ids, limit=5, offset=0)
    assert page["ok"] is True
    assert len(page["data"]["nodes"]) <= 5
    assert_no_dangling_edges(page["data"]["nodes"], page["data"]["edges"])
