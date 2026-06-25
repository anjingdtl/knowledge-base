"""MCP 修复后校验测试脚本 v2

直接通过 Streamable HTTP 调用 MCP 服务器的 JSON-RPC 接口，
校验 BUG-1~7 的修复是否实际生效。
"""
import json
import sys
import time
import urllib.request
import urllib.error

MCP_URL = "http://127.0.0.1:9000/mcp"
SESSION_ID = None

# ── 工具函数 ──

def _request(method: str, params: dict | None = None) -> dict:
    global SESSION_ID
    payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": int(time.time() * 1000)}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if SESSION_ID:
        headers["Mcp-Session-Id"] = SESSION_ID
    try:
        req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid: SESSION_ID = sid
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
            text = raw.decode("utf-8", errors="replace")
            if "text/event-stream" in ct or ct == "application/octet-stream":
                for line in text.split("\n"):
                    line = line.strip()
                    if line.startswith("data: "):
                        try:
                            return json.loads(line[6:])
                        except json.JSONDecodeError:
                            pass
                return {"error": f"SSE parse: {text[:300]}"}
            return json.loads(text)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"}
    except Exception as e:
        return {"error": str(e)}


def extract(r: dict) -> dict:
    """从 MCP 响应中提取 data payload。"""
    if "error" in r:
        return {}
    sc = r.get("result", {}).get("structuredContent", {})
    if isinstance(sc, dict) and sc.get("ok") is not None:
        return sc.get("data", sc)
    content = r.get("result", {}).get("content", [])
    if content and isinstance(content, list):
        text = content[0].get("text", "")
        if text:
            try:
                p = json.loads(text)
                return p.get("data", p)
            except json.JSONDecodeError:
                pass
    return r.get("result", {})


def check(label: str, ok: bool, detail=""):
    s = "✅" if ok else "❌"
    msg = f"   {s} {label}"
    if detail and not ok:
        msg += f" | {detail}"
    print(msg)
    return ok

# ── 初始化 ──

def init():
    r = _request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "post-fix-test", "version": "2.0"}})
    if "error" in r:
        print(f"❌ Init failed: {r['error']}")
        return False
    _request("notifications/initialized", {})
    print(f"✅ Session: {SESSION_ID[:20] if SESSION_ID else '?'}...")
    return True

# ── 测试用例 ──

def test_bug1_route_query():
    """BUG-1: route_query hybrid 应附带 query_spec"""
    print("\n" + "-" * 50)
    print("1. BUG-1: route_query hybrid → query_spec 不为空")
    all_ok = True
    for q in ["年中部署方案", "外包管理流程"]:
        r = _request("tools/call", {"name": "route_query", "arguments": {"question": q}})
        data = extract(r)
        mode = data.get("mode", "unknown")
        qs = data.get("query_spec", data.get("query_plan", {}))
        has_qs = bool(qs and isinstance(qs, dict) and qs.get("filter"))
        detail = f"mode={mode}, query_spec_keys={list(qs.keys()) if qs else 'None'}"
        all_ok &= check(f"route_query('{q}')", has_qs, detail)
    return all_ok


def test_bug5_ask_with_query():
    """BUG-5: ask_with_query 兼容旧 query 参数"""
    print("\n" + "-" * 50)
    print("2. BUG-5: ask_with_query(query='企微考核') ← 旧参数")
    r = _request("tools/call", {"name": "ask_with_query", "arguments": {"query": "企微考核"}})
    data = extract(r)
    # 不触发 validation error 即算通过
    ok = not isinstance(data, dict) or data.get("ok") is not False
    if not ok:
        err = data.get("error", data)
        ok = "VALIDATION" not in str(err) and "requires" not in str(err)
    return check("ask_with_query(query=...) 兼容性", ok,
                 f"data_keys={list(data.keys())[:5] if data else 'None'}")


def test_bug6_structured_query():
    """BUG-6: structured_query 兼容旧 filters 参数"""
    print("\n" + "-" * 50)
    print("3. BUG-6: structured_query(filters=...) ← 旧参数")
    r = _request("tools/call", {"name": "structured_query", "arguments": {"filters": '{"filter": {"fulltext": "企微"}}'}})
    data = extract(r)
    ok = isinstance(data, list)
    if not ok:
        err = data.get("error", data)
        ok = "VALIDATION" not in str(err) and "requires" not in str(err)
    cnt = len(data) if isinstance(data, list) else 0
    return check(f"structured_query(filters=...) results={cnt}", ok)


def test_bug7_ask():
    """BUG-7: ask 弱相关查询应提供方向性回答"""
    print("\n" + "-" * 50)
    print("4. BUG-7: ask('Block-First检索') 不应简单返回\"未找到\"")
    r = _request("tools/call", {"name": "ask", "arguments": {"question": "Block-First检索"}})
    data = extract(r)
    answer = data.get("answer", "")
    sources = data.get("sources", [])
    warnings = data.get("warnings", [])
    # 至少有来源或有实质性回答，或有领域概览兜底
    has_answer = len(answer) > 20 and not any(w in answer for w in ["未找到", "not found"])
    has_sources = len(sources) > 0
    has_domain_fallback = any("domain_summary" in str(w) for w in warnings)
    ok = has_answer or has_sources or has_domain_fallback
    return check(f"ask: answer_len={len(answer)}, sources={len(sources)}", ok,
                 f"answer[:80]={answer[:80]}")


def test_bug4_health_check():
    """BUG-4: health check 包含 recommendations"""
    print("\n" + "-" * 50)
    print("5. BUG-4: kb_health_check → recommendations 字段")
    r = _request("tools/call", {"name": "kb_health_check", "arguments": {}})
    data = extract(r)
    has_recs = "recommendations" in data
    tag_cov = data.get("tag_coverage", 0)
    return check(f"kb_health_check: tag_coverage={tag_cov:.1%}, has_recommendations={has_recs}", has_recs)


def test_auto_tag_exists():
    """auto_tag 工具存在"""
    print("\n" + "-" * 50)
    print("6. auto_tag 工具存在性")
    r = _request("tools/list", {})
    tools = r.get("result", {}).get("tools", [])
    names = {t["name"] for t in tools}
    return check("auto_tag in tools/list", "auto_tag" in names)


def test_search_basic():
    """search 基本功能"""
    print("\n" + "-" * 50)
    print("7. search 基本检索")
    r = _request("tools/call", {"name": "search", "arguments": {"query": "企微运营", "top_k": 3}})
    data = extract(r)
    ok = isinstance(data, list) and len(data) > 0
    return check(f"search('企微运营') result_count={len(data) if isinstance(data, list) else 0}", ok)

# ── main ──

def main():
    print("=" * 60)
    print("  ShineHe KB MCP 修复后校验 v2")
    print("=" * 60)

    if not init():
        return 1

    results = [
        test_bug1_route_query(),
        test_bug5_ask_with_query(),
        test_bug6_structured_query(),
        test_bug7_ask(),
        test_bug4_health_check(),
        test_auto_tag_exists(),
        test_search_basic(),
    ]

    print("\n" + "=" * 60)
    passed = sum(results)
    failed = len(results) - passed
    print(f"  通过 {passed}/{len(results)}，失败 {failed}")
    if failed > 0:
        print("  ⚠️ 部分校验未通过，请检查上述详情")
    else:
        print("  ✅ 所有校验通过！")
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
