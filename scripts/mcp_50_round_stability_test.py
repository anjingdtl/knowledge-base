"""ShineHeKnowledge MCP 50+ 轮全工具稳定性与召回测试

运行方式:
    cd f:\\ClaudeWorkSpace\\projects\\knowledge-base
    python scripts\\mcp_50_round_stability_test.py

测试目标:
    1. 通过 streamable-http MCP 接口调用全部工具(含 core/extended/admin/full/experimental 及 legacy 别名),
       验证服务在 Agent 高频调用下的稳定性。
    2. 验证关键词召回、结构化查询、图谱追溯的准确性。
    3. 输出包含成功率、延迟、召回指标的完整测试报告。

说明:
    原始需求为 50 轮;为覆盖全部 52 个正名工具与 30+ 个命名空间别名,
    实际执行 70 轮(部分轮次包含多个关联工具调用)。
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 测试数据中的唯一关键词,用于召回准确性验证
KEYWORD_ALPHA = "MCP稳定性测试关键词ALPHA"
KEYWORD_BETA = "MCP召回测试关键词BETA"
KEYWORD_GAMMA = "MCP图遍历测试关键词GAMMA"
UNIQUE_TAG = "mcp-50round-tag"

# 全局状态
state: dict[str, Any] = {
    "item_ids": {},
    "job_ids": {},
    "operation_ids": {},
    "wiki_page_id": None,
    "async_job_id": None,
    "ingest_job_id": None,
    "rounds": [],
}


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 30) -> None:
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


def _write_config(tmp_dir: Path) -> Path:
    config = {
        "storage": {"data_dir": str(tmp_dir / "data"), "db_name": "kb.db", "graph_dir": "graph"},
        "wiki": {"enabled": True, "auto_compile": False, "auto_publish": False, "query_save_min_length": 50},
        "rag": {
            "search_mode": "keywords",
            "enable_query_rewriting": False,
            "enable_rerank": False,
            "top_k": 5,
            "chunk_size": 500,
            "chunk_overlap": 50,
        },
        "embedding": {"api_key": "invalid-test-key", "base_url": "http://127.0.0.1", "model": "test", "provider": "test"},
        "llm": {"api_key": "invalid-test-key", "base_url": "http://127.0.0.1", "model": "test", "provider": "test", "max_tokens": 256},
        "reranker": {"enabled": False},
        "mcp": {
            "tool_profile": "full",
            "experimental_tools_enabled": True,
            "enable_legacy_aliases": True,
            "allow_http_write": True,
            "write_policy": "",
            "default_page_size": 20,
            "max_payload_bytes": 1_000_000,
        },
        "security": {"allowed_ingest_dirs": [str(tmp_dir)]},
    }
    config_path = tmp_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


def _create_test_file(tmp_dir: Path) -> Path:
    file_path = tmp_dir / "sample_doc.md"
    file_path.write_text(
        f"# 示例文档\n\n这是用于 MCP 导入测试的 Markdown 文件。\n"
        f"包含唯一关键词: {KEYWORD_BETA}。\n\n"
        "## 小节\n\n- 项目 A\n- 项目 B\n- 项目 C\n",
        encoding="utf-8",
    )
    return file_path


def _create_test_directory(tmp_dir: Path) -> Path:
    dir_path = tmp_dir / "docs"
    dir_path.mkdir()
    (dir_path / "a.md").write_text(f"# A\n\n{KEYWORD_GAMMA} 在目录 A 中。\n", encoding="utf-8")
    (dir_path / "b.txt").write_text(f"这是 B 文件,包含 {KEYWORD_ALPHA}。\n", encoding="utf-8")
    return dir_path


def _check_envelope(result) -> dict:
    """解析 FastMCP Client 返回的 CallToolResult,提取 envelope dict。"""
    data = getattr(result, "data", None)
    if data is None:
        # 某些版本直接返回 dict
        if isinstance(result, dict):
            return result
        raise ValueError(f"unexpected result type: {type(result)}")
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        return json.loads(data)
    if isinstance(data, list) and data:
        text = getattr(data[0], "text", None)
        if text:
            return json.loads(text)
        if isinstance(data[0], dict):
            return data[0]
        if isinstance(data[0], str):
            return json.loads(data[0])
    raise ValueError(f"unexpected data shape: {data!r}")


async def _call(client, tool_name: str, params: dict) -> dict:
    start = time.perf_counter()
    result = await client.call_tool(tool_name, params)
    elapsed = time.perf_counter() - start
    envelope = _check_envelope(result)
    return {"envelope": envelope, "latency_ms": round(elapsed * 1000, 2)}


def _record_round(name: str, tools: list[str], ok: bool, latency_ms: float, detail: str, metrics: dict | None = None):
    state["rounds"].append({
        "round": len(state["rounds"]) + 1,
        "name": name,
        "tools": tools,
        "ok": ok,
        "latency_ms": latency_ms,
        "detail": detail,
        "metrics": metrics or {},
    })


def _envelope_ok(env: dict) -> bool:
    return bool(env.get("ok"))


async def run_tests(client) -> None:
    """执行 50 轮 MCP 测试。"""

    # ===== 预置数据 =====
    # R1: create item A
    r1 = await _call(client, "create", {
        "title": "MCP 测试条目 A",
        "content": f"这是条目 A 的内容,关键词为 {KEYWORD_ALPHA}。",
        "tags": [UNIQUE_TAG, "alpha"],
        "file_type": "txt",
        "source_type": "manual",
    })
    env = r1["envelope"]
    state["item_ids"]["A"] = env["data"]["id"] if _envelope_ok(env) else None
    _record_round("create 预置条目 A", ["create"], _envelope_ok(env), r1["latency_ms"], json.dumps(env.get("error") or {}))

    # R2: create item B
    r2 = await _call(client, "create", {
        "title": "MCP 测试条目 B",
        "content": f"这是条目 B 的内容,关键词为 {KEYWORD_BETA}。",
        "tags": [UNIQUE_TAG, "beta"],
        "file_type": "md",
        "source_type": "manual",
    })
    env = r2["envelope"]
    state["item_ids"]["B"] = env["data"]["id"] if _envelope_ok(env) else None
    _record_round("create 预置条目 B", ["create"], _envelope_ok(env), r2["latency_ms"], json.dumps(env.get("error") or {}))

    # R3: create item C
    r3 = await _call(client, "create", {
        "title": "MCP 测试条目 C",
        "content": f"这是条目 C 的内容,关键词为 {KEYWORD_GAMMA}, 同时关联 {KEYWORD_ALPHA}。",
        "tags": [UNIQUE_TAG, "gamma"],
        "file_type": "txt",
        "source_type": "manual",
    })
    env = r3["envelope"]
    state["item_ids"]["C"] = env["data"]["id"] if _envelope_ok(env) else None
    _record_round("create 预置条目 C", ["create"], _envelope_ok(env), r3["latency_ms"], json.dumps(env.get("error") or {}))

    # ===== 连通性与能力 =====
    # R4: ping
    r4 = await _call(client, "ping", {})
    env = r4["envelope"]
    ok4 = _envelope_ok(env) and env.get("data", {}).get("status") == "alive"
    _record_round("ping 连通性检测", ["ping"], ok4, r4["latency_ms"], json.dumps(env.get("error") or {}))

    # R5: kb_capabilities
    r5 = await _call(client, "kb_capabilities", {})
    env = r5["envelope"]
    tools = env.get("data", {}).get("tools", [])
    ok5 = _envelope_ok(env) and len(tools) > 40
    _record_round("kb_capabilities 能力清单", ["kb_capabilities"], ok5, r5["latency_ms"],
                  f"visible_tools={len(tools)}")

    # ===== 召回准确性: search / search_fulltext =====
    # R6: search ALPHA
    r6 = await _call(client, "search", {"query": KEYWORD_ALPHA, "top_k": 5})
    env = r6["envelope"]
    results = env.get("data", []) if _envelope_ok(env) else []
    kid_a = state["item_ids"].get("A")
    found_a = any(item.get("knowledge_id") == kid_a for item in results)
    _record_round("search 语义搜索召回 ALPHA", ["search"], found_a, r6["latency_ms"],
                  f"found={found_a}, results={len(results)}",
                  {"recall": 1.0 if found_a else 0.0, "precision": 1.0 / max(len(results), 1), "results_count": len(results)})

    # R7: search_fulltext BETA
    r7 = await _call(client, "search_fulltext", {"query": KEYWORD_BETA, "limit": 10})
    env = r7["envelope"]
    results = env.get("data", []) if _envelope_ok(env) else []
    kid_b = state["item_ids"].get("B")
    found_b = any(item.get("knowledge_id") == kid_b for item in results)
    _record_round("search_fulltext 全文搜索召回 BETA", ["search_fulltext"], found_b, r7["latency_ms"],
                  f"found={found_b}, results={len(results)}",
                  {"recall": 1.0 if found_b else 0.0, "precision": 1.0 / max(len(results), 1), "results_count": len(results)})

    # R8: search GAMMA (目录导入后也会命中)
    r8 = await _call(client, "search", {"query": KEYWORD_GAMMA, "top_k": 5})
    env = r8["envelope"]
    results = env.get("data", []) if _envelope_ok(env) else []
    kid_c = state["item_ids"].get("C")
    found_c = any(item.get("knowledge_id") == kid_c for item in results)
    _record_round("search 语义搜索召回 GAMMA", ["search"], found_c, r8["latency_ms"],
                  f"found={found_c}, results={len(results)}",
                  {"recall": 1.0 if found_c else 0.0, "precision": 1.0 / max(len(results), 1), "results_count": len(results)})

    # ===== 读取与列表 =====
    # R9: read item A
    r9 = await _call(client, "read", {"item_id": state["item_ids"]["A"], "include_blocks": True})
    env = r9["envelope"]
    ok9 = _envelope_ok(env) and env.get("data", {}).get("id") == state["item_ids"]["A"]
    _record_round("read 读取条目 A", ["read"], ok9, r9["latency_ms"], json.dumps(env.get("error") or {}))

    # R10: list_knowledge
    r10 = await _call(client, "list_knowledge", {"tag": UNIQUE_TAG, "limit": 10})
    env = r10["envelope"]
    items = env.get("data", []) if _envelope_ok(env) else []
    ok10 = _envelope_ok(env) and len(items) >= 3
    _record_round("list_knowledge 按标签列出", ["list_knowledge"], ok10, r10["latency_ms"],
                  f"items={len(items)}")

    # R11: tags
    r11 = await _call(client, "tags", {})
    env = r11["envelope"]
    tags = env.get("data", []) if _envelope_ok(env) else []
    ok11 = _envelope_ok(env) and UNIQUE_TAG in tags
    _record_round("tags 标签列表", ["tags"], ok11, r11["latency_ms"], f"tag_count={len(tags)}")

    # ===== CRUD 生命周期 =====
    # R12: create item D
    r12 = await _call(client, "create", {
        "title": "MCP 测试条目 D",
        "content": "这是条目 D 的初始内容。",
        "tags": [UNIQUE_TAG, "delta"],
    })
    env = r12["envelope"]
    state["item_ids"]["D"] = env["data"]["id"] if _envelope_ok(env) else None
    _record_round("create 创建条目 D", ["create"], _envelope_ok(env), r12["latency_ms"], json.dumps(env.get("error") or {}))

    # R13: read item D
    r13 = await _call(client, "read", {"item_id": state["item_ids"]["D"]})
    env = r13["envelope"]
    ok13 = _envelope_ok(env) and env.get("data", {}).get("id") == state["item_ids"]["D"]
    _record_round("read 读取条目 D", ["read"], ok13, r13["latency_ms"], json.dumps(env.get("error") or {}))

    # R14: update item D
    r14 = await _call(client, "update", {
        "item_id": state["item_ids"]["D"],
        "content": f"这是条目 D 更新后的内容,加入关键词 {KEYWORD_BETA}。",
    })
    env = r14["envelope"]
    ok14 = _envelope_ok(env) and "content" in env.get("data", {}).get("updated_fields", [])
    _record_round("update 更新条目 D", ["update"], ok14, r14["latency_ms"], json.dumps(env.get("error") or {}))

    # R15: search after update (D should be found)
    r15 = await _call(client, "search", {"query": KEYWORD_BETA, "top_k": 10})
    env = r15["envelope"]
    results = env.get("data", []) if _envelope_ok(env) else []
    kid_d = state["item_ids"].get("D")
    found_d = any(item.get("knowledge_id") == kid_d for item in results)
    _record_round("search 更新后召回 D", ["search"], found_d, r15["latency_ms"],
                  f"found={found_d}, results={len(results)}",
                  {"recall": 1.0 if found_d else 0.0, "results_count": len(results)})

    # R16: delete item D
    r16 = await _call(client, "delete", {"item_id": state["item_ids"]["D"]})
    env = r16["envelope"]
    ok16 = _envelope_ok(env) and env.get("data", {}).get("id") == state["item_ids"]["D"]
    _record_round("delete 软删除条目 D", ["delete"], ok16, r16["latency_ms"], json.dumps(env.get("error") or {}))

    # R17: restore_knowledge item D
    r17 = await _call(client, "restore_knowledge", {"item_id": state["item_ids"]["D"]})
    env = r17["envelope"]
    ok17 = _envelope_ok(env) and env.get("data", {}).get("id") == state["item_ids"]["D"]
    _record_round("restore_knowledge 恢复条目 D", ["restore_knowledge"], ok17, r17["latency_ms"], json.dumps(env.get("error") or {}))

    # ===== 预览与写策略 =====
    # R18: preview_operation create
    r18 = await _call(client, "preview_operation", {
        "operation": "create",
        "title": "预览创建",
        "content": "预览内容",
        "tags": [UNIQUE_TAG],
    })
    env = r18["envelope"]
    ok18 = _envelope_ok(env) and env.get("dry_run") is True
    _record_round("preview_operation 预览创建", ["preview_operation"], ok18, r18["latency_ms"], json.dumps(env.get("error") or {}))

    # R19: preview_operation update
    r19 = await _call(client, "preview_operation", {
        "operation": "update",
        "item_id": state["item_ids"]["A"],
        "title": "预览更新标题",
    })
    env = r19["envelope"]
    ok19 = _envelope_ok(env) and env.get("dry_run") is True
    _record_round("preview_operation 预览更新", ["preview_operation"], ok19, r19["latency_ms"], json.dumps(env.get("error") or {}))

    # R20: preview_operation delete
    r20 = await _call(client, "preview_operation", {
        "operation": "delete",
        "item_id": state["item_ids"]["A"],
    })
    env = r20["envelope"]
    ok20 = _envelope_ok(env) and env.get("dry_run") is True
    _record_round("preview_operation 预览删除", ["preview_operation"], ok20, r20["latency_ms"], json.dumps(env.get("error") or {}))

    # ===== 索引与导入 =====
    # R21: reindex_all dry_run
    r21 = await _call(client, "reindex_all", {"dry_run": True})
    env = r21["envelope"]
    ok21 = _envelope_ok(env) and env.get("dry_run") is True
    _record_round("reindex_all 预览重建索引", ["reindex_all"], ok21, r21["latency_ms"], json.dumps(env.get("error") or {}))

    # R22: reindex_all (actual, lightweight because data is small)
    r22 = await _call(client, "reindex_all", {})
    env = r22["envelope"]
    _record_round("reindex_all 实际重建索引", ["reindex_all"], _envelope_ok(env), r22["latency_ms"], json.dumps(env.get("error") or {}))

    # R23: index_path
    r23 = await _call(client, "index_path", {"path": str(state["test_dir"]), "recursive": True})
    env = r23["envelope"]
    _record_round("index_path 索引目录", ["index_path"], _envelope_ok(env), r23["latency_ms"], json.dumps(env.get("error") or {}))

    # R24: ingest_file
    r24 = await _call(client, "ingest_file", {"file_path": str(state["test_file"]), "tags": [UNIQUE_TAG]})
    env = r24["envelope"]
    _record_round("ingest_file 导入文件", ["ingest_file"], _envelope_ok(env), r24["latency_ms"], json.dumps(env.get("error") or {}))

    # R25: ingest_url dry_run
    r25 = await _call(client, "ingest_url", {"url": "https://example.com/mcp-test", "tags": ["web"], "dry_run": True})
    env = r25["envelope"]
    ok25 = _envelope_ok(env) and env.get("dry_run") is True
    _record_round("ingest_url 预览导入网页", ["ingest_url"], ok25, r25["latency_ms"], json.dumps(env.get("error") or {}))

    # R26: create_ingest_job
    r26 = await _call(client, "create_ingest_job", {"file_path": str(state["test_file"]), "tags": [UNIQUE_TAG]})
    env = r26["envelope"]
    if _envelope_ok(env):
        state["ingest_job_id"] = env["data"].get("job_id")
    _record_round("create_ingest_job 创建导入任务", ["create_ingest_job"], _envelope_ok(env), r26["latency_ms"], json.dumps(env.get("error") or {}))

    # R27: get_job
    r27 = await _call(client, "get_job", {"job_id": state.get("ingest_job_id") or "invalid-job-id"})
    env = r27["envelope"]
    _record_round("get_job 查询导入任务", ["get_job"], _envelope_ok(env), r27["latency_ms"], json.dumps(env.get("error") or {}))

    # R28: list_jobs
    r28 = await _call(client, "list_jobs", {"limit": 10})
    env = r28["envelope"]
    _record_round("list_jobs 列出任务", ["list_jobs"], _envelope_ok(env), r28["latency_ms"], json.dumps(env.get("error") or {}))

    # R29: cancel_job
    r29 = await _call(client, "cancel_job", {"job_id": state.get("ingest_job_id") or "invalid-job-id"})
    env = r29["envelope"]
    _record_round("cancel_job 取消导入任务", ["cancel_job"], _envelope_ok(env), r29["latency_ms"], json.dumps(env.get("error") or {}))

    # ===== 异步任务 ops =====
    # R30: create_async_job
    r30 = await _call(client, "create_async_job", {"job_type": "test_job", "params": {"foo": "bar"}, "priority": 1})
    env = r30["envelope"]
    if _envelope_ok(env):
        state["async_job_id"] = env["data"].get("job_id")
    _record_round("create_async_job 创建异步任务", ["create_async_job"], _envelope_ok(env), r30["latency_ms"], json.dumps(env.get("error") or {}))

    # R31: get_async_job
    r31 = await _call(client, "get_async_job", {"job_id": state.get("async_job_id") or "invalid-job-id"})
    env = r31["envelope"]
    _record_round("get_async_job 查询异步任务", ["get_async_job"], _envelope_ok(env), r31["latency_ms"], json.dumps(env.get("error") or {}))

    # R32: list_async_jobs
    r32 = await _call(client, "list_async_jobs", {"limit": 10})
    env = r32["envelope"]
    _record_round("list_async_jobs 列出异步任务", ["list_async_jobs"], _envelope_ok(env), r32["latency_ms"], json.dumps(env.get("error") or {}))

    # R33: cancel_async_job
    r33 = await _call(client, "cancel_async_job", {"job_id": state.get("async_job_id") or "invalid-job-id"})
    env = r33["envelope"]
    _record_round("cancel_async_job 取消异步任务", ["cancel_async_job"], _envelope_ok(env), r33["latency_ms"], json.dumps(env.get("error") or {}))

    # ===== 操作审计 =====
    # R34: query_operation_logs
    r34 = await _call(client, "query_operation_logs", {"target_type": "knowledge", "limit": 10})
    env = r34["envelope"]
    _record_round("query_operation_logs 查询操作日志", ["query_operation_logs"], _envelope_ok(env), r34["latency_ms"], json.dumps(env.get("error") or {}))

    # R35: list_recent_operations
    r35 = await _call(client, "list_recent_operations", {"limit": 10})
    env = r35["envelope"]
    _record_round("list_recent_operations 最近操作", ["list_recent_operations"], _envelope_ok(env), r35["latency_ms"], json.dumps(env.get("error") or {}))

    # R36: get_operation_log (use first recent log id)
    r35_data = env.get("data", []) if _envelope_ok(env) else []
    log_id = r35_data[0].get("id") if r35_data else None
    state["operation_ids"]["recent"] = log_id
    r36 = await _call(client, "get_operation_log", {"operation_id": log_id or "invalid"})
    env = r36["envelope"]
    _record_round("get_operation_log 单条日志", ["get_operation_log"], _envelope_ok(env), r36["latency_ms"], json.dumps(env.get("error") or {}))

    # R37: undo_operation (undo the update on item A to test revert)
    r37 = await _call(client, "undo_operation", {"operation_id": log_id or "invalid", "operator": "mcp-tester"})
    env = r37["envelope"]
    _record_round("undo_operation 撤销操作", ["undo_operation"], _envelope_ok(env), r37["latency_ms"], json.dumps(env.get("error") or {}))

    # ===== Query DSL 与路由 =====
    # R38: route_query
    r38 = await _call(client, "route_query", {"question": f"查找 {KEYWORD_ALPHA}"})
    env = r38["envelope"]
    _record_round("route_query 路由分析", ["route_query"], _envelope_ok(env), r38["latency_ms"], json.dumps(env.get("error") or {}))

    # R39: structured_query natural language
    r39 = await _call(client, "structured_query", {"query": KEYWORD_BETA, "limit": 10})
    env = r39["envelope"]
    results = env.get("data", []) if _envelope_ok(env) else []
    _record_round("structured_query 自然语言查询", ["structured_query"], _envelope_ok(env), r39["latency_ms"],
                  f"results={len(results)}")

    # R40: structured_query DSL
    dsl = {
        "filter": {"tag": UNIQUE_TAG},
        "limit": 20,
        "sort": {"by": "updated_at", "order": "desc"},
    }
    r40 = await _call(client, "structured_query", {"query_dsl": dsl})
    env = r40["envelope"]
    results = env.get("data", []) if _envelope_ok(env) else []
    ok40 = _envelope_ok(env) and len(results) >= 3
    _record_round("structured_query DSL 结构化查询", ["structured_query"], ok40, r40["latency_ms"],
                  f"results={len(results)}", {"results_count": len(results)})

    # R41: explain_query
    r41 = await _call(client, "explain_query", {"query_dsl": dsl})
    env = r41["envelope"]
    _record_round("explain_query 查询解释", ["explain_query"], _envelope_ok(env), r41["latency_ms"], json.dumps(env.get("error") or {}))

    # R42: execute_query structured
    r42 = await _call(client, "execute_query", {"query_spec": dsl, "type": "structured", "limit": 20})
    env = r42["envelope"]
    results = env.get("data", []) if _envelope_ok(env) else []
    ok42 = _envelope_ok(env) and len(results) >= 3
    _record_round("execute_query 执行结构化查询", ["execute_query"], ok42, r42["latency_ms"],
                  f"results={len(results)}", {"results_count": len(results)})

    # R43: execute_query graph
    graph_spec = {
        "start_ids": [state["item_ids"]["A"]],
        "start_type": "knowledge",
        "max_depth": 2,
    }
    r43 = await _call(client, "execute_query", {"query_spec": graph_spec, "type": "graph", "limit": 20})
    env = r43["envelope"]
    _record_round("execute_query 执行图查询", ["execute_query"], _envelope_ok(env), r43["latency_ms"], json.dumps(env.get("error") or {}))

    # R44: ask_with_query
    r44 = await _call(client, "ask_with_query", {
        "question": f"{KEYWORD_ALPHA} 在哪里?",
        "query_spec": {"filter": {"fulltext": KEYWORD_ALPHA}, "limit": 5},
        "top_k": 5,
    })
    env = r44["envelope"]
    _record_round("ask_with_query 显式 QuerySpec 问答", ["ask_with_query"], _envelope_ok(env), r44["latency_ms"], json.dumps(env.get("error") or {}))

    # R45: ask
    r45 = await _call(client, "ask", {"question": f"请解释 {KEYWORD_ALPHA}"})
    env = r45["envelope"]
    _record_round("ask RAG 问答", ["ask"], _envelope_ok(env), r45["latency_ms"], json.dumps(env.get("error") or {}))

    # R46: get_source_graph
    r46 = await _call(client, "get_source_graph", {
        "knowledge_ids": [state["item_ids"]["A"], state["item_ids"]["B"]],
        "max_nodes": 50,
    })
    env = r46["envelope"]
    _record_round("get_source_graph 来源图谱", ["get_source_graph"], _envelope_ok(env), r46["latency_ms"], json.dumps(env.get("error") or {}))

    # R47: graph_traverse
    r47 = await _call(client, "graph_traverse", {
        "start_ids": json.dumps([state["item_ids"]["A"]]),
        "start_type": "knowledge",
        "max_depth": 2,
        "limit": 50,
    })
    env = r47["envelope"]
    _record_round("graph_traverse 图谱遍历", ["graph_traverse"], _envelope_ok(env), r47["latency_ms"], json.dumps(env.get("error") or {}))

    # ===== Wiki 工具 =====
    # R48: save_to_wiki (LLM 会失败,验证返回 envelope)
    r48 = await _call(client, "save_to_wiki", {
        "question": "MCP 测试问题",
        "answer": "这是一个足够长的答案用于测试 Wiki 保存功能。" * 3,
        "source_ids": [state["item_ids"]["A"]],
    })
    env = r48["envelope"]
    _record_round("save_to_wiki 保存问答到 Wiki", ["save_to_wiki"], _envelope_ok(env), r48["latency_ms"], json.dumps(env.get("error") or {}))

    # R49: wiki_lint
    r49 = await _call(client, "wiki_lint", {})
    env = r49["envelope"]
    _record_round("wiki_lint Wiki 体检", ["wiki_lint"], _envelope_ok(env), r49["latency_ms"], json.dumps(env.get("error") or {}))

    # R50: fix_dead_references dry_run
    r50 = await _call(client, "fix_dead_references", {"max_pages": 10, "dry_run": True})
    env = r50["envelope"]
    _record_round("fix_dead_references 预览修复死链", ["fix_dead_references"], _envelope_ok(env), r50["latency_ms"], json.dumps(env.get("error") or {}))

    # ===== Wiki workflow (需要真实 page_id,从 lint 结果里找一个) =====
    # 先调用 wiki_lint 拿到现有页面
    r51 = await _call(client, "wiki_lint", {})
    env = r51["envelope"]
    page_id = None
    if _envelope_ok(env):
        pages = env.get("data", {}).get("pages", [])
        if pages:
            page_id = pages[0].get("id")
    state["wiki_page_id"] = page_id
    _record_round("wiki_lint 获取 Wiki 页面列表", ["wiki_lint"], _envelope_ok(env), r51["latency_ms"],
                  f"page_id={page_id}")

    # R52: wiki_workflow_history
    r52 = await _call(client, "wiki_workflow_history", {"page_id": page_id or "invalid"})
    env = r52["envelope"]
    _record_round("wiki_workflow_history 工作流历史", ["wiki_workflow_history"], _envelope_ok(env), r52["latency_ms"], json.dumps(env.get("error") or {}))

    # R53: wiki_list_versions
    r53 = await _call(client, "wiki_list_versions", {"page_id": page_id or "invalid"})
    env = r53["envelope"]
    _record_round("wiki_list_versions 版本列表", ["wiki_list_versions"], _envelope_ok(env), r53["latency_ms"], json.dumps(env.get("error") or {}))

    # R54: wiki workflow transitions (submit/approve/reject/deprecate)
    # 这里使用一个不存在的 page_id 测试错误 envelope,避免依赖真实 Wiki 页面状态机
    r54 = await _call(client, "wiki_submit_review", {"page_id": "nonexistent-page", "operator": "tester"})
    env = r54["envelope"]
    _record_round("wiki_submit_review 错误状态流转", ["wiki_submit_review"], _envelope_ok(env), r54["latency_ms"], json.dumps(env.get("error") or {}))

    r55 = await _call(client, "wiki_approve", {"page_id": "nonexistent-page", "operator": "tester"})
    env = r55["envelope"]
    _record_round("wiki_approve 错误状态流转", ["wiki_approve"], _envelope_ok(env), r55["latency_ms"], json.dumps(env.get("error") or {}))

    r56 = await _call(client, "wiki_reject", {"page_id": "nonexistent-page", "operator": "tester"})
    env = r56["envelope"]
    _record_round("wiki_reject 错误状态流转", ["wiki_reject"], _envelope_ok(env), r56["latency_ms"], json.dumps(env.get("error") or {}))

    r57 = await _call(client, "wiki_deprecate", {"page_id": "nonexistent-page", "operator": "tester"})
    env = r57["envelope"]
    _record_round("wiki_deprecate 错误状态流转", ["wiki_deprecate"], _envelope_ok(env), r57["latency_ms"], json.dumps(env.get("error") or {}))

    r58 = await _call(client, "wiki_restore_version", {"page_id": "nonexistent-page", "version": 1})
    env = r58["envelope"]
    _record_round("wiki_restore_version 错误版本恢复", ["wiki_restore_version"], _envelope_ok(env), r58["latency_ms"], json.dumps(env.get("error") or {}))

    # ===== Agent Memory 工具 =====
    # R59: remember_fact
    r59 = await _call(client, "remember_fact", {
        "key": "mcp_test_decision",
        "value": "在 MCP 50 轮测试中决定使用 keywords 模式避免外部 embedding 调用。",
        "category": "decision",
    })
    env = r59["envelope"]
    _record_round("remember_fact 记住决策", ["remember_fact"], _envelope_ok(env), r59["latency_ms"], json.dumps(env.get("error") or {}))

    # R60: recall_facts (使用存储内容中确定存在的子串,避免中文分词差异)
    r60 = await _call(client, "recall_facts", {"query": "keywords", "limit": 5})
    env = r60["envelope"]
    results = env.get("data", []) if _envelope_ok(env) else []
    ok60 = _envelope_ok(env) and len(results) >= 1
    _record_round("recall_facts 回忆事实", ["recall_facts"], ok60, r60["latency_ms"], f"results={len(results)}")

    # R61: update_project_context
    r61 = await _call(client, "update_project_context", {"summary": "MCP 50 轮测试项目上下文"})
    env = r61["envelope"]
    _record_round("update_project_context 更新项目上下文", ["update_project_context"], _envelope_ok(env), r61["latency_ms"], json.dumps(env.get("error") or {}))

    # R62: search_decisions
    r62 = await _call(client, "search_decisions", {"query": "keywords", "limit": 5})
    env = r62["envelope"]
    _record_round("search_decisions 搜索决策", ["search_decisions"], _envelope_ok(env), r62["latency_ms"], json.dumps(env.get("error") or {}))

    # R63: summarize_recent_changes
    r63 = await _call(client, "summarize_recent_changes", {"since_hours": 1})
    env = r63["envelope"]
    _record_round("summarize_recent_changes 变更总结", ["summarize_recent_changes"], _envelope_ok(env), r63["latency_ms"], json.dumps(env.get("error") or {}))

    # R64: extract_tasks_from_doc
    r64 = await _call(client, "extract_tasks_from_doc", {"content": "TODO: 完成 MCP 测试报告; DONE: 运行 50 轮测试。"})
    env = r64["envelope"]
    _record_round("extract_tasks_from_doc 提取任务", ["extract_tasks_from_doc"], _envelope_ok(env), r64["latency_ms"], json.dumps(env.get("error") or {}))

    # ===== 别名测试 (legacy aliases) =====
    # R65: kb.search alias
    r65 = await _call(client, "kb.search", {"query": KEYWORD_ALPHA, "top_k": 5})
    env = r65["envelope"]
    _record_round("kb.search 命名空间别名", ["kb.search"], _envelope_ok(env), r65["latency_ms"], json.dumps(env.get("error") or {}))

    # R66: ops.ping alias
    r66 = await _call(client, "ops.ping", {})
    env = r66["envelope"]
    _record_round("ops.ping 命名空间别名", ["ops.ping"], _envelope_ok(env), r66["latency_ms"], json.dumps(env.get("error") or {}))

    # R67: wiki.lint alias
    r67 = await _call(client, "wiki.lint", {})
    env = r67["envelope"]
    _record_round("wiki.lint 命名空间别名", ["wiki.lint"], _envelope_ok(env), r67["latency_ms"], json.dumps(env.get("error") or {}))

    # R68: graph.traverse alias
    r68 = await _call(client, "graph.traverse", {
        "start_ids": json.dumps([state["item_ids"]["A"]]),
        "start_type": "knowledge",
        "max_depth": 1,
    })
    env = r68["envelope"]
    _record_round("graph.traverse 命名空间别名", ["graph.traverse"], _envelope_ok(env), r68["latency_ms"], json.dumps(env.get("error") or {}))

    # R69: memory.remember alias
    r69 = await _call(client, "memory.remember", {"key": "alias_test", "value": "别名测试", "category": "fact"})
    env = r69["envelope"]
    _record_round("memory.remember 命名空间别名", ["memory.remember"], _envelope_ok(env), r69["latency_ms"], json.dumps(env.get("error") or {}))

    # R70: memory.recall alias
    r70 = await _call(client, "memory.recall", {"query": "别名测试", "limit": 5})
    env = r70["envelope"]
    _record_round("memory.recall 命名空间别名", ["memory.recall"], _envelope_ok(env), r70["latency_ms"], json.dumps(env.get("error") or {}))


async def main():
    os.environ["PYTHONUNBUFFERED"] = "1"
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    tmp_dir = Path(tempfile.mkdtemp(prefix="shinehe-mcp-50round-"))
    print(f"[setup] tmp_dir={tmp_dir}", flush=True)
    config_path = _write_config(tmp_dir)
    state["test_file"] = _create_test_file(tmp_dir)
    state["test_dir"] = _create_test_directory(tmp_dir)

    port = _free_port()
    env = os.environ.copy()
    env["SHINEHE_HOME"] = str(tmp_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["MCP_TRANSPORT"] = "streamable-http"

    proc = subprocess.Popen(
        [
            sys.executable,
            str(PROJECT_ROOT / "run_mcp.py"),
            "-t",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "-p",
            str(port),
            "--config",
            str(config_path),
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=None,
        stderr=None,
    )

    try:
        _wait_for_port(port, proc, timeout=30)
        print(f"[server] started on port {port}", flush=True)

        # 端口打开后稍等片刻，让 StreamableHTTP 应用完成初始化
        time.sleep(2)
        print("[client] connecting", flush=True)

        from fastmcp import Client
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            print("[client] connected", flush=True)
            await run_tests(client)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        print("[server] stopped", flush=True)

    # 生成报告
    total = len(state["rounds"])
    passed = sum(1 for r in state["rounds"] if r["ok"])
    failed = total - passed
    avg_latency = sum(r["latency_ms"] for r in state["rounds"]) / max(total, 1)
    max_latency = max((r["latency_ms"] for r in state["rounds"]), default=0)

    recall_rounds = [r for r in state["rounds"] if "recall" in r.get("metrics", {})]
    avg_recall = sum(r["metrics"]["recall"] for r in recall_rounds) / max(len(recall_rounds), 1)
    avg_precision = sum(r["metrics"].get("precision", 0) for r in recall_rounds) / max(len(recall_rounds), 1)

    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / "mcp_50round_report.json"
    report = {
        "summary": {
            "total_rounds": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / max(total, 1), 4),
            "avg_latency_ms": round(avg_latency, 2),
            "max_latency_ms": round(max_latency, 2),
            "avg_recall": round(avg_recall, 4),
            "avg_precision": round(avg_precision, 4),
        },
        "rounds": state["rounds"],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 同时输出到 stdout
    print("\n" + "=" * 60)
    print("ShineHeKnowledge MCP 50 轮稳定性与召回测试报告")
    print("=" * 60)
    print(f"总轮次: {total}")
    print(f"通过: {passed}")
    print(f"失败: {failed}")
    print(f"成功率: {report['summary']['pass_rate']:.2%}")
    print(f"平均延迟: {avg_latency:.2f} ms")
    print(f"最大延迟: {max_latency:.2f} ms")
    print(f"平均召回率: {avg_recall:.2%}")
    print(f"平均精确率: {avg_precision:.2%}")
    print(f"详细报告: {report_path}")
    print("-" * 60)
    for r in state["rounds"]:
        status = "PASS" if r["ok"] else "FAIL"
        print(f"R{r['round']:03d} [{status}] {r['name']} ({r['latency_ms']:.2f} ms) {r['detail']}")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
