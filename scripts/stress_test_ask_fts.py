"""针对 ask 和 search_fulltext 的并发压力测试

重点模拟 TeleClaw 实际使用场景：
1. search_fulltext 并发调用（之前 SQLITE_MISUSE 的高发区）
2. ask 工具并发调用（RAG 管线，涉及多阶段 DB + LLM）
3. 混合调用（search + search_fulltext + ask 同时并发）
"""
import http.client
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HOST = "127.0.0.1"
PORT = 9000
PATH = "/mcp"


def mcp_call(session_id, tool, args, call_id, timeout=120):
    """发送单次 MCP 工具调用"""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Mcp-Session-Id": session_id,
    }
    body = {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }
    conn = http.client.HTTPConnection(HOST, PORT, timeout=timeout)
    t0 = time.monotonic()
    try:
        conn.request("POST", PATH, body=json.dumps(body), headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        elapsed_ms = (time.monotonic() - t0) * 1000

        # 解析 SSE 响应
        parsed = None
        if raw.startswith("event:"):
            for line in raw.split("\n"):
                if line.startswith("data: "):
                    try:
                        parsed = json.loads(line[6:])
                    except json.JSONDecodeError:
                        parsed = {"raw": line[6:][:200]}
                    break
        else:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"raw": raw[:200]}

        # 判断成功/失败
        success = False
        error = ""
        if isinstance(parsed, dict):
            result = parsed.get("result", {})
            if result.get("isError"):
                content = result.get("content", [])
                error = str(content[0].get("text", ""))[:200] if content else "unknown error"
            elif "content" in result:
                success = True
            elif "error" in parsed:
                error = str(parsed["error"])[:200]
            else:
                success = True

        return {
            "tool": tool, "success": success, "elapsed_ms": round(elapsed_ms, 1),
            "error": error, "status": resp.status,
        }
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return {
            "tool": tool, "success": False, "elapsed_ms": round(elapsed_ms, 1),
            "error": str(e)[:200], "status": -1,
        }
    finally:
        conn.close()


def init_session():
    """初始化 MCP 会话"""
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "stress-test", "version": "1.0"},
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    conn = http.client.HTTPConnection(HOST, PORT, timeout=10)
    try:
        conn.request("POST", PATH, body=json.dumps(body), headers=headers)
        resp = conn.getresponse()
        resp.read()
        for k, v in resp.getheaders():
            if k.lower() == "mcp-session-id":
                return v
    finally:
        conn.close()
    return None


def run_test(name, calls, concurrency, session_id):
    """执行一轮并发测试"""
    print(f"\n{'='*65}")
    print(f"  测试: {name}")
    print(f"  并发: {concurrency} | 调用数: {len(calls)}")
    print(f"{'='*65}")

    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(mcp_call, session_id, c["tool"], c["args"], 100 + i): c
            for i, c in enumerate(calls)
        }
        for future in as_completed(futures):
            results.append(future.result())

    # 统计
    ok_results = [r for r in results if r["success"]]
    fail_results = [r for r in results if not r["success"]]
    latencies = [r["elapsed_ms"] for r in ok_results]

    print(f"  成功: {len(ok_results)}/{len(results)}")
    print(f"  失败: {len(fail_results)}/{len(results)}")
    if latencies:
        print(f"  延迟: avg={statistics.mean(latencies):,.0f}ms  "
              f"p50={statistics.median(latencies):,.0f}ms  "
              f"max={max(latencies):,.0f}ms")
    if fail_results:
        print("  错误:")
        seen = set()
        for r in fail_results:
            key = f"{r['tool']}: {r['error'][:80]}"
            if key not in seen:
                seen.add(key)
                print(f"    - [{r['tool']}] {r['error'][:120]}")
    print()
    return len(ok_results), len(results), fail_results


def main():
    print("=" * 65)
    print("  ask + search_fulltext 并发压力测试")
    print("  验证线程本地连接修复效果")
    print("=" * 65)

    session_id = init_session()
    if not session_id:
        print("[FATAL] 无法初始化 MCP 会话")
        sys.exit(1)
    print(f"\n会话已建立: {session_id[:20]}...")

    total_ok = 0
    total_calls = 0
    all_failures = []

    # ---- 测试 1: search_fulltext 并发 10 ----
    calls_fts = [
        {"tool": "search_fulltext", "args": {"query": q, "limit": 5}}
        for q in [
            "门店宝", "息壤", "智算平台", "云改数转", "劳动竞赛",
            "技能竞赛", "管理办法", "竞赛规则", "渠道触点", "规模拓展",
        ]
    ]
    ok, n, fails = run_test("search_fulltext x10 并发", calls_fts, 10, session_id)
    total_ok += ok
    total_calls += n
    all_failures.extend(fails)

    # ---- 测试 2: search_fulltext 并发 20 ----
    calls_fts_20 = calls_fts * 2  # 20 个调用
    ok, n, fails = run_test("search_fulltext x20 并发", calls_fts_20, 20, session_id)
    total_ok += ok
    total_calls += n
    all_failures.extend(fails)

    # ---- 测试 3: ask 并发 5 ----
    calls_ask = [
        {"tool": "ask", "args": {"question": q, "include_graph": False}}
        for q in [
            "什么是门店宝？",
            "劳动竞赛的管理办法有哪些？",
            "云改数转的核心内容是什么？",
            "技能竞赛如何组织？",
            "渠道触点优先赛道的规则是什么？",
        ]
    ]
    ok, n, fails = run_test("ask x5 并发（RAG管线）", calls_ask, 5, session_id)
    total_ok += ok
    total_calls += n
    all_failures.extend(fails)

    # ---- 测试 4: 混合并发（search + search_fulltext + ask + ping）----
    calls_mixed = [
        {"tool": "ping", "args": {}},
        {"tool": "ping", "args": {}},
        {"tool": "search_fulltext", "args": {"query": "竞赛管理", "limit": 5}},
        {"tool": "search_fulltext", "args": {"query": "智算平台", "limit": 5}},
        {"tool": "search", "args": {"query": "门店宝使用方法", "top_k": 3}},
        {"tool": "search", "args": {"query": "劳动竞赛规则", "top_k": 3}},
        {"tool": "ask", "args": {"question": "规模拓展攻坚赛道怎么参加？", "include_graph": False}},
        {"tool": "ask", "args": {"question": "计划外竞赛如何管理？", "include_graph": False}},
        {"tool": "tags", "args": {}},
        {"tool": "list_knowledge", "args": {"limit": 3}},
    ]
    ok, n, fails = run_test("混合工具 x10 并发", calls_mixed, 10, session_id)
    total_ok += ok
    total_calls += n
    all_failures.extend(fails)

    # ---- 测试 5: 极端并发 search_fulltext x30 ----
    calls_extreme = [
        {"tool": "search_fulltext", "args": {"query": q, "limit": 3}}
        for q in [
            "门店宝", "息壤", "智算", "云改", "劳动", "技能", "管理",
            "竞赛", "渠道", "规模", "攻坚", "赛道", "办法", "规则", "平台",
        ]
    ] * 2  # 30 calls
    ok, n, fails = run_test("search_fulltext x30 极端并发", calls_extreme, 30, session_id)
    total_ok += ok
    total_calls += n
    all_failures.extend(fails)

    # ---- 总结 ----
    print(f"\n{'#'*65}")
    print("  总结")
    print(f"{'#'*65}")
    print(f"  总调用:  {total_calls}")
    print(f"  总成功:  {total_ok}")
    print(f"  总失败:  {total_calls - total_ok}")
    print(f"  成功率:  {total_ok/max(total_calls,1)*100:.1f}%")

    if total_calls - total_ok == 0:
        print("\n  [PASS] 所有调用全部成功，线程本地连接修复有效!")
    else:
        # 分析失败类型
        fts_errors = [f for f in all_failures if "search_fulltext" in f["tool"]]
        ask_errors = [f for f in all_failures if f["tool"] == "ask"]
        other_errors = [f for f in all_failures if f["tool"] not in ("search_fulltext", "ask")]
        if fts_errors:
            print(f"\n  search_fulltext 失败: {len(fts_errors)} 次")
            for e in fts_errors[:3]:
                print(f"    - {e['error'][:100]}")
        if ask_errors:
            print(f"\n  ask 失败: {len(ask_errors)} 次")
            for e in ask_errors[:3]:
                print(f"    - {e['error'][:100]}")
        if other_errors:
            print(f"\n  其他工具失败: {len(other_errors)} 次")

    print(f"{'#'*65}")


if __name__ == "__main__":
    main()
