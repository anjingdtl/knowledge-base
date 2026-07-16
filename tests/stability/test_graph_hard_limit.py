"""Phase 2 — max_graph_nodes hard limit must not self-loop."""
from __future__ import annotations

from src.services.graph_pagination import paginate_graph_result


def _fake_truncated(n: int = 200) -> dict:
    return {
        "nodes": [{"id": f"n{i}"} for i in range(n)],
        "edges": [
            {"source": f"n{i}", "target": f"n{i+1}"} for i in range(n - 1)
        ],
        "paths": [[f"n{i}", f"n{i+1}"] for i in range(min(n - 1, 20))],
        "truncated": True,
    }


def test_hard_limit_offsets_never_self_loop():
    raw = _fake_truncated(200)
    for offset in (190, 195, 199, 200, 205):
        page = paginate_graph_result(
            raw, limit=5, offset=offset, max_graph_nodes=200
        )
        nxt = page["meta"]["next_offset"]
        assert nxt is None or nxt > offset, f"self-loop at offset={offset} next={nxt}"
        if offset >= 200:
            assert page["nodes"] == []
            assert page["truncated"] is False
            assert page["meta"]["hard_limit_reached"] is True
            assert nxt is None


def test_auto_paginate_terminates_within_20_pages():
    """Near the hard ceiling, auto-paging must stop (no self-loop)."""
    raw = _fake_truncated(200)
    # Start near the boundary so termination is observable within 20 pages
    # (limit=5 from offset=0 would need 40 pages to cover max=200).
    offset = 150
    seen: set[str] = set()
    pages = 0
    page = paginate_graph_result(raw, limit=5, offset=offset, max_graph_nodes=200)
    while pages < 20:
        ids = [n["id"] for n in page["nodes"]]
        for i in ids:
            assert i not in seen, f"duplicate node {i}"
            seen.add(i)
        pages += 1
        nxt = page["meta"]["next_offset"]
        if nxt is None:
            break
        assert nxt > offset
        offset = nxt
        page = paginate_graph_result(
            raw, limit=5, offset=offset, max_graph_nodes=200
        )
    else:
        raise AssertionError("pagination did not terminate within 20 pages")
    assert pages <= 20
    assert page["meta"]["next_offset"] is None


def test_empty_truncated_page_sets_hard_limit():
    raw = _fake_truncated(200)
    page = paginate_graph_result(raw, limit=5, offset=200, max_graph_nodes=200)
    assert page["nodes"] == []
    assert page["meta"]["next_offset"] is None
    assert page["truncated"] is False
    assert page["meta"]["hard_limit_reached"] is True
