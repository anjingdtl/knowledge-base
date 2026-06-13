"""Graph view lifecycle regression tests."""

import ast
from pathlib import Path


def test_graph_view_has_single_hide_event_handler():
    source = Path("src/gui/graph_view.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    graph_view = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "GraphView"
    )

    hide_events = [
        node
        for node in graph_view.body
        if isinstance(node, ast.FunctionDef) and node.name == "hideEvent"
    ]

    assert len(hide_events) == 1
