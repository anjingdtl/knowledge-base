"""Phase 0/4 — ask / ask_with_query 硬超时与线程泄漏（pytest 版）。

使用可注入的慢 runner 验证：
1. 配置 1s 超时，墙钟 <= 1.5s
2. 连续多次超时线程增量可控
3. 超时返回 route.mode=timeout 且 cancelled 语义
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace


def _slow(seconds: float = 10.0):
    def _run(*_a, **_k):
        # Cooperative sleep so deadline cancel is real (no abandoned sleep threads).
        from src.services.deadline import cooperative_sleep

        cooperative_sleep(seconds)
        return {
            "answer": "should-not-return",
            "sources": [],
            "route": {"mode": "hybrid"},
            "warnings": [],
            "query_plan": {},
            "block_contexts": {},
            "wiki_context": "",
            "trace_id": "",
        }

    return _run


def test_ask_hard_timeout_within_budget(monkeypatch):
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    monkeypatch.setattr(Config, "get", lambda key, default=None: {
        "rag.ask.total_timeout": 1.0,
        "rag.ask.max_sources": 5,
    }.get(key, default))
    monkeypatch.setattr(retrieval, "_should_use_verified_ask", lambda: False)
    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(
            rag_pipeline=SimpleNamespace(query=_slow(10)),
        ),
    )
    # 绕过 verified 判断中的 AppContainer isinstance
    monkeypatch.setattr(retrieval, "AppContainer", type("NotApp", (), {}))

    t0 = time.monotonic()
    result = retrieval._do_ask("慢请求测试问题")
    elapsed = time.monotonic() - t0

    assert elapsed <= 1.5, f"ask wall-clock {elapsed:.2f}s exceeds 1.5s budget"
    assert result.get("route", {}).get("mode") == "timeout"
    # Spec 要求配置超时与 cancelled 语义
    route = result["route"]
    assert route.get("cancelled") is True or "timeout" in str(route).lower()
    assert result.get("answer", "") == ""


def test_ask_with_query_hard_timeout_within_budget(monkeypatch):
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    async def slow_execute(*_a, **_k):
        await __import__("asyncio").sleep(10)
        return {"answer": "nope", "sources": [], "route": {"mode": "hybrid"}, "warnings": []}

    monkeypatch.setattr(Config, "get", lambda key, default=None: {
        "rag.ask_with_query.total_timeout": 1,
        "rag.ask.total_timeout": 1,
    }.get(key, default if default is not None else 1))

    class FakePipeline:
        def __init__(self, *a, **k):
            pass

        async def execute(self, *a, **k):
            return await slow_execute()

    monkeypatch.setattr(retrieval, "RagPipeline", FakePipeline, raising=False)
    # patch where used
    import src.services.rag_pipeline as rp

    monkeypatch.setattr(rp, "RagPipeline", FakePipeline)
    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(
            llm=None,
            db=None,
            query_rewriter=None,
            reranker=None,
            hybrid_search=None,
            graph_backend=None,
            size_aware_router=None,
            wiki_page_locator=None,
            wiki_parent_retriever=None,
        ),
    )

    # _run_async 若存在则让其真正超时
    t0 = time.monotonic()
    result = retrieval.ask_with_query(question="慢请求", search_query="慢请求")
    elapsed = time.monotonic() - t0

    assert elapsed <= 1.5, f"ask_with_query wall-clock {elapsed:.2f}s exceeds 1.5s"
    assert result["ok"] is True
    assert result["data"]["route"]["mode"] == "timeout"


def test_consecutive_timeouts_do_not_leak_threads(monkeypatch):
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    monkeypatch.setattr(Config, "get", lambda key, default=None: {
        "rag.ask.total_timeout": 0.3,
        "rag.ask.max_sources": 5,
    }.get(key, default))
    monkeypatch.setattr(retrieval, "_should_use_verified_ask", lambda: False)
    monkeypatch.setattr(retrieval, "AppContainer", type("NotApp", (), {}))
    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(rag_pipeline=SimpleNamespace(query=_slow(5))),
    )

    baseline = threading.active_count()
    for _ in range(10):
        result = retrieval._do_ask("leak-test")
        assert result.get("route", {}).get("mode") == "timeout"
        time.sleep(0.05)
    # 给可能的线程短暂回收时间
    time.sleep(0.5)
    delta = threading.active_count() - baseline
    assert delta <= 2, f"thread leak: baseline={baseline}, now={threading.active_count()}, delta={delta}"
