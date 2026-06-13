"""MCP Server 高并发压力测试

模拟多客户端并发调用 MCP 工具，验证服务稳定性和响应时间。

用法:
    python scripts/stress_test_mcp.py                  # 默认测试
    python scripts/stress_test_mcp.py --rounds 3       # 多轮测试
    python scripts/stress_test_mcp.py --concurrency 30 # 指定并发数
"""
import argparse
import http.client
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9000
DEFAULT_PATH = "/mcp"


@dataclass
class ToolCallResult:
    tool: str
    args: dict
    success: bool
    elapsed_ms: float
    error: str = ""
    status_code: int = 0


@dataclass
class StressTestReport:
    concurrency: int = 0
    total_calls: int = 0
    success_count: int = 0
    fail_count: int = 0
    total_elapsed_ms: float = 0
    latencies: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.success_count / max(self.total_calls, 1) * 100

    @property
    def avg_latency_ms(self) -> float:
        return statistics.mean(self.latencies) if self.latencies else 0

    @property
    def p50_latency_ms(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0

    @property
    def p95_latency_ms(self) -> float:
        if len(self.latencies) < 2:
            return self.latencies[0] if self.latencies else 0
        return sorted(self.latencies)[int(len(self.latencies) * 0.95)]

    @property
    def p99_latency_ms(self) -> float:
        if len(self.latencies) < 2:
            return self.latencies[0] if self.latencies else 0
        return sorted(self.latencies)[int(len(self.latencies) * 0.99)]


def _mcp_call(host: str, port: int, path: str, body: dict,
              session_id: str | None = None, timeout: float = 90) -> tuple[int, dict | str]:
    """发送 MCP JSON-RPC 请求"""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("POST", path, body=json.dumps(body), headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        parsed = None
        if raw.startswith("event:"):
            for line in raw.split("\n"):
                if line.startswith("data: "):
                    try:
                        parsed = json.loads(line[6:])
                    except json.JSONDecodeError:
                        parsed = line[6:]
                    break
        else:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
        return resp.status, parsed
    except Exception as e:
        return -1, str(e)
    finally:
        conn.close()


def init_session(host: str, port: int, path: str) -> str | None:
    """初始化 MCP 会话，返回 session_id"""
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
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request("POST", path, body=json.dumps(body), headers=headers)
        resp = conn.getresponse()
        resp.read()
        session_id = None
        for k, v in resp.getheaders():
            if k.lower() == "mcp-session-id":
                session_id = v
                break
        return session_id
    except Exception:
        return None
    finally:
        conn.close()


# ---- 测试用例定义 ----

TOOL_CALLS = [
    {"tool": "ping", "args": {}},
    {"tool": "ping", "args": {}},
    {"tool": "search_fulltext", "args": {"query": "门店宝", "limit": 5}},
    {"tool": "search_fulltext", "args": {"query": "劳动竞赛", "limit": 5}},
    {"tool": "tags", "args": {}},
    {"tool": "list_knowledge", "args": {"limit": 3, "offset": 0}},
    {"tool": "kb_capabilities", "args": {}},
    {"tool": "search", "args": {"query": "云改数转 智算平台", "top_k": 3}},
    {"tool": "search", "args": {"query": "技能竞赛管理办法", "top_k": 5}},
    {"tool": "wiki_lint", "args": {}},
]


def run_single_call(host: str, port: int, path: str, session_id: str,
                    tool: str, args: dict, call_id: int) -> ToolCallResult:
    """执行单次工具调用并记录结果"""
    body = {
        "jsonrpc": "2.0",
        "id": call_id + 100,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }
    t0 = time.monotonic()
    status, result = _mcp_call(host, port, path, body, session_id, timeout=90)
    elapsed_ms = (time.monotonic() - t0) * 1000

    success = False
    error = ""
    if status == 200 and isinstance(result, dict):
        if "result" in result:
            content = result["result"].get("content", [])
            # 检查是否有 isError
            if result["result"].get("isError"):
                error = str(content)[:200]
            else:
                success = True
        elif "error" in result:
            error = str(result["error"])[:200]
    else:
        error = f"HTTP {status}: {str(result)[:200]}"

    return ToolCallResult(
        tool=tool, args=args, success=success,
        elapsed_ms=round(elapsed_ms, 1), error=error, status_code=status,
    )


def run_stress_round(host: str, port: int, path: str, session_id: str,
                     concurrency: int, calls: list[dict], round_id: int) -> StressTestReport:
    """以指定并发度执行一轮压力测试"""
    report = StressTestReport(concurrency=concurrency, total_calls=len(calls))

    # 为每个调用分配唯一 ID
    indexed_calls = [(i, c) for i, c in enumerate(calls)]

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for idx, call in indexed_calls:
            f = pool.submit(
                run_single_call, host, port, path, session_id,
                call["tool"], call["args"], round_id * 1000 + idx,
            )
            futures[f] = call

        for future in as_completed(futures):
            result = future.result()
            report.total_calls += 0  # already counted
            if result.success:
                report.success_count += 1
                report.latencies.append(result.elapsed_ms)
            else:
                report.fail_count += 1
                report.errors.append(f"{result.tool}: {result.error}")

    # 重新计算 total_calls
    report.total_calls = report.success_count + report.fail_count
    return report


def print_report(report: StressTestReport, label: str = ""):
    """打印单轮测试报告"""
    tag = f" [{label}]" if label else ""
    print(f"\n{'='*60}")
    print(f"  压力测试报告{tag}")
    print(f"{'='*60}")
    print(f"  并发数:     {report.concurrency}")
    print(f"  总调用数:   {report.total_calls}")
    print(f"  成功:       {report.success_count}")
    print(f"  失败:       {report.fail_count}")
    print(f"  成功率:     {report.success_rate:.1f}%")
    if report.latencies:
        print("  延迟统计:")
        print(f"    平均:     {report.avg_latency_ms:,.0f} ms")
        print(f"    P50:      {report.p50_latency_ms:,.0f} ms")
        print(f"    P95:      {report.p95_latency_ms:,.0f} ms")
        print(f"    P99:      {report.p99_latency_ms:,.0f} ms")
        print(f"    最小:     {min(report.latencies):,.0f} ms")
        print(f"    最大:     {max(report.latencies):,.0f} ms")
    if report.errors:
        print("  错误详情:")
        seen = set()
        for e in report.errors:
            short = e[:120]
            if short not in seen:
                seen.add(short)
                print(f"    - {e}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="MCP Server 高并发压力测试")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", "-p", type=int, default=DEFAULT_PORT)
    parser.add_argument("--path", default=DEFAULT_PATH)
    parser.add_argument("--concurrency", "-c", type=int, default=10,
                        help="最大并发数（默认 10）")
    parser.add_argument("--rounds", "-r", type=int, default=2,
                        help="测试轮数（默认 2）")
    parser.add_argument("--ramp", action="store_true",
                        help="阶梯式增加并发度（5→10→20→N）")
    args = parser.parse_args()

    print("=" * 60)
    print("  ShineHeKnowledge MCP Server 高并发压力测试")
    print("=" * 60)

    # 初始化会话
    print("\n[准备] 初始化 MCP 会话 ...", end=" ")
    session_id = init_session(args.host, args.port, args.path)
    if not session_id:
        print("FAIL — 无法初始化 MCP 会话")
        sys.exit(1)
    print(f"OK (session={session_id[:16]}...)")

    all_reports = []

    if args.ramp:
        # 阶梯式并发测试
        levels = sorted(set([5, 10, 20, args.concurrency]))
        for level in levels:
            calls = TOOL_CALLS * max(1, level // len(TOOL_CALLS) + 1)
            calls = calls[:level * 2]  # 每个并发度发 2x 调用

            print(f"\n[测试] 并发度 {level}, 总调用 {len(calls)} ...")
            report = run_stress_round(
                args.host, args.port, args.path, session_id,
                level, calls, len(all_reports),
            )
            all_reports.append(report)
            print_report(report, label=f"并发={level}")
            time.sleep(1)  # 轮间休息
    else:
        # 固定并发多轮测试
        calls = TOOL_CALLS * max(1, args.concurrency // len(TOOL_CALLS) + 1)
        calls = calls[:args.concurrency * 2]

        for r in range(args.rounds):
            print(f"\n[测试] 第 {r+1}/{args.rounds} 轮, 并发度 {args.concurrency}, "
                  f"总调用 {len(calls)} ...")
            report = run_stress_round(
                args.host, args.port, args.path, session_id,
                args.concurrency, calls, r,
            )
            all_reports.append(report)
            print_report(report, label=f"轮次 {r+1}")
            if r < args.rounds - 1:
                time.sleep(1)

    # 总结
    print(f"\n{'#'*60}")
    print("  总结")
    print(f"{'#'*60}")
    total = sum(r.total_calls for r in all_reports)
    ok_count = sum(r.success_count for r in all_reports)
    fail_count = sum(r.fail_count for r in all_reports)
    all_latencies = []
    for r in all_reports:
        all_latencies.extend(r.latencies)

    print(f"  总调用数:  {total}")
    print(f"  总成功:    {ok_count}")
    print(f"  总失败:    {fail_count}")
    print(f"  总成功率:  {ok_count/max(total,1)*100:.1f}%")
    if all_latencies:
        print(f"  全局平均延迟:  {statistics.mean(all_latencies):,.0f} ms")
        print(f"  全局 P95 延迟: {sorted(all_latencies)[int(len(all_latencies)*0.95)]:,.0f} ms")
        print(f"  全局最大延迟:  {max(all_latencies):,.0f} ms")

    if fail_count == 0:
        print(f"\n  [PASS] 所有 {total} 次调用全部成功，服务稳定!")
    elif ok_count / max(total, 1) >= 0.95:
        print(f"\n  [WARN] 成功率 {ok_count/max(total,1)*100:.1f}%，少量失败可接受")
    else:
        print("\n  [FAIL] 成功率过低，请检查服务端日志")

    print(f"{'#'*60}")


if __name__ == "__main__":
    main()
