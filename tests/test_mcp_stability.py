from __future__ import annotations

import asyncio
import concurrent.futures
import time
from threading import Event, Lock
from types import SimpleNamespace

import pytest


def test_run_async_uses_separate_loop_when_called_inside_running_loop():
    from src.mcp_server import _run_async

    async def sample():
        await asyncio.sleep(0)
        return "ok"

    async def caller():
        return _run_async(sample(), timeout=1)

    assert asyncio.run(caller()) == "ok"


def test_rag_query_uses_separate_loop_when_called_inside_running_loop(monkeypatch):
    from src.services.rag_pipeline import RAGService

    class Pipeline:
        async def execute(self, question, conversation_history=None):
            await asyncio.sleep(0)
            return {
                "answer": f"answer: {question}",
                "sources": [],
                "source_graph": {"nodes": [], "edges": []},
            }

    service = RAGService(deps={})
    service._pipeline = Pipeline()

    def fail_if_self_deadlocking(*args, **kwargs):
        raise AssertionError("must not synchronously wait on the running loop")

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", fail_if_self_deadlocking)
    monkeypatch.setattr(
        service,
        "_direct_query",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("query should not fall back when the pipeline can run")
        ),
    )

    async def caller():
        return service.query("MCP ask")

    assert asyncio.run(caller())["answer"] == "answer: MCP ask"


def test_rag_query_bounds_timed_out_async_bridge_workers():
    from src.services.rag_pipeline import (
        RAG_ASYNC_BRIDGE_MAX_WORKERS,
        RAGService,
    )

    release_pipeline = Event()
    extra_pipeline_started = Event()
    started_events = [Event() for _ in range(RAG_ASYNC_BRIDGE_MAX_WORKERS)]
    lock = Lock()
    started_count = 0
    completed_count = 0

    class BlockingPipeline:
        async def execute(self, question, conversation_history=None):
            nonlocal started_count, completed_count
            with lock:
                started_count += 1
                position = started_count
                if position <= RAG_ASYNC_BRIDGE_MAX_WORKERS:
                    started_events[position - 1].set()
                else:
                    extra_pipeline_started.set()
            try:
                release_pipeline.wait()
                return {"answer": question, "sources": []}
            finally:
                with lock:
                    completed_count += 1

    try:
        for position in range(RAG_ASYNC_BRIDGE_MAX_WORKERS):
            service = RAGService(deps={})
            service._pipeline = BlockingPipeline()
            with pytest.raises(concurrent.futures.TimeoutError):
                service.query(
                    f"blocking MCP ask {position}", timeout=0.02, skip_cache=True
                )
            assert started_events[position].wait(timeout=1)

        service = RAGService(deps={})
        service._pipeline = BlockingPipeline()
        started = time.monotonic()
        with pytest.raises(concurrent.futures.TimeoutError):
            service.query("bridge capacity exhausted", timeout=0.02, skip_cache=True)
        assert time.monotonic() - started < 0.12
        assert not extra_pipeline_started.wait(timeout=0.1)
        with lock:
            assert started_count == RAG_ASYNC_BRIDGE_MAX_WORKERS
    finally:
        release_pipeline.set()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            with lock:
                if completed_count == started_count:
                    break
            time.sleep(0.01)
        with lock:
            assert completed_count == started_count


def test_rag_query_enforces_timeout_when_pipeline_blocks_event_loop():
    from src.services.rag_pipeline import RAGService

    class Pipeline:
        async def execute(self, question, conversation_history=None):
            time.sleep(0.2)
            return {"answer": question, "sources": []}

    service = RAGService(deps={})
    service._pipeline = Pipeline()

    started = time.monotonic()
    with pytest.raises(concurrent.futures.TimeoutError):
        service.query("blocking MCP ask", timeout=0.02, skip_cache=True)
    assert time.monotonic() - started < 0.12


def test_rag_query_cancels_cooperative_pipeline_after_timeout():
    from src.services.rag_pipeline import RAGService

    cancellation_seen = Event()

    class Pipeline:
        async def execute(self, question, conversation_history=None):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancellation_seen.set()
                raise

    service = RAGService(deps={})
    service._pipeline = Pipeline()

    with pytest.raises(concurrent.futures.TimeoutError):
        service.query("cooperative cancellation", timeout=0.02, skip_cache=True)
    assert cancellation_seen.wait(timeout=0.5)


def test_do_ask_returns_timeout_payload_when_rag_query_times_out(monkeypatch):
    from src import mcp_server

    def raise_timeout(*args, **kwargs):
        raise concurrent.futures.TimeoutError()

    monkeypatch.setattr(
        mcp_server,
        "_get_container",
        lambda: SimpleNamespace(rag_pipeline=SimpleNamespace(query=raise_timeout)),
    )

    result = mcp_server._do_ask("blocking MCP ask")

    assert result["route"]["mode"] == "timeout"
    assert result["answer"] == ""


def test_ask_returns_timeout_payload_and_ping_remains_alive(monkeypatch):
    from src import mcp_server
    from src.services.rag_pipeline import RAGService
    from src.utils.config import Config

    pipeline_started = Event()
    pipeline_completed = Event()

    class Pipeline:
        async def execute(self, question, conversation_history=None):
            pipeline_started.set()
            try:
                time.sleep(0.2)
                return {"answer": question, "sources": []}
            finally:
                pipeline_completed.set()

    service = RAGService(deps={})
    service._pipeline = Pipeline()
    original_get = Config.get

    def get_with_short_ask_timeout(key, default=None):
        if key == "rag.ask.total_timeout":
            return 0.02
        return original_get(key, default)

    monkeypatch.setattr(Config, "get", get_with_short_ask_timeout)
    monkeypatch.setattr(
        mcp_server,
        "_get_container",
        lambda: SimpleNamespace(rag_pipeline=service),
    )

    started = time.monotonic()
    ask_result = mcp_server.ask(question="blocked", include_graph=False)
    elapsed = time.monotonic() - started
    ping_result = mcp_server.ping()

    assert ask_result["ok"] is True
    assert ask_result["data"]["answer"] == ""
    assert ask_result["data"]["route"]["mode"] == "timeout"
    assert elapsed < 0.12
    assert ping_result["data"]["status"] == "alive"
    assert pipeline_started.wait(timeout=1)
    assert pipeline_completed.wait(timeout=1)


def test_try_wiki_compile_does_not_block_caller_when_auto_compile_is_enabled(
    setup_db,
    monkeypatch,
):
    from src.services import wiki_compiler
    from src.utils.config import Config

    Config.set("wiki.enabled", True)
    Config.set("wiki.auto_compile", True)
    completed = Event()

    def slow_ingest(self, knowledge_id: str):
        time.sleep(0.4)
        completed.set()
        return {"created": [], "updated": [], "status": "success"}

    monkeypatch.setattr(wiki_compiler.WikiCompiler, "ingest", slow_ingest)

    started = time.monotonic()
    wiki_compiler.try_wiki_compile("kid-1")
    elapsed = time.monotonic() - started

    assert elapsed < 0.15
    assert completed.wait(timeout=1)


def test_try_wiki_compile_is_disabled_when_auto_compile_is_not_configured(monkeypatch):
    """缺省配置不得在写入后触发后台 LLM 编译。"""
    from src.services import wiki_compiler

    calls: list[str] = []

    def get_config(key, default=None):
        return True if key == "wiki.enabled" else default

    def ingest(self, knowledge_id: str):
        calls.append(knowledge_id)

    monkeypatch.setattr(wiki_compiler.Config, "get", get_config)
    monkeypatch.setattr(wiki_compiler.WikiCompiler, "ingest", ingest)

    wiki_compiler.try_wiki_compile("kid-default-off")

    assert calls == []


def test_legacy_external_graph_factory_config_uses_sqlite():
    from src.services.graph_backend import factory

    class DummyConfig:
        def get(self, key, default=None):
            values = {
                "graph_backend.provider": "neo" + "4j",
                "graph_backend.uri": "bolt" + "://bad-host:7687",
                "graph_backend.user": "neo" + "4j",
                "graph_backend.password": "",
                "graph_backend.database": "neo" + "4j",
            }
            return values.get(key, default)

    backend = factory.create_graph_backend(DummyConfig(), db=object())

    assert backend.name == "sqlite"
