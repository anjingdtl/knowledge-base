"""Phase 2 — next_offset must be strictly monotonic or null."""
from __future__ import annotations

import json

from src.services.graph_pagination import paginate_graph_result
from tests.stability.conftest import assert_no_dangling_edges, assert_paths_in_nodes


def test_paginate_next_offset_monotonic_unit():
    nodes = [{"id": f"n{i}"} for i in range(30)]
    edges = [{"source": f"n{i}", "target": f"n{i+1}"} for i in range(29)]
    raw = {"nodes": nodes, "edges": edges, "paths": [], "truncated": False}
    offset = 0
    prev = -1
    for _ in range(10):
        page = paginate_graph_result(raw, limit=5, offset=offset, max_graph_nodes=200)
        nxt = page["meta"]["next_offset"]
        if nxt is None:
            break
        assert nxt > offset
        assert nxt > prev
        prev = nxt
        offset = nxt


def test_graph_traverse_hard_limit_via_tool(patch_container, graph_ids, monkeypatch):
    from src.mcp.tools import graph as graph_mod
    from src.utils.config import Config

    monkeypatch.setattr(
        Config,
        "get",
        lambda key, default=None: 20 if key == "rag.max_graph_nodes" else default,
    )
    # Build a large enough synthetic traversal result via service monkeypatch
    big = {
        "nodes": [{"id": f"g{i}", "source_id": f"g{i}"} for i in range(20)],
        "edges": [{"source": f"g{i}", "target": f"g{i+1}"} for i in range(19)],
        "paths": [],
        "truncated": True,
    }

    class FakeService:
        def __init__(self, *a, **k):
            pass

        def traverse(self, **kwargs):
            max_nodes = int(kwargs.get("max_nodes") or 20)
            return {
                "nodes": big["nodes"][:max_nodes],
                "edges": big["edges"][: max(0, max_nodes - 1)],
                "paths": [],
                "truncated": True,
            }

    monkeypatch.setattr(graph_mod, "GraphTraversalService", FakeService, raising=False)
    import src.services.graph_traversal as gt

    monkeypatch.setattr(gt, "GraphTraversalService", FakeService)

    start = json.dumps([graph_ids[0]])
    offset = 0
    pages = 0
    while pages < 20:
        res = graph_mod.graph_traverse(
            start_ids=start, max_depth=2, limit=5, offset=offset
        )
        assert res["ok"] is True
        meta = res.get("meta") or {}
        data = res["data"]
        assert_no_dangling_edges(data["nodes"], data["edges"])
        assert_paths_in_nodes(data["nodes"], data.get("paths") or [])
        nxt = meta.get("next_offset")
        pages += 1
        if nxt is None:
            break
        assert nxt > offset
        offset = nxt
    assert pages <= 20
