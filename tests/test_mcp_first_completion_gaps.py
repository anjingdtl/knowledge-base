"""Regression coverage for MCP-first complete RAG acceptance gaps."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager

import pytest

from src.services.db import Database
from tests.conftest import insert_test_block, insert_test_knowledge


@pytest.fixture
def mcp_env(setup_db, monkeypatch):
    """Keep MCP tests off real embedding/vector backends."""

    class MockVS:
        def __init__(self, db=None):
            pass

        def search(self, query, top_k=5):
            return []

        def add_chunks(self, chunks):
            pass

        def delete_by_knowledge(self, kid):
            pass

        def count(self):
            return 0

    class MockBS:
        def __init__(self, db=None):
            pass

        def search(self, query, top_k=5):
            return []

        def add_block_embedding(self, block_id, embedding):
            pass

        def delete_by_page(self, page_id):
            pass

        def count(self):
            return 0

    class MockEmbedding:
        def __init__(self, config=None):
            pass

        def embed_batch_with_cache(self, texts, batch_size=20):
            return [[0.0] * 1024 for _ in texts]

    monkeypatch.setattr("src.services.vectorstore.VectorStore", MockVS)
    monkeypatch.setattr("src.services.block_store.BlockStore", MockBS)
    monkeypatch.setattr("src.services.embedding.EmbeddingService", MockEmbedding)

    import src.mcp_server as mcp_mod
    mcp_mod._container = None


def test_get_source_graph_returns_envelope_for_block_id(mcp_env):
    from src.mcp_server import get_source_graph

    page_id = insert_test_knowledge(
        title="Graph Source Page",
        content="page body",
        item_id="source-graph-page",
    )
    block_id = insert_test_block(
        page_id,
        content="source graph block",
        block_id="source-graph-block",
    )

    result = get_source_graph(block_ids=[block_id])

    assert result["ok"] is True
    assert result["data"]["node_count"] >= 2
    node_ids = {node["id"] for node in result["data"]["nodes"]}
    assert page_id in node_ids
    assert block_id in node_ids


def test_structured_query_excludes_soft_deleted_items(mcp_env):
    from src.mcp_server import structured_query

    keep_id = insert_test_knowledge(
        title="Visible structured item",
        content="visible",
        tags=["soft-delete-filter"],
        item_id="visible-structured-item",
    )
    deleted_id = insert_test_knowledge(
        title="Deleted structured item",
        content="deleted",
        tags=["soft-delete-filter"],
        item_id="deleted-structured-item",
    )
    assert Database.soft_delete_knowledge(deleted_id) is True

    result = structured_query(
        json.dumps({"filter": {"tag": "soft-delete-filter"}}),
        limit=10,
    )

    assert result["ok"] is True
    ids = {row["id"] for row in result["data"]}
    assert keep_id in ids
    assert deleted_id not in ids


def test_undo_ingest_soft_deletes_created_item(mcp_env):
    from src.mcp_server import undo_operation
    from src.repositories.operation_log_repo import OperationLogRepository

    item_id = insert_test_knowledge(
        title="Undo ingest item",
        content="created by ingest",
        item_id="undo-ingest-item",
    )
    log_id = OperationLogRepository().insert({
        "operation": "ingest",
        "target_type": "knowledge",
        "target_id": item_id,
        "source": "mcp",
        "snapshot_after": json.dumps({"title": "Undo ingest item"}),
    })

    result = undo_operation(log_id)

    assert result["ok"] is True
    assert result["data"]["operation"] == "undo_ingest"
    assert result["data"]["soft_deleted"] is True
    assert Database.get_knowledge(item_id) is None
    assert Database.get_knowledge(item_id, include_deleted=True) is not None


def test_sync_ingest_file_returns_operation_id_for_imported_item(mcp_env, tmp_path):
    from src.mcp_server import ingest_file
    from src.repositories.operation_log_repo import OperationLogRepository
    from src.utils.config import Config

    Config.set("ingest.size_threshold_bytes", 5_000_000)
    path = tmp_path / "small-import.txt"
    path.write_text("small MCP-first import", encoding="utf-8")

    result = ingest_file(str(path), tags=["mcp-import"])

    assert result["ok"] is True
    assert result.get("operation_id")
    entry = OperationLogRepository().get_by_id(result["operation_id"])
    assert entry is not None
    assert entry["operation"] == "ingest"


def test_ingest_url_supports_dry_run_without_fetching(mcp_env, monkeypatch):
    from src.mcp_server import ingest_url

    def fail_if_fetched(url):
        raise AssertionError("dry_run must not fetch the URL")

    monkeypatch.setattr("src.mcp_server.parse_url", fail_if_fetched)

    result = ingest_url("https://example.com/doc", tags=["web"], dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["data"]["would_change"]["url"] == "https://example.com/doc"


def test_mcp_lifespan_starts_and_stops_async_worker(monkeypatch):
    import src.mcp_server as mcp_mod
    import src.services.async_worker as worker_mod

    calls: list[str] = []

    async def fake_heartbeat_loop():
        await asyncio.Event().wait()

    monkeypatch.setattr(mcp_mod, "create_container", lambda: object())
    monkeypatch.setattr(mcp_mod, "shutdown_container", lambda container: calls.append("shutdown"))
    monkeypatch.setattr(mcp_mod, "beat", lambda: None)
    monkeypatch.setattr(mcp_mod, "_heartbeat_loop", fake_heartbeat_loop)
    monkeypatch.setattr(worker_mod, "start_worker", lambda **kwargs: calls.append(f"start:{kwargs}"))
    monkeypatch.setattr(worker_mod, "stop_worker", lambda: calls.append("stop"))

    async def run_lifespan():
        async with mcp_mod.server_lifespan(None):
            calls.append("inside")

    asyncio.run(run_lifespan())

    assert any(call.startswith("start:") for call in calls)
    assert "inside" in calls
    assert "stop" in calls
    assert "shutdown" in calls


def test_api_lifespan_starts_and_stops_async_worker(monkeypatch):
    import src.api as api_mod
    import src.services.async_worker as worker_mod

    calls: list[str] = []

    monkeypatch.setattr(api_mod, "create_container", lambda: object())
    monkeypatch.setattr(api_mod, "shutdown_container", lambda container: calls.append("shutdown"))
    monkeypatch.setattr(worker_mod, "start_worker", lambda **kwargs: calls.append(f"start:{kwargs}"))
    monkeypatch.setattr(worker_mod, "stop_worker", lambda: calls.append("stop"))

    class FakeApp:
        state = type("State", (), {})()

    async def run_lifespan():
        async with api_mod.lifespan(FakeApp()):
            calls.append("inside")

    asyncio.run(run_lifespan())

    assert any(call.startswith("start:") for call in calls)
    assert "inside" in calls
    assert "stop" in calls
    assert "shutdown" in calls


def test_async_worker_processes_ingest_job_end_to_end(mcp_env, tmp_path):
    from src.mcp_server import create_ingest_job, get_job
    from src.services.async_worker import start_worker, stop_worker

    path = tmp_path / "worker-import.txt"
    path.write_text("worker managed MCP import", encoding="utf-8")

    created = create_ingest_job(file_path=str(path), tags=["worker-import"])
    assert created["ok"] is True
    job_id = created["data"]["job_id"]

    start_worker(poll_interval=0.05, max_workers=1)
    try:
        for _ in range(100):
            status = get_job(job_id)
            assert status["ok"] is True
            if status["data"]["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
    finally:
        stop_worker()

    final = get_job(job_id)
    assert final["ok"] is True
    assert final["data"]["status"] == "completed"
    result = final["data"]["result"]
    assert result["created_items"] or result["skipped_items"]
    assert "block_count" in result
