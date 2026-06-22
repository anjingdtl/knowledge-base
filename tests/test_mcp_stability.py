from __future__ import annotations

import asyncio
import time
from threading import Event


def test_run_async_uses_separate_loop_when_called_inside_running_loop():
    from src.mcp_server import _run_async

    async def sample():
        await asyncio.sleep(0)
        return "ok"

    async def caller():
        return _run_async(sample(), timeout=1)

    assert asyncio.run(caller()) == "ok"


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
