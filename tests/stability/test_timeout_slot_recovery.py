"""Phase 1 — timeout must not permanently occupy request slots."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from src.services.deadline import cooperative_sleep, run_with_deadline


def test_two_hangs_do_not_block_third_request():
    """Two permanent non-coop hangs must not permanently block a third call."""
    started = []
    lock = threading.Lock()

    def hang():
        with lock:
            started.append(time.monotonic())
        time.sleep(30)
        return "done"

    def third():
        with lock:
            started.append(time.monotonic())
        return "ok-third"

    def run_hang():
        try:
            run_with_deadline(hang, 0.2)
        except TimeoutError:
            pass

    t1 = threading.Thread(target=run_hang)
    t2 = threading.Thread(target=run_hang)
    t1.start()
    t2.start()
    t1.join(2)
    t2.join(2)

    t0 = time.monotonic()
    try:
        result = run_with_deadline(third, 1.0)
    except TimeoutError:
        result = None
    elapsed = time.monotonic() - t0

    assert result == "ok-third"
    assert elapsed <= 1.5
    assert len(started) >= 3


def test_ask_slot_recovery_after_timeouts(monkeypatch):
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    calls = {"n": 0}

    def sometimes_slow(question, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            cooperative_sleep(5.0)
        return {
            "answer": f"ok-{calls['n']}",
            "sources": [{"title": "t", "score": 0.9, "text": question}],
            "route": {"mode": "hybrid"},
            "warnings": [],
            "query_plan": {},
            "block_contexts": {},
            "wiki_context": "",
            "trace_id": "",
            "answer_mode": "answer",
        }

    monkeypatch.setattr(
        Config,
        "get",
        lambda key, default=None: {
            "rag.ask.total_timeout": 0.25,
            "rag.ask.max_sources": 5,
            "rag.ask.no_answer_threshold": 0.01,
        }.get(key, default),
    )
    monkeypatch.setattr(retrieval, "_should_use_verified_ask", lambda: False)
    monkeypatch.setattr(retrieval, "AppContainer", type("NotApp", (), {}))
    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(rag_pipeline=SimpleNamespace(query=sometimes_slow)),
    )

    r1 = retrieval._do_ask("FINAL_CLOSURE_TEST_slot1")
    r2 = retrieval._do_ask("FINAL_CLOSURE_TEST_slot2")
    assert r1["route"]["mode"] == "timeout"
    assert r2["route"]["mode"] == "timeout"

    # Raise timeout so third can complete.
    monkeypatch.setattr(
        Config,
        "get",
        lambda key, default=None: {
            "rag.ask.total_timeout": 2.0,
            "rag.ask.max_sources": 5,
            "rag.ask.no_answer_threshold": 0.01,
        }.get(key, default),
    )
    t0 = time.monotonic()
    r3 = retrieval._do_ask("FINAL_CLOSURE_TEST_slot3")
    assert time.monotonic() - t0 <= 2.5
    assert r3.get("route", {}).get("mode") != "timeout" or r3.get("answer")
