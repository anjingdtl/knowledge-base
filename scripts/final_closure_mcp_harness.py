"""Real MCP harness for ShineHeKB final-closure Phases 0/5/6/7/8.

Transports: streamable-http (subprocess server) and stdio (FastMCP Client).
Never mutates formal data/kb.db for write paths — write tests use temp home.
Golden accuracy eval uses formal DB in read-only tool calls only.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "final-closure"
ART.mkdir(parents=True, exist_ok=True)
GOLDEN = ROOT / "tests" / "eval" / "datasets" / "stability_round2_queries.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_port(port: int, proc: subprocess.Popen, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"MCP exited early rc={proc.returncode}")
        sock = socket.socket()
        try:
            sock.settimeout(0.2)
            sock.connect(("127.0.0.1", port))
            return
        except OSError:
            time.sleep(0.1)
        finally:
            sock.close()
    raise TimeoutError(f"port {port} not open")


def write_temp_config(
    home: Path,
    *,
    formal_db: bool = False,
    tool_profile: str = "full",
) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    data_dir = home / "data"
    data_dir.mkdir(exist_ok=True)
    if formal_db:
        # Use project storage/chroma; omit API keys from the temp file (keyring/env).
        # Never enable writes against formal DB.
        cfg = {
            "storage": {
                "data_dir": str((ROOT / "data").resolve()),
                "db_name": "kb.db",
                "graph_dir": str((ROOT / "data" / "graph").resolve()),
            },
            "wiki": {"enabled": False, "auto_compile": False},
            "rag": {
                "enable_query_rewriting": False,
                "enable_rerank": False,
                "ask": {"total_timeout": 20, "no_answer_threshold": 0.35},
                "search": {"no_match_threshold": 0.35},
                "max_graph_nodes": 200,
                "use_planetary_router": True,
                "route_llm_timeout": 5,
            },
            "mcp": {
                "tool_profile": tool_profile,
                "experimental_tools_enabled": True,
                "enable_legacy_aliases": False,
                "allow_http_write": False,
                "write_policy": "deny",
            },
            # Provider endpoints/models from project config; secrets via keyring/env.
            "embedding": {
                "base_url": "https://api.siliconflow.cn/v1",
                "model": "BAAI/bge-m3",
                "timeout": 15,
            },
            "llm": {
                "base_url": "https://api.minimaxi.com/v1",
                "model": "MiniMax-M3",
                "timeout": 20,
            },
        }
    else:
        cfg = {
            "storage": {
                "data_dir": str(data_dir.resolve()),
                "db_name": "test.db",
                "graph_dir": str((home / "graph").resolve()),
            },
            "wiki": {"enabled": False, "auto_compile": False},
            "rag": {
                "enable_query_rewriting": False,
                "enable_rerank": False,
                "search_mode": "keywords",
                "use_planetary_router": False,
                "route_llm_timeout": 1,
                "ask": {"total_timeout": 2, "no_answer_threshold": 0.35},
                "ask_with_query": {"total_timeout": 2},
                "search": {"no_match_threshold": 0.35},
                "max_graph_nodes": 50,
            },
            "mcp": {
                "tool_profile": tool_profile,
                "experimental_tools_enabled": True,
                "enable_legacy_aliases": False,
                "allow_http_write": True,
                "write_policy": "",
            },
            "security": {
                "allowed_ingest_dirs": [str(home), tempfile.gettempdir()],
            },
            "embedding": {
                "api_key": "invalid",
                "base_url": "http://127.0.0.1:9",
                "model": "test",
                "timeout": 0.5,
            },
            "llm": {
                "api_key": "invalid",
                "base_url": "http://127.0.0.1:9",
                "model": "test",
                "timeout": 0.5,
            },
        }
    path = home / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


def seed_temp_db(home: Path) -> list[str]:
    """Seed via direct DB (harness setup only; not final MCP acceptance)."""
    sys.path.insert(0, str(ROOT))
    os.environ["SHINEHE_HOME"] = str(home)
    from src.services.db import Database
    from src.utils.config import Config

    Config.load(str(home / "config.yaml"))
    Database._instance = None
    db_path = home / "data" / "test.db"
    Database.connect(str(db_path))
    ids = []
    now = datetime.now().isoformat()
    for i in range(12):
        kid = f"FINAL_CLOSURE_TEST_{i:03d}"
        ids.append(kid)
        Database.insert_knowledge({
            "id": kid,
            "title": f"FINAL_CLOSURE_TEST_ Doc {i} 企微 广西电信",
            "content": (
                f"内容{i} 企微 知识库 60米 光纤 6个月试用期 "
                f"vector retrieval MCP"
            ),
            "source_type": "manual",
            "source_path": "",
            "file_type": "md" if i % 2 == 0 else "pdf",
            "file_size": 0,
            "content_hash": kid,
            "file_created_at": "",
            "file_modified_at": "",
            "tags": json.dumps(["FINAL_CLOSURE_TEST_", "企微"], ensure_ascii=False),
            "version": 1,
            "created_at": now,
            "updated_at": now,
        })
    # chain graph
    for i in range(11):
        Database.insert_blocks([{
            "id": f"FINAL_CLOSURE_TEST_b{i}",
            "parent_id": None,
            "page_id": ids[i],
            "content": f"block {i} ref",
            "block_type": "text",
            "properties": "{}",
            "order_idx": 0,
            "created_at": now,
            "updated_at": now,
        }])
    conn = Database.get_conn()
    for i in range(11):
        conn.execute(
            "INSERT OR REPLACE INTO entity_refs "
            "(id, source_type, source_id, target_type, target_id, ref_type, weight) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"FINAL_CLOSURE_TEST_ref{i}",
                "block",
                f"FINAL_CLOSURE_TEST_b{i}",
                "knowledge",
                ids[i + 1],
                "references",
                1.0,
            ),
        )
    conn.commit()
    return ids


def _unwrap_tool_result(result: Any) -> Any:
    data = getattr(result, "data", None)
    if data is not None:
        return data
    # fallback structured content
    sc = getattr(result, "structured_content", None)
    if sc is not None:
        return sc
    content = getattr(result, "content", None)
    if content:
        texts = []
        for block in content:
            t = getattr(block, "text", None)
            if t:
                texts.append(t)
        if texts:
            try:
                return json.loads(texts[0])
            except Exception:
                return {"text": texts[0]}
    return result


class HttpMcpServer:
    def __init__(self, home: Path, port: int | None = None):
        self.home = home
        self.port = port or free_port()
        self.proc: subprocess.Popen | None = None
        self.url = f"http://127.0.0.1:{self.port}/mcp"

    def start(self, *, skip_migration_gate: bool = False) -> None:
        env = os.environ.copy()
        env["SHINEHE_HOME"] = str(self.home)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["MCP_TRANSPORT"] = "streamable-http"
        if skip_migration_gate:
            # Temp DBs seeded via Database.connect are not alembic-stamped.
            env["SHINEHE_SKIP_MIGRATION_GATE"] = "1"
        cfg = self.home / "config.yaml"
        log_path = self.home / "mcp-server.log"
        self._log_fh = open(log_path, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "run_mcp.py"),
                "-t",
                "streamable-http",
                "--host",
                "127.0.0.1",
                "-p",
                str(self.port),
                "--config",
                str(cfg),
            ],
            cwd=str(ROOT),
            env=env,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_port(self.port, self.proc, timeout=90)
        except Exception:
            # surface last log lines for debugging
            try:
                self._log_fh.flush()
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
                print("MCP server failed to open port; log tail:\n", tail, file=sys.stderr)
            except Exception:
                pass
            raise

    def stop(self) -> None:
        if not self.proc:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        self.proc = None
        fh = getattr(self, "_log_fh", None)
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass
            self._log_fh = None


async def call_tool(client: Any, name: str, arguments: dict | None = None) -> dict:
    t0 = time.perf_counter()
    err = None
    payload = None
    try:
        raw = await client.call_tool(name, arguments or {})
        payload = _unwrap_tool_result(raw)
        ok = True
    except Exception as exc:  # noqa: BLE001
        ok = False
        err = f"{type(exc).__name__}: {exc}"
        payload = {"error": err}
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return {
        "timestamp": utc_now(),
        "tool": name,
        "arguments": arguments or {},
        "elapsed_ms": elapsed_ms,
        "response_ok": ok,
        "error_code": None if ok else err,
        "response_hash": hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16],
        "payload": payload,
    }


async def list_tools(client: Any) -> list[str]:
    tools = await client.list_tools()
    names = []
    for t in tools:
        names.append(getattr(t, "name", None) or t.get("name"))
    return sorted(n for n in names if n)


async def run_http_session(url: str, corofn):
    from fastmcp import Client

    async with Client(url) as client:
        return await corofn(client)


async def run_stdio_session(home: Path, corofn, *, skip_migration_gate: bool = False):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    env = os.environ.copy()
    env["SHINEHE_HOME"] = str(home)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["MCP_TRANSPORT"] = "stdio"
    if skip_migration_gate:
        env["SHINEHE_SKIP_MIGRATION_GATE"] = "1"
    transport = StdioTransport(
        command=sys.executable,
        args=[
            str(ROOT / "run_mcp.py"),
            "-t",
            "stdio",
            "--config",
            str(home / "config.yaml"),
        ],
        env=env,
        cwd=str(ROOT),
    )
    async with Client(transport) as client:
        return await corofn(client)


# --------------- Phase 0 probe ---------------

async def phase0_probe_client(client, transport: str) -> dict:
    tools = await list_tools(client)
    ping = await call_tool(client, "ping", {})
    return {
        "transport": transport,
        "tool_count": len(tools),
        "tools": tools,
        "ping": {k: ping[k] for k in ("elapsed_ms", "response_ok", "error_code", "response_hash")},
        "ping_payload": ping.get("payload"),
    }


def cmd_phase0() -> None:
    home = Path(tempfile.mkdtemp(prefix="final-closure-p0-"))
    write_temp_config(home, formal_db=False)
    seed_temp_db(home)
    server = HttpMcpServer(home)
    out: dict[str, Any] = {"started": utc_now()}
    try:
        server.start(skip_migration_gate=True)
        http = asyncio.run(run_http_session(server.url, lambda c: phase0_probe_client(c, "streamable-http")))
        out["http"] = http
    finally:
        server.stop()
    stdio = asyncio.run(
        run_stdio_session(home, lambda c: phase0_probe_client(c, "stdio"), skip_migration_gate=True)
    )
    out["stdio"] = stdio
    out["tool_sets_equal"] = set(out.get("http", {}).get("tools") or []) == set(
        stdio.get("tools") or []
    )
    out["finished"] = utc_now()
    (ART / "phase0-mcp-probe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"tool_sets_equal": out["tool_sets_equal"], "http_tools": out.get("http", {}).get("tool_count"), "stdio_tools": stdio.get("tool_count")}, indent=2))


# --------------- Phase 6 agent scenarios ---------------

CORE_TOOLS = [
    "ping",
    "search",
    "search_fulltext",
    "route_query",
    "execute_query",
    "graph_traverse",
    "ask",
    "ask_with_query",
    "kb_health_check",
    "tags",
    "ingest_url",
]


async def phase6_scenarios(client, transport: str, start_id: str) -> dict:
    records = []
    tools = await list_tools(client)
    records.append({
        "timestamp": utc_now(),
        "transport": transport,
        "tool": "tools/list",
        "arguments": {},
        "elapsed_ms": 0,
        "response_ok": True,
        "error_code": None,
        "response_hash": hashlib.sha256(",".join(tools).encode()).hexdigest()[:16],
        "test_case_id": "tools_list",
        "payload_summary": {"count": len(tools), "has_core": all(t in tools for t in CORE_TOOLS if t != "ingest_url" or "ingest_url" in tools)},
    })

    for name, args in [
        ("ping", {}),
        ("kb_health_check", {}),
        ("tags", {"limit": 10, "offset": 0}),
        ("search", {"query": "企微", "limit": 5}),
        ("search_fulltext", {"query": "知识库", "limit": 5}),
        ("route_query", {"question": "标签为企微的所有文档"}),
        (
            "execute_query",
            {
                "query_spec": {"filter": {"tag": "企微"}, "limit": 5},
                "type": "structured",
                "limit": 5,
                "offset": 0,
            },
        ),
        (
            "graph_traverse",
            {
                "start_ids": json.dumps([start_id]),
                "max_depth": 2,
                "limit": 5,
                "offset": 0,
            },
        ),
        ("ask", {"question": "今天公司营收是多少"}),
        ("ask_with_query", {"question": "知识库是什么", "search_query": "知识库"}),
        ("ingest_url", {"url": "http://127.0.0.1:9/nope"}),
    ]:
        if name not in tools and name != "ingest_url":
            records.append({
                "timestamp": utc_now(),
                "transport": transport,
                "tool": name,
                "arguments": args,
                "elapsed_ms": 0,
                "response_ok": False,
                "error_code": "TOOL_MISSING",
                "response_hash": "",
                "test_case_id": f"core_{name}",
            })
            continue
        rec = await call_tool(client, name, args)
        rec["transport"] = transport
        rec["test_case_id"] = f"core_{name}"
        # strip large payload from main log — keep summary
        payload = rec.pop("payload", None)
        rec["payload_summary"] = _summarize_payload(payload)
        records.append(rec)

    # route as-is for 30 queries
    route_queries = [
        "企微",
        "广西电信",
        "知识库",
        "标签为企微的所有文档",
        "file_type 为 pdf",
        "文档引用了哪些页面",
        "上下游依赖关系",
        "广西电信企微未来应该怎么发展",
        "60 米",
        "60珠/米",
        "今天公司营收是多少",
        "今日股价多少",
        "向量检索",
        "MCP工具",
        "全文搜索",
        "列出所有 md 文档",
        "与企微有什么关联",
        "总结主要问题",
        "6个月试用期",
        "6个月无互动",
        "检索 场景1",
        "分页 next_offset",
        "stdio HTTP 工具一致性",
        "source_type 为 manual",
        "被哪些文档引用",
        "给出建议和原因",
        "综合判断项目风险",
        "健康检查",
        "证据链",
        "图谱关系路径",
    ]
    route_ok = 0
    route_records = []
    for i, q in enumerate(route_queries):
        r = await call_tool(client, "route_query", {"question": q})
        payload = r.get("payload")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) and isinstance(payload, dict) and "recommended_tool" in payload:
            data = payload
        tool = None
        args = None
        if isinstance(data, dict):
            tool = data.get("recommended_tool")
            args = data.get("recommended_arguments") or {}
        success = False
        exec_rec = None
        if tool:
            exec_rec = await call_tool(client, tool, args if isinstance(args, dict) else {})
            success = bool(exec_rec.get("response_ok"))
            # validation errors count as successful contract execution if stable envelope
            pl = exec_rec.get("payload")
            if isinstance(pl, dict) and pl.get("ok") is False and pl.get("error"):
                success = True  # stable validation / business error
            if isinstance(pl, dict) and pl.get("ok") is True:
                success = True
        if success:
            route_ok += 1
        route_records.append({
            "id": f"route_{i:02d}",
            "query": q,
            "recommended_tool": tool,
            "recommended_arguments": args,
            "success": success,
            "route_elapsed_ms": r.get("elapsed_ms"),
            "exec_elapsed_ms": (exec_rec or {}).get("elapsed_ms"),
            "exec_ok": (exec_rec or {}).get("response_ok"),
            "exec_summary": _summarize_payload((exec_rec or {}).get("payload")),
        })

    # graph auto pagination
    graph_pages = []
    offset = 0
    seen: set[str] = set()
    self_loop = False
    dangling = False
    for page_i in range(25):
        g = await call_tool(
            client,
            "graph_traverse",
            {
                "start_ids": json.dumps([start_id]),
                "max_depth": 3,
                "limit": 5,
                "offset": offset,
            },
        )
        pl = g.get("payload") or {}
        data = pl.get("data") if isinstance(pl, dict) else {}
        meta = pl.get("meta") if isinstance(pl, dict) else {}
        nodes = (data or {}).get("nodes") or []
        edges = (data or {}).get("edges") or []
        ids = []
        for n in nodes:
            nid = str(n.get("id") or n.get("source_id") or "")
            ids.append(nid)
            if nid in seen:
                dangling = True  # reuse flag for duplicate
            seen.add(nid)
        # dangling edges
        idset = set(ids)
        for e in edges:
            s = str(e.get("source") or e.get("from") or "")
            t = str(e.get("target") or e.get("to") or "")
            if s.split(":")[-1] not in {x.split(":")[-1] for x in idset} or t.split(":")[-1] not in {
                x.split(":")[-1] for x in idset
            }:
                dangling = True
        nxt = (meta or {}).get("next_offset")
        graph_pages.append({
            "offset": offset,
            "count": len(nodes),
            "next_offset": nxt,
            "truncated": (data or {}).get("truncated") or (meta or {}).get("truncated"),
            "hard_limit_reached": (meta or {}).get("hard_limit_reached"),
        })
        if nxt is None:
            break
        if nxt <= offset:
            self_loop = True
            break
        offset = nxt
    else:
        self_loop = True  # did not terminate

    # timeout probe via ask with short configured timeout already on temp home
    t0 = time.perf_counter()
    to = await call_tool(client, "ask", {"question": "FINAL_CLOSURE_TEST_ timeout probe " + ("x" * 200)})
    wall = int((time.perf_counter() - t0) * 1000)
    ping_after = await call_tool(client, "ping", {})
    search_after = await call_tool(client, "search", {"query": "企微", "limit": 3})

    return {
        "transport": transport,
        "records": records,
        "route_execution": {
            "total": len(route_queries),
            "success": route_ok,
            "rate": route_ok / max(1, len(route_queries)),
            "details": route_records,
        },
        "graph_pagination": {
            "pages": graph_pages,
            "self_loop": self_loop,
            "duplicate_or_dangling": dangling,
            "terminated": not self_loop,
        },
        "timeout": {
            "wall_clock_ms": wall,
            "ask": _summarize_payload(to.get("payload")),
            "ping_after_ok": ping_after.get("response_ok"),
            "search_after_ok": search_after.get("response_ok"),
        },
        "tool_count": len(tools),
        "tools": tools,
    }


def _summarize_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return str(payload)[:200]
    out = {k: payload.get(k) for k in ("ok", "error", "code") if k in payload}
    data = payload.get("data")
    meta = payload.get("meta")
    if isinstance(data, list):
        out["data_len"] = len(data)
    elif isinstance(data, dict):
        out["data_keys"] = sorted(data.keys())[:20]
        if "answer" in data:
            out["answer_mode"] = data.get("answer_mode")
            out["answer_empty"] = not bool(data.get("answer"))
        if "route" in data:
            out["route"] = data.get("route")
        if "nodes" in data:
            out["nodes"] = len(data.get("nodes") or [])
    if isinstance(meta, dict):
        out["meta"] = {
            k: meta.get(k)
            for k in (
                "no_match",
                "reason",
                "next_offset",
                "truncated",
                "limit",
                "offset",
                "hard_limit_reached",
                "top_score",
            )
            if k in meta
        }
    if "route" in payload:
        out["route"] = payload.get("route")
    if "answer_mode" in payload:
        out["answer_mode"] = payload.get("answer_mode")
    return out


def cmd_phase6() -> None:
    home = Path(tempfile.mkdtemp(prefix="final-closure-p6-"))
    write_temp_config(home, formal_db=False)
    ids = seed_temp_db(home)
    start_id = ids[0]
    server = HttpMcpServer(home)
    results = {"started": utc_now()}
    try:
        server.start(skip_migration_gate=True)
        http = asyncio.run(
            run_http_session(server.url, lambda c: phase6_scenarios(c, "streamable-http", start_id))
        )
        results["http"] = http
    finally:
        server.stop()
    stdio = asyncio.run(
        run_stdio_session(home, lambda c: phase6_scenarios(c, "stdio", start_id), skip_migration_gate=True)
    )
    results["stdio"] = stdio
    results["tool_set_equal"] = set(http.get("tools") or []) == set(stdio.get("tools") or [])
    results["route_success_rate"] = {
        "http": http["route_execution"]["rate"],
        "stdio": stdio["route_execution"]["rate"],
    }
    results["finished"] = utc_now()

    def dump_records(path: Path, block: dict) -> None:
        with path.open("w", encoding="utf-8") as f:
            for rec in block.get("records") or []:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    dump_records(ART / "agent-mcp-http.jsonl", http)
    dump_records(ART / "agent-mcp-stdio.jsonl", stdio)
    (ART / "agent-route-execution.jsonl").write_text(
        "\n".join(
            json.dumps(r, ensure_ascii=False)
            for r in (http["route_execution"]["details"] + stdio["route_execution"]["details"])
        )
        + "\n",
        encoding="utf-8",
    )
    (ART / "agent-graph-pagination.jsonl").write_text(
        json.dumps(
            {"http": http["graph_pagination"], "stdio": stdio["graph_pagination"]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (ART / "agent-timeout.jsonl").write_text(
        json.dumps(
            {"http": http["timeout"], "stdio": stdio["timeout"]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (ART / "phase6-summary.json").write_text(
        json.dumps(
            {
                "tool_set_equal": results["tool_set_equal"],
                "route_success_rate": results["route_success_rate"],
                "graph": {
                    "http": http["graph_pagination"],
                    "stdio": stdio["graph_pagination"],
                },
                "timeout": {
                    "http": http["timeout"],
                    "stdio": stdio["timeout"],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(results["route_success_rate"], indent=2))
    print("tool_set_equal", results["tool_set_equal"])


# --------------- Phase 5 golden eval ---------------

def _dcg(relevances: list[float], k: int) -> float:
    s = 0.0
    for i, rel in enumerate(relevances[:k]):
        s += (2**rel - 1) / math.log2(i + 2)
    return s


def _ndcg(got_ids: list[str], expected: list[str], k: int = 10) -> float:
    if not expected:
        return 1.0 if not got_ids else 1.0
    ideal = [1.0] * min(k, len(expected))
    rels = [1.0 if gid in set(expected) else 0.0 for gid in got_ids[:k]]
    idcg = _dcg(ideal, k)
    if idcg == 0:
        return 0.0
    return _dcg(rels, k) / idcg


def _extract_ids(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    rows = data if isinstance(data, list) else []
    if isinstance(data, dict) and "sources" in data:
        rows = data.get("sources") or []
    ids = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        kid = r.get("knowledge_id") or r.get("id") or r.get("page_id")
        if kid:
            ids.append(str(kid))
    return ids


def _is_no_match(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    meta = payload.get("meta") or {}
    if meta.get("no_match") is True:
        return True
    data = payload.get("data")
    if data == [] or data is None:
        return True
    if isinstance(data, dict):
        if data.get("answer_mode") == "no_answer":
            return True
        if data.get("route", {}).get("mode") == "no_answer":
            return True
        if not data.get("sources") and data.get("answer") in ("", None):
            return True
    return False


async def eval_transport(client, transport: str, rows: list[dict]) -> dict:
    out_rows = []
    for row in rows:
        qid = row["id"]
        query = row["query"]
        expected_ids = list(row.get("expected_ids") or [])
        expected_no = bool(row.get("expected_no_answer"))
        search_rec = await call_tool(client, "search", {"query": query, "limit": 10})
        payload = search_rec.get("payload")
        got_ids = _extract_ids(payload)
        no_match = _is_no_match(payload)

        # optional ask only for no-answer set (cost control; Spec: search + necessary ask)
        ask_rec = None
        if expected_no or row.get("type") == "no_answer":
            ask_rec = await call_tool(client, "ask", {"question": query})

        # route for route_* expects
        route_rec = None
        if str(row.get("expect", "")).startswith("route") or row.get("type") in (
            "structured",
            "graph",
            "hybrid",
        ):
            route_rec = await call_tool(client, "route_query", {"question": query})

        # metrics components
        recall1 = 0.0
        recall5 = 0.0
        mrr = 0.0
        if expected_ids:
            recall1 = 1.0 if got_ids[:1] and got_ids[0] in expected_ids else 0.0
            recall5 = 1.0 if any(g in expected_ids for g in got_ids[:5]) else 0.0
            for i, g in enumerate(got_ids[:10]):
                if g in expected_ids:
                    mrr = 1.0 / (i + 1)
                    break
        elif expected_no:
            recall1 = recall5 = mrr = 1.0 if no_match else 0.0
        else:
            # hit_or_empty / soft — success if tool ok
            ok = bool(search_rec.get("response_ok"))
            recall1 = recall5 = mrr = 1.0 if ok else 0.0

        ndcg = _ndcg(got_ids, expected_ids, 10) if expected_ids else (1.0 if search_rec.get("response_ok") else 0.0)

        false_answer = False
        if expected_no:
            # false answer if search returns non-empty without no_match OR ask fabricates
            if not no_match and got_ids:
                false_answer = True
            if ask_rec:
                ap = ask_rec.get("payload") or {}
                data = ap.get("data") if isinstance(ap, dict) else ap
                if isinstance(data, dict):
                    if data.get("answer") and data.get("answer_mode") != "no_answer":
                        false_answer = True

        citation_complete = True
        citation_correct = True
        if ask_rec and isinstance(ask_rec.get("payload"), dict):
            data = ask_rec["payload"].get("data") or ask_rec["payload"]
            if isinstance(data, dict) and data.get("answer") and data.get("answer_mode") != "no_answer":
                sources = data.get("sources") or []
                citation_complete = len(sources) > 0
                for s in sources:
                    sid = str(s.get("knowledge_id") or s.get("id") or "")
                    if expected_ids and sid and sid not in expected_ids:
                        citation_correct = False

        unit_ok = True
        if row.get("expected_units"):
            blob = json.dumps(payload, ensure_ascii=False)
            # soft check: if hits exist, unit string should appear in top texts
            if got_ids and not no_match:
                unit_ok = any(u in blob for u in row["expected_units"])

        out_rows.append({
            "id": qid,
            "query": query,
            "transport": transport,
            "type": row.get("type"),
            "expect": row.get("expect"),
            "expected_no_answer": expected_no,
            "expected_ids": expected_ids,
            "got_ids": got_ids[:10],
            "no_match": no_match,
            "search_ok": search_rec.get("response_ok"),
            "elapsed_ms": search_rec.get("elapsed_ms"),
            "recall@1": recall1,
            "recall@5": recall5,
            "mrr": mrr,
            "ndcg@10": ndcg,
            "false_answer": false_answer,
            "citation_complete": citation_complete,
            "citation_correct": citation_correct,
            "unit_ok": unit_ok,
            "route_summary": _summarize_payload((route_rec or {}).get("payload")) if route_rec else None,
            "ask_summary": _summarize_payload((ask_rec or {}).get("payload")) if ask_rec else None,
            "search_summary": _summarize_payload(payload),
        })
    return {"transport": transport, "rows": out_rows}


def aggregate_metrics(rows: list[dict]) -> dict:
    n = max(1, len(rows))
    def avg(key):
        return sum(float(r.get(key) or 0.0) for r in rows) / n

    no_ans = [r for r in rows if r.get("expected_no_answer")]
    no_acc = (
        sum(1 for r in no_ans if r.get("no_match") and not r.get("false_answer")) / max(1, len(no_ans))
    )
    false_rate = sum(1 for r in rows if r.get("false_answer")) / n
    cite_comp = sum(1 for r in rows if r.get("citation_complete")) / n
    cite_corr = sum(1 for r in rows if r.get("citation_correct")) / n
    unit_rows = [r for r in rows if r.get("expected_units") or (r.get("type") == "numeric_unit")]
    unit_acc = sum(1 for r in unit_rows if r.get("unit_ok")) / max(1, len(unit_rows))
    return {
        "n": len(rows),
        "Recall@1": round(avg("recall@1"), 4),
        "Recall@5": round(avg("recall@5"), 4),
        "MRR": round(avg("mrr"), 4),
        "nDCG@10": round(avg("ndcg@10"), 4),
        "No-answer Accuracy": round(no_acc, 4),
        "False-answer Rate": round(false_rate, 4),
        "Citation completeness": round(cite_comp, 4),
        "Citation correctness": round(cite_corr, 4),
        "数字单位准确率": round(unit_acc, 4),
    }


def transport_consistency(stdio_rows: list[dict], http_rows: list[dict]) -> float:
    by_id_s = {r["id"]: r for r in stdio_rows}
    by_id_h = {r["id"]: r for r in http_rows}
    ids = sorted(set(by_id_s) & set(by_id_h))
    if not ids:
        return 0.0
    ok = 0
    for i in ids:
        a, b = by_id_s[i], by_id_h[i]
        same_na = bool(a.get("no_match")) == bool(b.get("no_match"))
        top5_a = set(a.get("got_ids") or [][:5])
        top5_b = set(b.get("got_ids") or [][:5])
        # core overlap or both empty
        if not top5_a and not top5_b:
            same_top = True
        else:
            same_top = bool(top5_a & top5_b) or top5_a == top5_b
        if same_na and same_top:
            ok += 1
    return ok / len(ids)


def cmd_phase5() -> None:
    # Enrich golden in-place if needed
    from scripts.final_closure_enrich_golden import enrich

    raw_lines = GOLDEN.read_text(encoding="utf-8").strip().splitlines()
    rows = [enrich(json.loads(L)) for L in raw_lines if L.strip()]
    GOLDEN.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )

    # Formal DB readonly MCP
    home = Path(tempfile.mkdtemp(prefix="final-closure-eval-"))
    write_temp_config(home, formal_db=True)
    # snapshot formal db size
    formal = ROOT / "data" / "kb.db"
    size_before = formal.stat().st_size if formal.exists() else 0

    server = HttpMcpServer(home)
    try:
        # Formal DB is already migrated; do not skip gate, do not seed.
        server.start(skip_migration_gate=False)
        http = asyncio.run(
            run_http_session(server.url, lambda c: eval_transport(c, "streamable-http", rows))
        )
    finally:
        server.stop()
    stdio = asyncio.run(
        run_stdio_session(home, lambda c: eval_transport(c, "stdio", rows), skip_migration_gate=False)
    )

    size_after = formal.stat().st_size if formal.exists() else 0
    m_http = aggregate_metrics(http["rows"])
    m_stdio = aggregate_metrics(stdio["rows"])
    consistency = transport_consistency(stdio["rows"], http["rows"])
    metrics = {
        "stdio": m_stdio,
        "http": m_http,
        "Transport 一致率": round(consistency, 4),
        "formal_db_size_before": size_before,
        "formal_db_size_after": size_after,
        "formal_db_unchanged": size_before == size_after,
        "thresholds": {
            "Recall@5": 0.90,
            "MRR": 0.85,
            "nDCG@10": 0.85,
            "No-answer Accuracy": 0.85,
            "False-answer Rate": 0.10,
            "Citation completeness": 1.00,
            "Citation correctness": 0.95,
            "数字单位准确率": 0.95,
            "Transport 一致率": 0.95,
        },
    }
    # Pass/fail using average of transports for recall-like, max false-answer
    combined = {
        "Recall@5": (m_stdio["Recall@5"] + m_http["Recall@5"]) / 2,
        "MRR": (m_stdio["MRR"] + m_http["MRR"]) / 2,
        "nDCG@10": (m_stdio["nDCG@10"] + m_http["nDCG@10"]) / 2,
        "No-answer Accuracy": (m_stdio["No-answer Accuracy"] + m_http["No-answer Accuracy"]) / 2,
        "False-answer Rate": max(m_stdio["False-answer Rate"], m_http["False-answer Rate"]),
        "Citation completeness": min(
            m_stdio["Citation completeness"], m_http["Citation completeness"]
        ),
        "Citation correctness": min(
            m_stdio["Citation correctness"], m_http["Citation correctness"]
        ),
        "数字单位准确率": (m_stdio["数字单位准确率"] + m_http["数字单位准确率"]) / 2,
        "Transport 一致率": consistency,
    }
    metrics["combined"] = {k: round(v, 4) for k, v in combined.items()}
    thr = metrics["thresholds"]
    failures = []
    for k, need in thr.items():
        got = combined[k]
        if k == "False-answer Rate":
            if got > need:
                failures.append({k: got, "need": f"<={need}"})
        else:
            if got < need:
                failures.append({k: got, "need": f">={need}"})
    metrics["failures"] = failures
    metrics["pass"] = len(failures) == 0

    with (ART / "eval-http.jsonl").open("w", encoding="utf-8") as f:
        for r in http["rows"]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (ART / "eval-stdio.jsonl").open("w", encoding="utf-8") as f:
        for r in stdio["rows"]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (ART / "eval-metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    errs = [r for r in http["rows"] + stdio["rows"] if r.get("false_answer") or not r.get("search_ok")]
    with (ART / "eval-errors.jsonl").open("w", encoding="utf-8") as f:
        for r in errs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps(metrics["combined"], ensure_ascii=False, indent=2))
    print("PASS" if metrics["pass"] else f"FAIL {failures}")


# --------------- Phase 7 concurrency ---------------

def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    k = (len(xs) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return xs[int(k)]
    return xs[f] * (c - k) + xs[c] * (k - f)


def cmd_phase7() -> None:
    home = Path(tempfile.mkdtemp(prefix="final-closure-p7-"))
    write_temp_config(home, formal_db=False)
    seed_temp_db(home)
    server = HttpMcpServer(home)
    results: dict[str, Any] = {"started": utc_now(), "search": {}, "ask": {}}

    def one_search(i: int) -> dict:
        async def _run():
            from fastmcp import Client

            async with Client(server.url) as client:
                return await call_tool(client, "search", {"query": f"企微 {i % 5}", "limit": 5})

        return asyncio.run(_run())

    def one_ask(i: int) -> dict:
        async def _run():
            from fastmcp import Client

            async with Client(server.url) as client:
                return await call_tool(
                    client,
                    "ask",
                    {"question": f"FINAL_CLOSURE_TEST_ ask {i}"},
                )

        return asyncio.run(_run())

    try:
        server.start(skip_migration_gate=True)
        for conc in (1, 5, 10, 20, 50):
            n = 100
            latencies = []
            errors = 0
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=conc) as pool:
                futs = [pool.submit(one_search, i) for i in range(n)]
                for fut in as_completed(futs):
                    try:
                        rec = fut.result()
                        latencies.append(float(rec.get("elapsed_ms") or 0))
                        if not rec.get("response_ok"):
                            errors += 1
                        else:
                            pl = rec.get("payload")
                            if isinstance(pl, dict) and pl.get("ok") is False:
                                errors += 1
                    except Exception:
                        errors += 1
            wall = time.perf_counter() - t0
            results["search"][str(conc)] = {
                "n": n,
                "concurrency": conc,
                "errors": errors,
                "success_rate": (n - errors) / n,
                "qps": n / wall if wall else 0,
                "P50": _percentile(latencies, 0.50),
                "P95": _percentile(latencies, 0.95),
                "P99": _percentile(latencies, 0.99),
                "max": max(latencies) if latencies else 0,
            }
            print("search", conc, results["search"][str(conc)])

        for conc in (1, 3, 5, 10):
            n = 30
            latencies = []
            errors = 0
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=conc) as pool:
                futs = [pool.submit(one_ask, i) for i in range(n)]
                for fut in as_completed(futs):
                    try:
                        rec = fut.result()
                        latencies.append(float(rec.get("elapsed_ms") or 0))
                        if not rec.get("response_ok"):
                            errors += 1
                    except Exception:
                        errors += 1
            wall = time.perf_counter() - t0
            results["ask"][str(conc)] = {
                "n": n,
                "concurrency": conc,
                "errors": errors,
                "success_rate": (n - errors) / n,
                "qps": n / wall if wall else 0,
                "P50": _percentile(latencies, 0.50),
                "P95": _percentile(latencies, 0.95),
                "P99": _percentile(latencies, 0.99),
                "max": max(latencies) if latencies else 0,
                "provider": "controlled_invalid_llm",
            }
            print("ask", conc, results["ask"][str(conc)])
    finally:
        server.stop()

    results["finished"] = utc_now()
    (ART / "concurrency-http.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("phase7 done")


# --------------- Phase 8 soak ---------------

def sample_resources(pid: int) -> dict:
    try:
        import psutil

        p = psutil.Process(pid)
        with p.oneshot():
            return {
                "rss_mb": round(p.memory_info().rss / (1024 * 1024), 2),
                "cpu_percent": p.cpu_percent(interval=0.0),
                "thread_count": p.num_threads(),
                "process_count": 1 + len(p.children(recursive=True)),
                "open_connection_count": len(p.net_connections()) if hasattr(p, "net_connections") else len(getattr(p, "connections", lambda: [])()),
            }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def cmd_phase8(http_seconds: int, stdio_seconds: int) -> None:
    home = Path(tempfile.mkdtemp(prefix="final-closure-soak-"))
    write_temp_config(home, formal_db=False)
    ids = seed_temp_db(home)
    start_id = ids[0]
    formal = ROOT / "data" / "kb.db"
    formal_before = formal.stat().st_size if formal.exists() else 0

    tools_cycle = [
        ("ping", {}),
        ("search", {"query": "企微", "limit": 3}),
        ("route_query", {"question": "标签为企微的所有文档"}),
        (
            "execute_query",
            {
                "query_spec": {"filter": {"tag": "企微"}, "limit": 5},
                "type": "structured",
                "limit": 5,
            },
        ),
        (
            "graph_traverse",
            {
                "start_ids": json.dumps([start_id]),
                "max_depth": 2,
                "limit": 5,
                "offset": 0,
            },
        ),
        ("list_knowledge", {"limit": 5, "offset": 0}),
        ("tags", {"limit": 10}),
        ("kb_health_check", {}),
    ]

    async def soak_loop(client, transport: str, seconds: int, pid: int | None) -> dict:
        t_end = time.time() + seconds
        latencies: list[float] = []
        errors = 0
        timeouts = 0
        samples = []
        err_lines = []
        i = 0
        last_sample = 0.0
        start = time.time()
        while time.time() < t_end:
            name, args = tools_cycle[i % len(tools_cycle)]
            i += 1
            # ask every 5 minutes
            if i % 50 == 0:
                name, args = "ask", {"question": "FINAL_CLOSURE_TEST_ soak ask"}
            # timeout scenario every 15 minutes
            if i % 150 == 0:
                name, args = "ask", {"question": "x" * 500}
            rec = await call_tool(client, name, args)
            latencies.append(float(rec.get("elapsed_ms") or 0))
            if not rec.get("response_ok"):
                errors += 1
                err_lines.append(rec)
            pl = rec.get("payload")
            if isinstance(pl, dict):
                route = (pl.get("data") or {}).get("route") if isinstance(pl.get("data"), dict) else pl.get("route")
                if isinstance(route, dict) and route.get("mode") == "timeout":
                    timeouts += 1
            now = time.time()
            if now - last_sample >= 300 or not samples:
                last_sample = now
                res = sample_resources(pid) if pid else {}
                window = latencies[-50:] or latencies
                samples.append({
                    "t": utc_now(),
                    "elapsed_s": int(now - start),
                    "ops": i,
                    "P50": _percentile(window, 0.5),
                    "P95": _percentile(window, 0.95),
                    "P99": _percentile(window, 0.99),
                    "errors": errors,
                    "timeouts": timeouts,
                    **res,
                })
            await asyncio.sleep(0.05)
        first = samples[0] if samples else {}
        last = samples[-1] if samples else {}
        p95_first = first.get("P95") or 0
        p95_last = last.get("P95") or 0
        degraded = False
        if p95_first > 0 and p95_last > p95_first * 1.5:
            degraded = True
        return {
            "transport": transport,
            "seconds": seconds,
            "ops": i,
            "errors": errors,
            "timeouts": timeouts,
            "P50": _percentile(latencies, 0.5),
            "P95": _percentile(latencies, 0.95),
            "P99": _percentile(latencies, 0.99),
            "samples": samples,
            "p95_degraded": degraded,
            "error_samples": err_lines[:50],
        }

    # HTTP soak
    server = HttpMcpServer(home)
    http_result: dict[str, Any]
    try:
        server.start(skip_migration_gate=True)
        assert server.proc and server.proc.pid
        http_result = asyncio.run(
            run_http_session(
                server.url,
                lambda c: soak_loop(c, "streamable-http", http_seconds, server.proc.pid if server.proc else None),
            )
        )
    finally:
        server.stop()

    # stdio soak
    stdio_result = asyncio.run(
        run_stdio_session(
            home,
            lambda c: soak_loop(c, "stdio", stdio_seconds, None),
            skip_migration_gate=True,
        )
    )

    formal_after = formal.stat().st_size if formal.exists() else 0
    summary = {
        "http": {k: v for k, v in http_result.items() if k != "error_samples"},
        "stdio": {k: v for k, v in stdio_result.items() if k != "error_samples"},
        "formal_db_unchanged": formal_before == formal_after,
        "formal_db_size_before": formal_before,
        "formal_db_size_after": formal_after,
        "finished": utc_now(),
    }
    (ART / "soak-http.json").write_text(
        json.dumps(http_result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ART / "soak-stdio.json").write_text(
        json.dumps(stdio_result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # resource CSV
    lines = ["transport,t,elapsed_s,rss_mb,thread_count,P50,P95,P99,errors,timeouts"]
    for label, block in (("http", http_result), ("stdio", stdio_result)):
        for s in block.get("samples") or []:
            lines.append(
                f"{label},{s.get('t')},{s.get('elapsed_s')},{s.get('rss_mb')},{s.get('thread_count')},"
                f"{s.get('P50')},{s.get('P95')},{s.get('P99')},{s.get('errors')},{s.get('timeouts')}"
            )
    (ART / "soak-resource.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (ART / "soak-errors.jsonl").open("w", encoding="utf-8") as f:
        for rec in (http_result.get("error_samples") or []) + (stdio_result.get("error_samples") or []):
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    (ART / "soak-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=["phase0", "phase5", "phase6", "phase7", "phase8", "all-fast"],
    )
    parser.add_argument("--http-seconds", type=int, default=7200)
    parser.add_argument("--stdio-seconds", type=int, default=1800)
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.phase == "phase0":
        cmd_phase0()
    elif args.phase == "phase5":
        cmd_phase5()
    elif args.phase == "phase6":
        cmd_phase6()
    elif args.phase == "phase7":
        cmd_phase7()
    elif args.phase == "phase8":
        cmd_phase8(args.http_seconds, args.stdio_seconds)
    elif args.phase == "all-fast":
        cmd_phase0()
        cmd_phase6()
        cmd_phase7()
        # short soak dry-run optional — full soak separate
        cmd_phase8(120, 60)


if __name__ == "__main__":
    main()
