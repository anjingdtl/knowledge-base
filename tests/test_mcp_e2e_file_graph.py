import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 15) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"MCP process exited early: {proc.returncode}")
        sock = socket.socket()
        try:
            sock.settimeout(0.2)
            sock.connect(("127.0.0.1", port))
            return
        except OSError:
            time.sleep(0.1)
        finally:
            sock.close()
    raise TimeoutError("MCP process did not open port")


def test_mcp_process_file_graph_round_trip(tmp_path):
    config = {
        "storage": {"data_dir": "data", "db_name": "kb.db", "graph_dir": "graph"},
        "wiki": {"enabled": False, "auto_compile": False},
        "rag": {"enable_query_rewriting": False, "enable_rerank": False, "search_mode": "keywords"},
        "embedding": {"api_key": "invalid", "base_url": "http://127.0.0.1", "model": "test"},
        "llm": {"api_key": "invalid", "base_url": "http://127.0.0.1", "model": "test"},
        "mcp": {"allow_http_write": True, "write_policy": ""},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    (tmp_path / "data").mkdir()

    port = _free_port()
    env = os.environ.copy()
    env["SHINEHE_HOME"] = str(tmp_path)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [
            sys.executable,
            "run_mcp.py",
            "-t",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "-p",
            str(port),
            "--config",
            str(config_path),
        ],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port, proc)
        async def run_client():
            from fastmcp import Client

            async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                created = await client.call_tool("create", {
                    "title": "E2E Page",
                    "content": "端到端唯一搜索词",
                    "tags": ["e2e"],
                })
                # Sprint 1 envelope: 工具返回 {"ok": true, "data": {...}, "operation_id": ...}
                assert created.data["ok"] is True
                page_id = created.data["data"]["id"]
                page_path = Path(created.data["data"]["path"])
                assert page_path.exists()
                assert "id::" in page_path.read_text(encoding="utf-8")

                read = await client.call_tool("read", {"item_id": page_id})
                assert read.data["ok"] is True
                assert read.data["data"]["id"] == page_id
                assert "端到端唯一搜索词" in read.data["data"]["content"]

                search = await client.call_tool("search", {"query": "端到端唯一搜索词", "top_k": 5})
                assert search.data["ok"] is True
                assert any(item.get("knowledge_id") == page_id for item in search.data["data"])

                updated = await client.call_tool("update", {"item_id": page_id, "content": "更新后的端到端搜索词"})
                assert updated.data["ok"] is True
                assert "content" in updated.data["data"]["updated_fields"]
                assert "更新后的端到端搜索词" in page_path.read_text(encoding="utf-8")

                deleted = await client.call_tool("delete", {"item_id": page_id})
                assert deleted.data["ok"] is True
                assert deleted.data["data"]["id"] == page_id
                assert not page_path.exists()

        asyncio.run(run_client())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
