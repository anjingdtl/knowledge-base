"""MCP Server 连接诊断工具

用法:
    python scripts/check_mcp.py              # 完整诊断
    python scripts/check_mcp.py --ping       # 仅测试 ping
    python scripts/check_mcp.py --tools      # 仅列出可用工具
"""
import argparse
import http.client
import json
import sys
import time

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9000
DEFAULT_PATH = "/mcp"


def _post(host: str, port: int, path: str, body: dict, session_id: str | None = None) -> tuple[int, dict | str, dict]:
    """发送 MCP JSON-RPC 请求，返回 (status_code, parsed_body_or_raw, headers)"""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request("POST", path, body=json.dumps(body), headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        resp_headers = dict(resp.getheaders())

        # 尝试解析 JSON（SSE 格式时提取 data: 行）
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

        return resp.status, parsed, resp_headers
    except Exception as e:
        return -1, str(e), {}
    finally:
        conn.close()


def check_connectivity(host: str, port: int) -> bool:
    """检查端口是否可达"""
    print(f"[1/4] 检查端口连通性 {host}:{port} ...", end=" ")
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/", headers={"Accept": "*/*"})
        resp = conn.getresponse()
        conn.close()
        print(f"OK (HTTP {resp.status})")
        return True
    except Exception as e:
        print(f"FAIL — {e}")
        return False


def check_initialize(host: str, port: int, path: str) -> tuple[str | None, dict | None]:
    """初始化 MCP 会话"""
    print("[2/4] 初始化 MCP 会话 ...", end=" ")
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "shinehe-diag", "version": "1.0"},
        },
    }
    status, result, headers = _post(host, port, path, body)

    if status != 200:
        print(f"FAIL (HTTP {status})")
        return None, None

    if isinstance(result, dict):
        server_info = result.get("result", {}).get("serverInfo", {})
        print(f"OK — {server_info.get('name', '?')} v{server_info.get('version', '?')}")
        session_id = headers.get("Mcp-Session-Id", headers.get("mcp-session-id", ""))
        return session_id, result
    else:
        print(f"FAIL — unexpected response: {str(result)[:200]}")
        return None, None


def check_tools(host: str, port: int, path: str, session_id: str) -> list[str]:
    """列出可用工具"""
    print("[3/4] 获取工具列表 ...", end=" ")
    body = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
    }
    status, result, _ = _post(host, port, path, body, session_id)

    if status != 200 or not isinstance(result, dict):
        print(f"FAIL (HTTP {status})")
        return []

    tools = result.get("result", {}).get("tools", [])
    tool_names = [t.get("name", "?") for t in tools]
    print(f"OK — {len(tool_names)} 个工具")
    for name in sorted(tool_names):
        print(f"       - {name}")
    return tool_names


def check_ping(host: str, port: int, path: str, session_id: str) -> float | None:
    """测试 ping 工具"""
    print("[4/4] 测试 ping 工具 ...", end=" ")
    body = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "ping",
            "arguments": {},
        },
    }
    t0 = time.monotonic()
    status, result, _ = _post(host, port, path, body, session_id)
    elapsed_ms = (time.monotonic() - t0) * 1000

    if status != 200 or not isinstance(result, dict):
        print(f"FAIL (HTTP {status}, {elapsed_ms:.0f}ms)")
        return None

    # 提取结果
    content = result.get("result", {}).get("content", [])
    for c in content:
        if c.get("type") == "text":
            try:
                data = json.loads(c["text"])
                if data.get("ok"):
                    print(f"OK — {elapsed_ms:.0f}ms, server alive")
                    return elapsed_ms
            except (json.JSONDecodeError, KeyError):
                pass

    print(f"WARN — unexpected result: {str(result)[:200]}")
    return None


def main():
    parser = argparse.ArgumentParser(description="MCP Server 连接诊断")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", "-p", type=int, default=DEFAULT_PORT)
    parser.add_argument("--path", default=DEFAULT_PATH)
    parser.add_argument("--ping", action="store_true", help="仅测试 ping")
    parser.add_argument("--tools", action="store_true", help="仅列出工具")
    args = parser.parse_args()

    print("=" * 60)
    print("  ShineHeKnowledge MCP Server 诊断工具")
    print("=" * 60)
    print()

    # Step 1: 端口连通
    if not check_connectivity(args.host, args.port):
        print("\n[ERROR] MCP 服务端口不可达，请先启动服务:")
        print(f"  python run_mcp.py -t streamable-http --host {args.host} -p {args.port}")
        sys.exit(1)

    # Step 2: 初始化会话
    session_id, init_result = check_initialize(args.host, args.port, args.path)
    if not session_id:
        print("\n[ERROR] MCP 会话初始化失败")
        sys.exit(1)

    if args.ping:
        check_ping(args.host, args.port, args.path, session_id)
        return

    if args.tools:
        check_tools(args.host, args.port, args.path, session_id)
        return

    # Step 3: 列出工具
    tool_names = check_tools(args.host, args.port, args.path, session_id)

    # Step 4: ping
    check_ping(args.host, args.port, args.path, session_id)

    # 诊断总结
    print()
    print("=" * 60)
    has_ask = "ask" in tool_names
    has_search = "search" in tool_names
    has_ping = "ping" in tool_names

    print(f"  工具总数: {len(tool_names)}")
    print(f"  ask 工具:    {'OK' if has_ask else 'MISSING'}")
    print(f"  search 工具: {'OK' if has_search else 'MISSING'}")
    print(f"  ping 工具:   {'OK' if has_ping else 'MISSING (请重启 MCP 服务以加载新工具)'}")
    print()

    if has_ask and has_search:
        print("  [OK] MCP 服务状态正常。如果 TeleClaw 仍无法调用:")
        print("    1. 确认 TeleClaw 中已导入 MCP 配置")
        print("    2. 在 TeleClaw 设置中检查 MCP 服务器 URL 是否正确")
        print("    3. 尝试在 TeleClaw 中断开并重新连接 MCP 服务")
    else:
        print("  [WARN] 关键工具缺失，请检查 MCP 服务是否正常启动")

    print("=" * 60)


if __name__ == "__main__":
    main()
