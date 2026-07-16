"""Graph intent must not be stolen by structured tag/fulltext rules."""
from __future__ import annotations

from src.services.route_engine import RuleRouter


def test_graph_signals_win_over_tag_match() -> None:
    r = RuleRouter(db=None)
    out = r.route("与企微有什么关联")
    assert out is not None
    assert out["mode"] == "graph"


def test_reference_query_is_graph() -> None:
    r = RuleRouter(db=None)
    for q in (
        "文档引用了哪些页面",
        "上下游依赖关系",
        "被哪些文档引用",
        "图谱关系路径",
    ):
        out = r.route(q)
        assert out is not None, q
        assert out["mode"] == "graph", q
