"""Tests for graph generation progress callbacks."""

from src.services.graph_builder import GraphBuilder


def test_emit_progress_prefers_structured_callback():
    calls = []
    builder = GraphBuilder(progress_callback=lambda message, current, total: calls.append((message, current, total)))

    builder._emit_progress("分析中", 2, 5)

    assert calls == [("分析中", 2, 5)]


def test_emit_progress_falls_back_to_message_only_callback():
    calls = []
    builder = GraphBuilder(progress_callback=lambda message: calls.append(message))

    builder._emit_progress("分析中", 2, 5)

    assert calls == ["分析中"]
