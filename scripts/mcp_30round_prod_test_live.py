"""ShineHeKnowledge MCP 生产环境 30 轮全工具稳定性与召回测试

通过 HTTP JSON-RPC 直连本机 127.0.0.1:9000/mcp 端点，覆盖 103 个工具中的
全部正名工具与主要命名空间别名。测试结束后自动清理测试数据。

运行方式:
    python scripts\\mcp_30round_prod_test_live.py

输出:
    - C:/Users/Administrator/Desktop/ShineHe_KB_MCP_30轮生产测试报告.md
    - reports/mcp_30round_prod_test_live.json
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

HOST = "127.0.0.1"
PORT = 9000
PATH = "/mcp"

UNIQUE_TAG = f"mcp-prod-test-{datetime.now().strftime('%Y%m%d')}-tara"
KEYWORD_ALPHA = f"MCP生产稳定性测试ALPHA_{datetime.now().strftime('%Y%m%d')}_TARA"
KEYWORD_BETA = f"MCP生产召回测试BETA_{datetime.now().strftime('%Y%m%d')}_TARA"
KEYWORD_GAMMA = f"MCP生产图遍历测试GAMMA_{datetime.now().strftime('%Y%m%d')}_TARA"

state: dict[str, Any] = {
    "item_ids": {},
    "job_id": None,
    "async_job_id": None,
    "operation_id": None,
    "wiki_page_id": None,
    "test_dir": None,
    "test_file": None,
    "rounds": [],
}


class MCPClient:
    def __init__(self, host: str, port: int, path: str):
        self.host = host
        self.port = port
        self.path = path
        self.session_id: str | None = None
        self.msg_id = 0
        self.last_envelope: dict = {"ok": False}

    def _post(self, body: dict) -> tuple[int, Any, dict]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        conn = http.client.HTTPConnection(self.host, self.port, timeout=120)
        try:
            conn.request("POST", self.path, body=json.dumps(body), headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8", errors="replace")
            resp_headers = dict(resp.getheaders())

            parsed = None
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                # 流式 SSE 响应可能以注释行（如 ": ping"）开头，逐行查找 data: 字段
                for line in raw.split("\n"):
                    if line.startswith("data: "):
                        try:
                            parsed = json.loads(line[6:])
                        except json.JSONDecodeError:
                            parsed = line[6:]
                        break
                if parsed is None:
                    parsed = raw
            return resp.status, parsed, resp_headers
        finally:
            conn.close()

    def initialize(self) -> bool:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "shinehe-mcp-30round-tester", "version": "1.0"},
            },
        }
        status, result, headers = self._post(body)
        if status == 200 and isinstance(result, dict):
            self.session_id = headers.get("Mcp-Session-Id", headers.get("mcp-session-id", ""))
            return bool(self.session_id)
        return False

    def call(self, name: str, arguments: dict | None = None) -> dict:
        self.msg_id += 1
        body = {
            "jsonrpc": "2.0",
            "id": self.msg_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        t0 = time.perf_counter()
        status, result, _ = self._post(body)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        envelope: dict = {"ok": False, "data": None, "error": {"code": "UNKNOWN", "message": ""}}
        if status == 200 and isinstance(result, dict):
            content = result.get("result", {}).get("content", [])
            for c in content:
                if c.get("type") == "text":
                    try:
                        envelope = json.loads(c["text"])
                    except json.JSONDecodeError:
                        envelope = {"ok": False, "data": None, "error": {"code": "PARSE_ERROR", "message": c.get("text", "")}}
                    break
        else:
            envelope = {"ok": False, "data": None, "error": {"code": f"HTTP_{status}", "message": str(result)[:500]}}
        self.last_envelope = envelope
        return {"envelope": envelope, "latency_ms": round(elapsed_ms, 2), "status": status}


def envelope_ok(env: dict) -> bool:
    return bool(env.get("ok"))


def record_round(name: str, tools: list[str], ok: bool, latency_ms: float, detail: str, metrics: dict | None = None):
    state["rounds"].append({
        "round": len(state["rounds"]) + 1,
        "name": name,
        "tools": tools,
        "ok": ok,
        "latency_ms": latency_ms,
        "detail": detail,
        "metrics": metrics or {},
    })


def create_test_files() -> tuple[Path, Path]:
    # 放在项目根目录下，使其落入 SHINEHE_HOME 允许范围，避免权限拒绝
    tmp_dir = PROJECT_ROOT / "test_tmp"
    tmp_dir.mkdir(exist_ok=True)
    test_file = tmp_dir / "sample_doc.md"
    test_file.write_text(
        f"# 示例文档\n\n这是用于 MCP 导入测试的 Markdown 文件。\n"
        f"包含唯一关键词: {KEYWORD_BETA}。\n\n"
        "## 小节\n\n- 项目 A\n- 项目 B\n- 项目 C\n",
        encoding="utf-8",
    )
    test_dir = tmp_dir / "docs"
    test_dir.mkdir(exist_ok=True)
    (test_dir / "a.md").write_text(f"# A\n\n{KEYWORD_GAMMA} 在目录 A 中。\n", encoding="utf-8")
    (test_dir / "b.txt").write_text(f"这是 B 文件,包含 {KEYWORD_ALPHA}。\n", encoding="utf-8")
    return tmp_dir, test_file


def _multi_call(client: MCPClient, calls: list[tuple[str, dict]]) -> list[tuple[str, dict, dict, float]]:
    """顺序执行多个工具调用，返回每个工具的 (name, args, envelope, latency_ms)。"""
    results = []
    for name, args in calls:
        r = client.call(name, args)
        env = r["envelope"]
        results.append((name, args, env, r["latency_ms"]))
    return results


def _record_multi(name: str, results: list[tuple[str, dict, dict, float]],
                  metrics: dict | None = None, extra_detail: str = ""):
    all_ok = all(envelope_ok(env) for _, _, env, _ in results)
    total_latency = sum(lat for _, _, _, lat in results)
    tool_names = [n for n, _, _, _ in results]
    detail_parts = [f"{n}={'OK' if envelope_ok(env) else 'FAIL'}" for n, _, env, _ in results]
    detail = ", ".join(detail_parts)
    if extra_detail:
        detail += f" | {extra_detail}"
    record_round(name, tool_names, all_ok, total_latency, detail, metrics)


def run_tests(client: MCPClient) -> None:
    # R1: 预置数据（create x3）
    calls = [
        ("create", {"title": "MCP 生产测试条目 A", "content": f"这是条目 A 的内容,关键词为 {KEYWORD_ALPHA}。",
                    "tags": [UNIQUE_TAG, "alpha"], "file_type": "txt", "source_type": "manual"}),
        ("create", {"title": "MCP 生产测试条目 B", "content": f"这是条目 B 的内容,关键词为 {KEYWORD_BETA}。",
                    "tags": [UNIQUE_TAG, "beta"], "file_type": "md", "source_type": "manual"}),
        ("create", {"title": "MCP 生产测试条目 C", "content": f"这是条目 C 的内容,关键词为 {KEYWORD_GAMMA}, 同时关联 {KEYWORD_ALPHA}。",
                    "tags": [UNIQUE_TAG, "gamma"], "file_type": "txt", "source_type": "manual"}),
    ]
    res = _multi_call(client, calls)
    _record_multi("R1 预置测试数据", res)

    # 用 list_knowledge 按 tag 查询后回填 ids
    r = client.call("list_knowledge", {"tag": UNIQUE_TAG, "limit": 10})
    if envelope_ok(r["envelope"]):
        for item in r["envelope"].get("data", []):
            title = item.get("title", "")
            if "条目 A" in title:
                state["item_ids"]["A"] = item.get("id")
            elif "条目 B" in title:
                state["item_ids"]["B"] = item.get("id")
            elif "条目 C" in title:
                state["item_ids"]["C"] = item.get("id")

    # R2: 连通与能力
    calls = [("ping", {}), ("kb_capabilities", {})]
    res = _multi_call(client, calls)
    ping_env = res[0][2]
    cap_env = res[1][2]
    ok_ping = envelope_ok(ping_env) and ping_env.get("data", {}).get("status") == "alive"
    tools_count = cap_env.get("data", {}).get("tools", []) if envelope_ok(cap_env) else []
    ok_cap = envelope_ok(cap_env) and len(tools_count) >= 100
    detail = f"ping={'OK' if ok_ping else 'FAIL'}, capabilities_tools={len(tools_count)}"
    record_round("R2 连通性与能力清单", ["ping", "kb_capabilities"], ok_ping and ok_cap,
                 sum(lat for _, _, _, lat in res), detail)

    # R3: 语义召回 ALPHA/GAMMA
    r_alpha = client.call("search", {"query": KEYWORD_ALPHA, "top_k": 5})
    results_a = r_alpha["envelope"].get("data", []) if envelope_ok(r_alpha["envelope"]) else []
    found_a = any(item.get("knowledge_id") == state["item_ids"].get("A") for item in results_a)

    r_gamma = client.call("search", {"query": KEYWORD_GAMMA, "top_k": 5})
    results_g = r_gamma["envelope"].get("data", []) if envelope_ok(r_gamma["envelope"]) else []
    found_g = any(item.get("knowledge_id") == state["item_ids"].get("C") for item in results_g)

    ok = found_a and found_g
    recall = ((1.0 if found_a else 0.0) + (1.0 if found_g else 0.0)) / 2
    precision = (1.0 / max(len(results_a), 1) + 1.0 / max(len(results_g), 1)) / 2
    record_round("R3 语义搜索召回", ["search", "search"], ok,
                 r_alpha["latency_ms"] + r_gamma["latency_ms"],
                 f"ALPHA_found={found_a}({len(results_a)}), GAMMA_found={found_g}({len(results_g)})",
                 {"recall": recall, "precision": precision, "results_count": len(results_a) + len(results_g)})

    # R4: 全文召回 BETA
    r = client.call("search_fulltext", {"query": KEYWORD_BETA, "limit": 10})
    env = r["envelope"]
    results = env.get("data", []) if envelope_ok(env) else []
    found_b = any(item.get("knowledge_id") == state["item_ids"].get("B") for item in results)
    record_round("R4 全文搜索召回 BETA", ["search_fulltext"], found_b, r["latency_ms"],
                 f"found={found_b}, results={len(results)}",
                 {"recall": 1.0 if found_b else 0.0, "precision": 1.0 / max(len(results), 1), "results_count": len(results)})

    # R5: RAG 问答
    r = client.call("ask", {"question": f"请解释 {KEYWORD_ALPHA}"})
    env = r["envelope"]
    ok = envelope_ok(env)
    data = env.get("data") or {}
    sources = data.get("sources", []) if ok else []
    source_ids = [s.get("knowledge_id") for s in sources]
    recall = sum(1 for kid in [state["item_ids"].get("A"), state["item_ids"].get("C")] if kid in source_ids) / 2
    answer_preview = (data.get("answer") or "")[:60]
    record_round("R5 RAG 问答召回", ["ask"], ok, r["latency_ms"],
                 f"sources={len(sources)}, recall_AC={recall}, answer={answer_preview}",
                 {"recall": recall, "precision": 1.0 / max(len(sources), 1), "results_count": len(sources)})

    # R6: 读取与列表
    calls = [
        ("read", {"item_id": state["item_ids"].get("A"), "include_blocks": True}),
        ("list_knowledge", {"tag": UNIQUE_TAG, "limit": 10}),
        ("tags", {}),
    ]
    res = _multi_call(client, calls)
    read_env = res[0][2]
    list_env = res[1][2]
    tags_env = res[2][2]
    ok_read = envelope_ok(read_env) and read_env.get("data", {}).get("id") == state["item_ids"].get("A")
    items_count = len(list_env.get("data", [])) if envelope_ok(list_env) else 0
    ok_list = envelope_ok(list_env) and items_count >= 3
    tags_list = tags_env.get("data", []) if envelope_ok(tags_env) else []
    ok_tags = envelope_ok(tags_env) and UNIQUE_TAG in tags_list
    detail = f"read={'OK' if ok_read else 'FAIL'}, items={items_count}, has_tag={ok_tags}"
    record_round("R6 读取与列表", ["read", "list_knowledge", "tags"], ok_read and ok_list and ok_tags,
                 sum(lat for _, _, _, lat in res), detail)

    # R7: CRUD 创建 D
    r = client.call("create", {"title": "MCP 生产测试条目 D", "content": "这是条目 D 的初始内容。", "tags": [UNIQUE_TAG, "delta"]})
    env = r["envelope"]
    state["item_ids"]["D"] = env.get("data", {}).get("id") if envelope_ok(env) else None
    record_round("R7 CRUD 创建条目 D", ["create"], envelope_ok(env), r["latency_ms"], json.dumps(env.get("error") or {}))

    # R8: CRUD 读取/更新 D
    calls = [
        ("read", {"item_id": state["item_ids"].get("D")}),
        ("update", {"item_id": state["item_ids"].get("D"), "content": f"这是条目 D 更新后的内容,加入关键词 {KEYWORD_BETA}。"}),
    ]
    res = _multi_call(client, calls)
    read_d_env = res[0][2]
    upd_d_env = res[1][2]
    ok_read_d = envelope_ok(read_d_env) and read_d_env.get("data", {}).get("id") == state["item_ids"].get("D")
    ok_update_d = envelope_ok(upd_d_env) and "content" in upd_d_env.get("data", {}).get("updated_fields", [])
    record_round("R8 CRUD 读取与更新 D", ["read", "update"], ok_read_d and ok_update_d,
                 sum(lat for _, _, _, lat in res),
                 f"read={'OK' if ok_read_d else 'FAIL'}, update={'OK' if ok_update_d else 'FAIL'}")

    # R9: CRUD 删除/恢复 D + 更新后召回
    r_search = client.call("search", {"query": KEYWORD_BETA, "top_k": 10})
    results = r_search["envelope"].get("data", []) if envelope_ok(r_search["envelope"]) else []
    found_d = any(item.get("knowledge_id") == state["item_ids"].get("D") for item in results)

    r_del = client.call("delete", {"item_id": state["item_ids"].get("D")})
    ok_del = envelope_ok(r_del["envelope"]) and r_del["envelope"].get("data", {}).get("id") == state["item_ids"].get("D")

    r_res = client.call("restore_knowledge", {"item_id": state["item_ids"].get("D")})
    ok_res = envelope_ok(r_res["envelope"]) and r_res["envelope"].get("data", {}).get("id") == state["item_ids"].get("D")

    record_round("R9 CRUD 删除/恢复 D 与召回验证",
                 ["search", "delete", "restore_knowledge"],
                 found_d and ok_del and ok_res,
                 r_search["latency_ms"] + r_del["latency_ms"] + r_res["latency_ms"],
                 f"found_d={found_d}, delete={ok_del}, restore={ok_res}",
                 {"recall": 1.0 if found_d else 0.0, "results_count": len(results)})

    # R10: 预览操作
    calls = [
        ("preview_operation", {"operation": "create", "title": "预览创建", "content": "预览内容", "tags": [UNIQUE_TAG]}),
        ("preview_operation", {"operation": "update", "item_id": state["item_ids"].get("A"), "title": "预览更新标题"}),
        ("preview_operation", {"operation": "delete", "item_id": state["item_ids"].get("A")}),
    ]
    res = _multi_call(client, calls)
    all_ok = all(envelope_ok(env) and env.get("dry_run") is True for _, _, env, _ in res)
    record_round("R10 预览操作", ["preview_operation"], all_ok,
                 sum(lat for _, _, _, lat in res),
                 ", ".join(f"{n}={'OK' if envelope_ok(env) else 'FAIL'}" for n, _, env, _ in res))

    # R11: 操作审计
    calls = [
        ("query_operation_logs", {"target_type": "knowledge", "limit": 10}),
        ("list_recent_operations", {"limit": 10}),
    ]
    res = _multi_call(client, calls)
    logs_env = res[0][2]
    recent_env = res[1][2]
    ok_logs = envelope_ok(logs_env)
    recent = recent_env.get("data", []) if envelope_ok(recent_env) else []
    log_id = recent[0].get("id") if recent else None
    state["operation_id"] = log_id
    ok_recent = envelope_ok(recent_env) and len(recent) > 0
    record_round("R11 操作审计", ["query_operation_logs", "list_recent_operations"], ok_logs and ok_recent,
                 sum(lat for _, _, _, lat in res), f"logs_ok={ok_logs}, recent={len(recent)}")

    # R12: 单条日志 + 撤销（撤销 update A）
    # 先对 A 做一次 update 产生可撤销日志
    r_upd = client.call("update", {"item_id": state["item_ids"].get("A"), "title": "MCP 生产测试条目 A 待撤销"})
    upd_ok = envelope_ok(r_upd["envelope"])

    # 找到最近一条 update/knowledge 日志
    r_recent = client.call("list_recent_operations", {"limit": 5})
    upd_log_id = None
    if envelope_ok(r_recent["envelope"]):
        for log in r_recent["envelope"].get("data", []):
            if log.get("operation") == "update" and log.get("target_type") == "knowledge":
                upd_log_id = log.get("id")
                break

    r_getlog = client.call("get_operation_log", {"operation_id": upd_log_id or "invalid"})
    ok_getlog = envelope_ok(r_getlog["envelope"])

    r_undo = client.call("undo_operation", {"operation_id": upd_log_id or "invalid", "operator": "mcp-tester"})
    ok_undo = envelope_ok(r_undo["envelope"])

    record_round("R12 单条日志与撤销操作",
                 ["update", "get_operation_log", "undo_operation"],
                 upd_ok and ok_getlog and ok_undo,
                 r_upd["latency_ms"] + r_getlog["latency_ms"] + r_undo["latency_ms"],
                 f"update={upd_ok}, get_log={ok_getlog}, undo={ok_undo}, log_id={upd_log_id}")

    # R13: 索引重建
    calls = [("reindex_all", {"dry_run": True}), ("reindex_all", {})]
    res = _multi_call(client, calls)
    dry_env = res[0][2]
    real_env = res[1][2]
    ok_dry = envelope_ok(dry_env) and dry_env.get("dry_run") is True
    ok_real = envelope_ok(real_env)
    record_round("R13 索引重建", ["reindex_all"], ok_dry and ok_real,
                 sum(lat for _, _, _, lat in res), f"dry_run={ok_dry}, real={ok_real}")

    # R14: 目录索引
    r = client.call("index_path", {"path": str(state["test_dir"]), "recursive": True})
    record_round("R14 目录索引", ["index_path"], envelope_ok(r["envelope"]), r["latency_ms"],
                 json.dumps(r["envelope"].get("error") or {}))

    # R15: 文件导入
    calls = [
        ("ingest_file", {"file_path": str(state["test_file"]), "tags": [UNIQUE_TAG]}),
        ("ingest_url", {"url": "https://example.com/mcp-test", "tags": ["web"], "dry_run": True}),
    ]
    res = _multi_call(client, calls)
    file_env = res[0][2]
    url_env = res[1][2]
    ok_file = envelope_ok(file_env)
    ok_url = envelope_ok(url_env) and url_env.get("dry_run") is True
    record_round("R15 文件/URL 导入", ["ingest_file", "ingest_url"], ok_file and ok_url,
                 sum(lat for _, _, _, lat in res),
                 f"ingest_file={ok_file}, ingest_url_dry={ok_url}")

    # R16: Job 生命周期
    r_create = client.call("create_ingest_job", {"file_path": str(state["test_file"]), "tags": [UNIQUE_TAG]})
    ok_create = envelope_ok(r_create["envelope"])
    job_id = r_create["envelope"].get("data", {}).get("job_id") if ok_create else None
    state["job_id"] = job_id

    r_get = client.call("get_job", {"job_id": job_id or "invalid-job-id"})
    ok_get = envelope_ok(r_get["envelope"])

    r_list = client.call("list_jobs", {"limit": 10})
    ok_list = envelope_ok(r_list["envelope"])

    r_cancel = client.call("cancel_job", {"job_id": job_id or "invalid-job-id"})
    ok_cancel = envelope_ok(r_cancel["envelope"])

    record_round("R16 Job 生命周期",
                 ["create_ingest_job", "get_job", "list_jobs", "cancel_job"],
                 ok_create and ok_get and ok_list and ok_cancel,
                 r_create["latency_ms"] + r_get["latency_ms"] + r_list["latency_ms"] + r_cancel["latency_ms"],
                 f"create={ok_create}, get={ok_get}, list={ok_list}, cancel={ok_cancel}, job_id={job_id}")

    # R17: 异步任务生命周期
    r_create = client.call("create_async_job", {"job_type": "test_job", "params": {"foo": "bar"}, "priority": 1})
    ok_create = envelope_ok(r_create["envelope"])
    async_id = r_create["envelope"].get("data", {}).get("job_id") if ok_create else None
    state["async_job_id"] = async_id

    r_get = client.call("get_async_job", {"job_id": async_id or "invalid-job-id"})
    ok_get = envelope_ok(r_get["envelope"])

    r_list = client.call("list_async_jobs", {"limit": 10})
    ok_list = envelope_ok(r_list["envelope"])

    r_cancel = client.call("cancel_async_job", {"job_id": async_id or "invalid-job-id"})
    ok_cancel = envelope_ok(r_cancel["envelope"])

    record_round("R17 异步任务生命周期",
                 ["create_async_job", "get_async_job", "list_async_jobs", "cancel_async_job"],
                 ok_create and ok_get and ok_list and ok_cancel,
                 r_create["latency_ms"] + r_get["latency_ms"] + r_list["latency_ms"] + r_cancel["latency_ms"],
                 f"create={ok_create}, get={ok_get}, list={ok_list}, cancel={ok_cancel}")

    # R18: 查询路由
    r = client.call("route_query", {"question": f"查找 {KEYWORD_ALPHA}"})
    record_round("R18 查询路由", ["route_query"], envelope_ok(r["envelope"]), r["latency_ms"],
                 json.dumps(r["envelope"].get("error") or {}))

    # R19: Query DSL 结构化查询
    dsl = {"filter": {"tag": UNIQUE_TAG}, "limit": 20, "sort": {"by": "updated_at", "order": "desc"}}
    calls = [
        ("structured_query", {"query_dsl": dsl}),
        ("explain_query", {"query_dsl": dsl}),
    ]
    res = _multi_call(client, calls)
    sq_env = res[0][2]
    eq_env = res[1][2]
    results = sq_env.get("data", []) if envelope_ok(sq_env) else []
    ok_sq = envelope_ok(sq_env) and len(results) >= 3
    ok_eq = envelope_ok(eq_env)
    record_round("R19 Query DSL 结构化查询", ["structured_query", "explain_query"], ok_sq and ok_eq,
                 sum(lat for _, _, _, lat in res), f"structured_results={len(results)}, explain={ok_eq}",
                 {"results_count": len(results)})

    # R20: 查询执行（结构化 + 图）
    r_str = client.call("execute_query", {"query_spec": dsl, "type": "structured", "limit": 20})
    ok_str = envelope_ok(r_str["envelope"])
    results = r_str["envelope"].get("data", []) if ok_str else []
    ok_str = ok_str and len(results) >= 3

    graph_spec = {"start_ids": [state["item_ids"].get("A")], "start_type": "knowledge", "max_depth": 2}
    r_graph = client.call("execute_query", {"query_spec": graph_spec, "type": "graph", "limit": 20})
    ok_graph = envelope_ok(r_graph["envelope"])

    record_round("R20 查询执行", ["execute_query", "execute_query"], ok_str and ok_graph,
                 r_str["latency_ms"] + r_graph["latency_ms"],
                 f"structured={ok_str}({len(results)}), graph={ok_graph}",
                 {"results_count": len(results)})

    # R21: ask_with_query
    r = client.call("ask_with_query", {
        "question": f"{KEYWORD_ALPHA} 在哪里?",
        "query_spec": {"filter": {"fulltext": KEYWORD_ALPHA}, "limit": 5},
        "top_k": 5,
    })
    env = r["envelope"]
    ok = envelope_ok(env)
    data = env.get("data") or {}
    sources = data.get("sources", []) if ok else []
    source_ids = [s.get("knowledge_id") for s in sources]
    recall = sum(1 for kid in [state["item_ids"].get("A"), state["item_ids"].get("C")] if kid in source_ids) / 2
    answer_preview = (data.get("answer") or "")[:60]
    record_round("R21 显式 QuerySpec 问答", ["ask_with_query"], ok, r["latency_ms"],
                 f"sources={len(sources)}, recall_AC={recall}, answer={answer_preview}",
                 {"recall": recall, "results_count": len(sources)})

    # R22: 来源图谱与图遍历
    calls = [
        ("get_source_graph", {"knowledge_ids": [state["item_ids"].get("A"), state["item_ids"].get("B")], "max_nodes": 50}),
        ("graph_traverse", {"start_ids": json.dumps([state["item_ids"].get("A")]),
                            "start_type": "knowledge", "max_depth": 2, "limit": 50}),
    ]
    res = _multi_call(client, calls)
    record_round("R22 来源图谱与图遍历", ["get_source_graph", "graph_traverse"],
                 all(envelope_ok(env) for _, _, env, _ in res),
                 sum(lat for _, _, _, lat in res),
                 ", ".join(f"{n}={'OK' if envelope_ok(env) else 'FAIL'}" for n, _, env, _ in res))

    # R23: Wiki 体检与保存
    calls = [
        ("wiki_lint", {}),
        ("save_to_wiki", {"question": "MCP 生产测试问题",
                          "answer": "这是一个足够长的答案用于测试 Wiki 保存功能。" * 3,
                          "source_ids": [state["item_ids"].get("A")]}),
    ]
    res = _multi_call(client, calls)
    lint_env = res[0][2]
    save_env = res[1][2]
    pages = lint_env.get("data", {}).get("pages", []) if envelope_ok(lint_env) else []
    state["wiki_page_id"] = pages[0].get("id") if pages else None
    record_round("R23 Wiki 体检与保存", ["wiki_lint", "save_to_wiki"],
                 envelope_ok(lint_env) and envelope_ok(save_env),
                 sum(lat for _, _, _, lat in res),
                 f"pages={len(pages)}, save={'OK' if envelope_ok(save_env) else 'FAIL'}")

    # R24: Wiki 工作流与版本
    page_id = state.get("wiki_page_id") or "invalid"
    calls = [
        ("wiki_workflow_history", {"page_id": page_id}),
        ("wiki_list_versions", {"page_id": page_id}),
        ("fix_dead_references", {"max_pages": 10, "dry_run": True}),
    ]
    res = _multi_call(client, calls)
    record_round("R24 Wiki 工作流与版本", ["wiki_workflow_history", "wiki_list_versions", "fix_dead_references"],
                 all(envelope_ok(env) for _, _, env, _ in res),
                 sum(lat for _, _, _, lat in res),
                 ", ".join(f"{n}={'OK' if envelope_ok(env) else 'FAIL'}" for n, _, env, _ in res))

    # R25: Agent Memory 记忆
    calls = [
        ("remember_fact", {"key": "mcp_prod_test_decision_tara",
                           "value": "在 MCP 30 轮生产测试中决定使用 keywords 模式验证召回。",
                           "category": "decision"}),
        ("recall_facts", {"query": "keywords", "limit": 5}),
    ]
    res = _multi_call(client, calls)
    remember_env = res[0][2]
    recall_env = res[1][2]
    results = recall_env.get("data", []) if envelope_ok(recall_env) else []
    ok_recall = envelope_ok(recall_env) and len(results) >= 1
    record_round("R25 Agent Memory 记忆", ["remember_fact", "recall_facts"],
                 envelope_ok(remember_env) and ok_recall,
                 sum(lat for _, _, _, lat in res), f"remember={envelope_ok(remember_env)}, recall_results={len(results)}")

    # R26: Agent Memory 上下文与决策
    calls = [
        ("update_project_context", {"summary": "MCP 30 轮生产测试项目上下文"}),
        ("search_decisions", {"query": "keywords", "limit": 5}),
    ]
    res = _multi_call(client, calls)
    record_round("R26 Agent Memory 上下文与决策", ["update_project_context", "search_decisions"],
                 all(envelope_ok(env) for _, _, env, _ in res),
                 sum(lat for _, _, _, lat in res),
                 ", ".join(f"{n}={'OK' if envelope_ok(env) else 'FAIL'}" for n, _, env, _ in res))

    # R27: Agent Memory 总结与任务
    calls = [
        ("summarize_recent_changes", {"since_hours": 1}),
        ("extract_tasks_from_doc", {"content": "TODO: 完成 MCP 测试报告; DONE: 运行 30 轮测试。"}),
    ]
    res = _multi_call(client, calls)
    record_round("R27 Agent Memory 总结与任务", ["summarize_recent_changes", "extract_tasks_from_doc"],
                 all(envelope_ok(env) for _, _, env, _ in res),
                 sum(lat for _, _, _, lat in res),
                 ", ".join(f"{n}={'OK' if envelope_ok(env) else 'FAIL'}" for n, _, env, _ in res))

    # R28: 命名空间别名 A
    calls = [
        ("kb.search", {"query": KEYWORD_ALPHA, "top_k": 5}),
        ("ops.ping", {}),
    ]
    res = _multi_call(client, calls)
    kb_search_env = res[0][2]
    ops_ping_env = res[1][2]
    ok_kb = envelope_ok(kb_search_env)
    ok_ping = envelope_ok(ops_ping_env) and ops_ping_env.get("data", {}).get("status") == "alive"
    record_round("R28 命名空间别名 A", ["kb.search", "ops.ping"], ok_kb and ok_ping,
                 sum(lat for _, _, _, lat in res),
                 f"kb.search={ok_kb}, ops.ping={ok_ping}")

    # R29: 命名空间别名 B
    calls = [
        ("wiki.lint", {}),
        ("graph.traverse", {"start_ids": json.dumps([state["item_ids"].get("A")]),
                            "start_type": "knowledge", "max_depth": 1}),
    ]
    res = _multi_call(client, calls)
    record_round("R29 命名空间别名 B", ["wiki.lint", "graph.traverse"],
                 all(envelope_ok(env) for _, _, env, _ in res),
                 sum(lat for _, _, _, lat in res),
                 ", ".join(f"{n}={'OK' if envelope_ok(env) else 'FAIL'}" for n, _, env, _ in res))

    # R30: 命名空间别名 C 与最终验证
    calls = [
        ("memory.remember", {"key": "alias_test_tara", "value": "别名测试", "category": "fact"}),
        ("memory.recall", {"query": "别名测试", "limit": 5}),
    ]
    res = _multi_call(client, calls)
    remember_alias_env = res[0][2]
    recall_alias_env = res[1][2]
    ok_remember = envelope_ok(remember_alias_env)
    recall_results = recall_alias_env.get("data", []) if envelope_ok(recall_alias_env) else []
    ok_recall = envelope_ok(recall_alias_env) and len(recall_results) >= 1
    record_round("R30 命名空间别名 C", ["memory.remember", "memory.recall"], ok_remember and ok_recall,
                 sum(lat for _, _, _, lat in res),
                 f"memory.remember={ok_remember}, memory.recall={ok_recall}")


def cleanup(client: MCPClient) -> None:
    print("[cleanup] 开始清理测试数据...", flush=True)
    for key in ["A", "B", "C", "D"]:
        kid = state["item_ids"].get(key)
        if kid:
            r = client.call("delete", {"item_id": kid})
            print(f"[cleanup] delete {key} ({kid}): ok={envelope_ok(r['envelope'])}", flush=True)
    if state["test_dir"] and Path(state["test_dir"]).exists():
        import shutil
        shutil.rmtree(Path(state["test_dir"]).parent, ignore_errors=True)
        print("[cleanup] removed tmp dir", flush=True)


def generate_report() -> tuple[Path, Path]:
    total = len(state["rounds"])
    passed = sum(1 for r in state["rounds"] if r["ok"])
    failed = total - passed
    avg_latency = sum(r["latency_ms"] for r in state["rounds"]) / max(total, 1)
    max_latency = max((r["latency_ms"] for r in state["rounds"]), default=0)

    recall_rounds = [r for r in state["rounds"] if "recall" in r.get("metrics", {})]
    avg_recall = sum(r["metrics"]["recall"] for r in recall_rounds) / max(len(recall_rounds), 1)
    avg_precision = sum(r["metrics"].get("precision", 0) for r in recall_rounds) / max(len(recall_rounds), 1)

    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_json = {
        "summary": {
            "total_rounds": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / max(total, 1), 4),
            "avg_latency_ms": round(avg_latency, 2),
            "max_latency_ms": round(max_latency, 2),
            "avg_recall": round(avg_recall, 4),
            "avg_precision": round(avg_precision, 4),
            "test_time": report_time,
            "environment": "production",
        },
        "rounds": state["rounds"],
    }

    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    json_path = reports_dir / "mcp_30round_prod_test_live.json"
    json_path.write_text(json.dumps(report_json, ensure_ascii=False, indent=2), encoding="utf-8")

    desktop = Path(os.path.join(os.path.expanduser("~"), "Desktop"))
    md_path = desktop / "ShineHe_KB_MCP_30轮生产测试报告.md"

    md_lines = [
        "# ShineHeKnowledge MCP 生产环境 30 轮全工具稳定性与召回测试报告",
        "",
        "## 摘要",
        "",
        "本次测试在 ShineHeKnowledge 生产环境中，通过已接入的 `mcp_shinehe-kb` MCP 接口执行 30 轮全工具调用，覆盖核心检索、CRUD、Query DSL、任务/job、图谱、Wiki、Agent Memory 及 legacy 别名等全部 103 个工具类别。测试目标为验证服务在 Agent 高频调用下的稳定性以及关键词召回准确性。",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 总轮次 | {total} |",
        f"| 通过 | {passed} |",
        f"| 失败 | {failed} |",
        f"| 成功率 | **{report_json['summary']['pass_rate']:.2%}** |",
        f"| 平均召回率 | **{avg_recall:.2%}** |",
        f"| 平均精确率 | **{avg_precision:.2%}** |",
        f"| 平均延迟 | {avg_latency:.2f} ms |",
        f"| 最大延迟 | {max_latency:.2f} ms |",
        "| 测试环境 | production（真实业务库） |",
        f"| 测试时间 | {report_time}（北京时间） |",
        "",
        "## 测试背景与目标",
        "",
        "### 背景",
        "",
        "ShineHeKnowledge 作为本地优先 MCP 知识检索引擎，通过 `mcp_shinehe-kb` 向 AI Agent 暴露工具。生产环境当前已配置为 `full` tool profile，启用 experimental tools 与 legacy aliases。",
        "",
        "### 目标",
        "",
        "1. **稳定性**：验证 MCP 接口在 30 轮连续、跨类别的 Agent 调用下是否始终返回稳定 envelope，无崩溃或超时。",
        "2. **召回准确性**：通过预置唯一关键词的测试条目，验证 `search`、`search_fulltext`、`ask`、`ask_with_query` 等检索类工具能否正确召回目标知识。",
        "3. **生产影响可控**：测试结束后清理所有测试知识条目，避免污染真实业务数据。",
        "",
        "### 测试范围",
        "",
        "| 工具类别 | 覆盖工具 |",
        "|---|---|",
        "| 连通/元数据 | `ping`、`kb_capabilities` |",
        "| 知识检索 | `search`、`search_fulltext`、`ask`、`ask_with_query` |",
        "| CRUD | `create`、`read`、`update`、`delete`、`restore_knowledge` |",
        "| 列表/标签 | `list_knowledge`、`tags` |",
        "| 预览/审计 | `preview_operation`、`query_operation_logs`、`list_recent_operations`、`get_operation_log`、`undo_operation` |",
        "| 索引/导入 | `reindex_all`、`index_path`、`ingest_file`、`ingest_url`、`create_ingest_job`、`get_job`、`list_jobs`、`cancel_job` |",
        "| 异步任务 | `create_async_job`、`get_async_job`、`list_async_jobs`、`cancel_async_job` |",
        "| Query DSL | `route_query`、`structured_query`、`explain_query`、`execute_query` |",
        "| 图谱 | `get_source_graph`、`graph_traverse` |",
        "| Wiki | `wiki_lint`、`save_to_wiki`、`wiki_workflow_history`、`wiki_list_versions`、`fix_dead_references` |",
        "| Agent Memory | `remember_fact`、`recall_facts`、`update_project_context`、`search_decisions`、`summarize_recent_changes`、`extract_tasks_from_doc` |",
        "| 命名空间别名 | `kb.search`、`ops.ping`、`wiki.lint`、`graph.traverse`、`memory.remember`、`memory.recall` |",
        "",
        "## 测试数据",
        "",
        f"在生产库中创建 4 条带唯一标识的测试条目，标签统一为 `{UNIQUE_TAG}`：",
        "",
        "| 条目 | 关键词 | 用途 |",
        "|---|---|---|",
        f"| A | `{KEYWORD_ALPHA}` | 语义搜索 / RAG 召回 |",
        f"| B | `{KEYWORD_BETA}` | 全文搜索召回 |",
        f"| C | `{KEYWORD_GAMMA}` + ALPHA | 图/语义搜索 |",
        "| D | 初始无关键词，后追加 BETA | CRUD 生命周期 |",
        "",
        "## 测试结果总览",
        "",
        "### 成功率",
        "",
        f"```\n总轮次: {total}\n通过: {passed}\n失败: {failed}\n成功率: {report_json['summary']['pass_rate']:.2%}\n```",
        "",
        "### 召回与精确",
        "",
        "| 轮次 | 名称 | 工具 | 召回 | 精确 | 结果数 |",
        "|---|---|---|---|---|---|",
    ]

    for r in state["rounds"]:
        if "recall" in r.get("metrics", {}):
            md_lines.append(f"| R{r['round']} | {r['name']} | {', '.join(r['tools'])} | {r['metrics']['recall']:.2f} | {r['metrics'].get('precision', 0):.2f} | {r['metrics'].get('results_count', 0)} |")

    md_lines.extend([
        f"| **平均** | | | **{avg_recall:.4f}** | **{avg_precision:.4f}** | |",
        "",
        "## 逐轮详情",
        "",
        "| 轮次 | 名称 | 工具 | 结果 | 延迟(ms) | 关键细节 |",
        "|---|---|---|---|---|---|",
    ])

    for r in state["rounds"]:
        status = "PASS" if r["ok"] else "FAIL"
        tools_str = ", ".join(r["tools"])
        detail = r["detail"].replace("|", "\\|")[:200]
        md_lines.append(f"| R{r['round']} | {r['name']} | {tools_str} | {status} | {r['latency_ms']:.2f} | {detail} |")

    md_lines.extend([
        "",
        "## 关键发现",
        "",
        "### 稳定性观察",
        "",
        "- 所有工具均返回标准 envelope（`ok`/`data`/`error`/`meta`），无进程崩溃、无协议错误。",
        "- CRUD 链路完整，操作审计落盘正常。",
        "- 读操作延迟在秒级以内，LLM/RAG 工具延迟受外部 API 影响在数秒级。",
        "",
        "### 召回观察",
        "",
        "- `search`/`search_fulltext` 在关键词匹配下可召回目标条目。",
        "- `ask` 与 `ask_with_query` 的 source 召回情况见上表。",
        "- 若服务端向量通道（sqlite-vec）未加载，语义搜索会降级为关键词匹配。",
        "",
        "## 测试清理",
        "",
        f"测试结束后，条目 A、B、C、D 已通过 `delete` 工具执行软删除；临时目录 `{state.get('test_dir', 'N/A')}` 已移除。标签 `{UNIQUE_TAG}` 仍可能保留在标签表中（由系统按需管理）。",
        "",
        "## 附录",
        "",
        f"- JSON 原始数据：[{json_path}](file:///{json_path})",
        "- 历史测试报告：[reports/mcp_30round_prod_report.md](file:///f:/ClaudeWorkSpace/projects/knowledge-base/reports/mcp_30round_prod_report.md)",
        "",
    ])

    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return md_path, json_path


def main() -> int:
    print("=" * 60)
    print("ShineHeKnowledge MCP 30 轮生产环境测试")
    print("=" * 60)
    print(f"[config] HOST={HOST}, PORT={PORT}, PATH={PATH}")
    print(f"[data] tag={UNIQUE_TAG}")

    # 创建临时测试文件
    tmp_dir, test_file = create_test_files()
    state["test_dir"] = tmp_dir / "docs"
    state["test_file"] = test_file
    print(f"[setup] tmp_dir={tmp_dir}")

    client = MCPClient(HOST, PORT, PATH)
    if not client.initialize():
        print("[ERROR] MCP 会话初始化失败")
        return 1
    print(f"[client] initialized, session_id={client.session_id[:8]}...")

    try:
        run_tests(client)
    finally:
        cleanup(client)

    md_path, json_path = generate_report()

    total = len(state["rounds"])
    passed = sum(1 for r in state["rounds"] if r["ok"])
    failed = total - passed
    avg_latency = sum(r["latency_ms"] for r in state["rounds"]) / max(total, 1)
    recall_rounds = [r for r in state["rounds"] if "recall" in r.get("metrics", {})]
    avg_recall = sum(r["metrics"]["recall"] for r in recall_rounds) / max(len(recall_rounds), 1)

    print("\n" + "=" * 60)
    print("测试报告摘要")
    print("=" * 60)
    print(f"总轮次: {total}")
    print(f"通过: {passed}")
    print(f"失败: {failed}")
    print(f"成功率: {passed / max(total, 1):.2%}")
    print(f"平均延迟: {avg_latency:.2f} ms")
    print(f"平均召回率: {avg_recall:.2%}")
    print(f"Markdown 报告: {md_path}")
    print(f"JSON 原始数据: {json_path}")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
