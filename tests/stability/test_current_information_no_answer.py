"""Phase 4 — current / live information queries must no-answer on local KB."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.relevance_gate import is_current_information_query


@pytest.mark.parametrize(
    "q",
    [
        "中国电信股价今天多少",
        "量子计算最新进展",
        "当前实时行情",
    ],
)
def test_current_info_detector(q):
    assert is_current_information_query(q) is True


def test_search_current_info_no_match(monkeypatch):
    from src.mcp.tools import retrieval

    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(search_service=None, hybrid_search=None, db=None),
    )
    res = retrieval.search(query="中国电信股价今天多少", limit=5)
    assert res["ok"] is True
    assert res["data"] == []
    assert res["meta"].get("no_match") is True
    assert res["meta"].get("reason") == "requires_current_external_data"


def test_ask_current_info_no_answer(monkeypatch):
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    monkeypatch.setattr(
        Config,
        "get",
        lambda key, default=None: {
            "rag.ask.total_timeout": 5,
            "rag.ask.max_sources": 5,
            "rag.ask.no_answer_threshold": 0.35,
        }.get(key, default),
    )
    # Should short-circuit before pipeline
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("pipeline should not run for current-info queries")

    monkeypatch.setattr(retrieval, "_should_use_verified_ask", lambda: False)
    monkeypatch.setattr(retrieval, "AppContainer", type("X", (), {}))
    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(rag_pipeline=SimpleNamespace(query=boom)),
    )
    out = retrieval._do_ask("中国电信股价今天多少")
    assert out.get("answer_mode") == "no_answer"
    assert out.get("reason") == "requires_current_external_data"
    assert called["n"] == 0
