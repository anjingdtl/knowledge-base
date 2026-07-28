"""ShineHeKnowledge MCP 知识命中准确率测试驱动

直连本机 127.0.0.1:9000/mcp，对 Golden Set 逐条执行
  search(top_k=5) -> read -> ask
并保存完整 MCP 原始返回，用于核验与报告生成。

运行方式：
    python scripts/hit_rate_test_harness.py --golden <golden.json> --out <out_dir>
"""
from __future__ import annotations

import argparse
import http.client
import json
import time
from pathlib import Path
from typing import Any

HOST = "127.0.0.1"
PORT = 9000
PATH = "/mcp"


class MCPClient:
    def __init__(self, host: str = HOST, port: int = PORT, path: str = PATH):
        self.host = host
        self.port = port
        self.path = path
        self.session_id: str | None = None
        self.msg_id = 0

    def _post(self, body: dict, timeout: int = 180) -> tuple[int, Any, dict]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            conn.request("POST", self.path, body=json.dumps(body), headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8", errors="replace")
            resp_headers = dict(resp.getheaders())
            parsed = None
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                # SSE: locate first data: line
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
                "clientInfo": {"name": "hit-rate-tester", "version": "1.0"},
            },
        }
        status, result, headers = self._post(body, timeout=60)
        if status == 200 and isinstance(result, dict):
            self.session_id = headers.get("Mcp-Session-Id", headers.get("mcp-session-id", ""))
            return bool(self.session_id)
        return False

    def call(self, name: str, arguments: dict | None = None, timeout: int = 180) -> dict:
        self.msg_id += 1
        body = {
            "jsonrpc": "2.0",
            "id": self.msg_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        t0 = time.perf_counter()
        status, result, _ = self._post(body, timeout=timeout)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        envelope: dict = {"ok": False, "data": None, "error": {"code": "UNKNOWN", "message": ""}}
        if status == 200 and isinstance(result, dict):
            content = result.get("result", {}).get("content", [])
            for c in content:
                if c.get("type") == "text":
                    try:
                        envelope = json.loads(c["text"])
                    except json.JSONDecodeError:
                        envelope = {
                            "ok": False,
                            "data": None,
                            "error": {"code": "PARSE_ERROR", "message": c.get("text", "")[:500]},
                        }
                    break
            # If no text content, surface raw result for debugging
            if envelope["data"] is None and envelope["error"]["code"] == "UNKNOWN":
                envelope = {
                    "ok": False,
                    "data": None,
                    "error": {"code": "NO_TEXT_CONTENT", "message": json.dumps(result)[:500]},
                }
        else:
            envelope = {
                "ok": False,
                "data": None,
                "error": {"code": f"HTTP_{status}", "message": str(result)[:500]},
            }
        return {
            "envelope": envelope,
            "latency_ms": round(elapsed_ms, 2),
            "status": status,
            "raw_result": result if isinstance(result, dict) else None,
        }


def run(golden_path: Path, out_dir: Path, *, resume: bool = False):
    out_dir.mkdir(parents=True, exist_ok=True)
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    cases = golden["cases"] if isinstance(golden, dict) and "cases" in golden else golden
    client = MCPClient()
    print("[step] initialize MCP session ...", end=" ", flush=True)
    if not client.initialize():
        raise RuntimeError("MCP initialize failed")
    print(f"OK session={client.session_id}")

    # 1) ping + kb_capabilities (refresh each run; safe to overwrite)
    ping = client.call("ping", {})
    caps = client.call("kb_capabilities", {})
    (out_dir / "00_capabilities.json").write_text(
        json.dumps({"ping": ping, "kb_capabilities": caps}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[step] ping ok={ping['envelope'].get('ok')} caps ok={caps['envelope'].get('ok')}")

    results = []
    skipped = 0
    for i, case in enumerate(cases, 1):
        cid = case["case_id"]
        query = case["query"]
        case_path = out_dir / f"{cid}.json"
        if resume and case_path.exists():
            try:
                prev = json.loads(case_path.read_text(encoding="utf-8"))
                results.append(prev)
                skipped += 1
                print(
                    f"\n[{i}/{len(cases)}] {cid} SKIP resume "
                    f"top={prev.get('top_candidate_id')}",
                    flush=True,
                )
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"\n[{i}/{len(cases)}] {cid} resume-load failed ({exc}); re-run", flush=True)

        print(f"\n[{i}/{len(cases)}] {cid} category={case.get('category')} q={query[:50]}", flush=True)

        # search top_k=5
        search = client.call("search", {"query": query, "top_k": 5}, timeout=180)
        top_candidate_id = None
        cand_list = []
        env = search["envelope"]
        if env.get("ok"):
            data = env.get("data")
            # data may be a bare list (observed) or a dict with results/items
            if isinstance(data, list):
                cand_list = data
            elif isinstance(data, dict):
                if isinstance(data.get("results"), list):
                    cand_list = data["results"]
                elif isinstance(data.get("items"), list):
                    cand_list = data["items"]
            if cand_list:
                first = cand_list[0]
                top_candidate_id = (
                    first.get("knowledge_id")
                    or first.get("id")
                    or first.get("item_id")
                    or first.get("source_id")
                )

        # read top candidate
        read_resp = None
        if top_candidate_id:
            read_resp = client.call("read", {"knowledge_id": top_candidate_id}, timeout=120)

        # ask
        ask = client.call("ask", {"question": query}, timeout=180)

        result = {
            "case": case,
            "search": {"envelope": search["envelope"], "latency_ms": search["latency_ms"]},
            "read": (
                {"envelope": read_resp["envelope"], "latency_ms": read_resp["latency_ms"], "knowledge_id": top_candidate_id}
                if read_resp
                else None
            ),
            "ask": {"envelope": ask["envelope"], "latency_ms": ask["latency_ms"]},
            "top_candidate_id": top_candidate_id,
            "candidates": [
                {
                    "knowledge_id": c.get("knowledge_id") or c.get("id"),
                    "title": c.get("title"),
                    "score": c.get("score"),
                    "final_relevance_score": c.get("final_relevance_score"),
                    "text": c.get("text"),
                }
                for c in cand_list if isinstance(c, dict)
            ],
        }
        results.append(result)
        case_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"   top={top_candidate_id} search_t={search['latency_ms']:.0f}ms "
            f"ask_t={ask['latency_ms']:.0f}ms",
            flush=True,
        )

    summary = {
        "session_id": client.session_id,
        "total": len(results),
        "skipped_resume": skipped,
        "cases": [
            {
                "case_id": r["case"]["case_id"],
                "category": r["case"].get("category"),
                "query": r["case"]["query"],
                "top_candidate_id": r["top_candidate_id"],
                "search_ok": r["search"]["envelope"].get("ok"),
                "ask_ok": r["ask"]["envelope"].get("ok"),
            }
            for r in results
        ],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[done] {len(results)} cases (skipped_resume={skipped}) -> {out_dir}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--resume",
        action="store_true",
        help="Skip cases that already have <CaseID>.json in --out",
    )
    args = ap.parse_args()
    run(Path(args.golden), Path(args.out), resume=bool(args.resume))
