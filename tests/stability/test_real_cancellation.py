"""Phase 1 — honest cancellation + resource recovery after timeout."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from src.services.deadline import DeadlineTimeout, cooperative_sleep, run_with_deadline


def test_cooperative_timeout_marks_cancelled_true():
    def slow():
        cooperative_sleep(5.0)
        return "nope"

    try:
        run_with_deadline(slow, 0.25)
        assert False, "expected DeadlineTimeout"
    except DeadlineTimeout as exc:
        assert exc.cancelled is True
        assert exc.background_work_may_continue is False


def test_noncooperative_timeout_marks_background_may_continue():
    def hang():
        time.sleep(2.0)
        return "nope"

    try:
        run_with_deadline(hang, 0.2)
        assert False, "expected DeadlineTimeout"
    except DeadlineTimeout as exc:
        assert exc.cancelled is False
        assert exc.background_work_may_continue is True


def test_ask_timeout_envelope_uses_honest_cancelled(monkeypatch):
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    def hang_query(*_a, **_k):
        time.sleep(3.0)
        return {"answer": "x", "sources": [], "route": {"mode": "hybrid"}, "warnings": []}

    monkeypatch.setattr(
        Config,
        "get",
        lambda key, default=None: {
            "rag.ask.total_timeout": 0.25,
            "rag.ask.max_sources": 5,
            "rag.ask.no_answer_threshold": 0.35,
        }.get(key, default),
    )
    monkeypatch.setattr(retrieval, "_should_use_verified_ask", lambda: False)
    monkeypatch.setattr(retrieval, "AppContainer", type("NotApp", (), {}))
    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(rag_pipeline=SimpleNamespace(query=hang_query)),
    )

    t0 = time.monotonic()
    out = retrieval._do_ask("FINAL_CLOSURE_TEST_timeout")
    elapsed = time.monotonic() - t0
    assert elapsed <= 1.2
    route = out["route"]
    assert route["mode"] == "timeout"
    # Non-cooperative sleep → cancelled may be false
    assert "cancelled" in route
    assert route.get("background_work_may_continue") is True or route["cancelled"] is True


def test_ask_cooperative_timeout_cancelled_true(monkeypatch):
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    def slow_query(*_a, **_k):
        cooperative_sleep(10.0)
        return {"answer": "x", "sources": [], "route": {"mode": "hybrid"}, "warnings": []}

    monkeypatch.setattr(
        Config,
        "get",
        lambda key, default=None: {
            "rag.ask.total_timeout": 0.3,
            "rag.ask.max_sources": 5,
            "rag.ask.no_answer_threshold": 0.35,
        }.get(key, default),
    )
    monkeypatch.setattr(retrieval, "_should_use_verified_ask", lambda: False)
    monkeypatch.setattr(retrieval, "AppContainer", type("NotApp", (), {}))
    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(rag_pipeline=SimpleNamespace(query=slow_query)),
    )

    t0 = time.monotonic()
    out = retrieval._do_ask("FINAL_CLOSURE_TEST_timeout_coop")
    elapsed = time.monotonic() - t0
    assert elapsed <= 1.2
    assert out["route"]["mode"] == "timeout"
    assert out["route"]["cancelled"] is True
    assert out["route"].get("background_work_may_continue") is False


def test_fifty_cooperative_timeouts_thread_delta_le_one(monkeypatch):
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    def slow_query(*_a, **_k):
        cooperative_sleep(5.0)
        return {"answer": "x", "sources": [], "route": {"mode": "hybrid"}, "warnings": []}

    monkeypatch.setattr(
        Config,
        "get",
        lambda key, default=None: {
            "rag.ask.total_timeout": 0.15,
            "rag.ask.max_sources": 5,
            "rag.ask.no_answer_threshold": 0.35,
        }.get(key, default),
    )
    monkeypatch.setattr(retrieval, "_should_use_verified_ask", lambda: False)
    monkeypatch.setattr(retrieval, "AppContainer", type("NotApp", (), {}))
    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(rag_pipeline=SimpleNamespace(query=slow_query)),
    )

    baseline = threading.active_count()
    for _ in range(50):
        out = retrieval._do_ask("FINAL_CLOSURE_TEST_t50")
        assert out["route"]["mode"] == "timeout"
    time.sleep(0.4)
    delta = threading.active_count() - baseline
    assert delta <= 1, f"thread leak delta={delta} baseline={baseline} now={threading.active_count()}"
