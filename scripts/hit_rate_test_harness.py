"""ShineHeKnowledge MCP 知识命中准确率测试驱动 (SPEC v5 harness).

直连本机 127.0.0.1:9000/mcp，对 Golden Set 执行
  search(top_k=5) -> ask (optional snapshot reuse) -> deferred unique read
并保存完整 MCP 原始返回，用于核验与报告生成。

运行方式：
    python scripts/hit_rate_test_harness.py --golden <golden.json> --out <out_dir>
    python scripts/hit_rate_test_harness.py --golden ... --out ... --reuse-snapshot --read-mode unique --workers 1
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
            if envelope["data"] is None and envelope.get("error", {}).get("code") == "UNKNOWN":
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


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_revision() -> str:
    try:
        import subprocess
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _build_manifest(
    *,
    golden_path: Path,
    out_dir: Path,
    reuse_snapshot: bool,
    read_mode: str,
    workers: int,
    case_filter: list[str] | None,
) -> dict[str, Any]:
    return {
        "git_revision": _git_revision(),
        "golden_path": str(golden_path),
        "golden_sha256": _file_sha256(golden_path) if golden_path.exists() else "",
        "config_hash": os.environ.get("HIT_RATE_CONFIG_HASH", ""),
        "index_revision": os.environ.get("HIT_RATE_INDEX_REVISION", ""),
        "db_revision": os.environ.get("HIT_RATE_DB_REVISION", ""),
        "process_start_id": os.environ.get("HIT_RATE_PROCESS_START_ID", ""),
        "reuse_snapshot": bool(reuse_snapshot),
        "read_mode": read_mode,
        "workers": int(workers),
        "case_filter": list(case_filter or []),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "host": HOST,
        "port": PORT,
    }


def _manifest_compatible(prev: dict, cur: dict) -> tuple[bool, str]:
    keys = (
        "git_revision",
        "golden_sha256",
        "config_hash",
        "index_revision",
        "db_revision",
        "process_start_id",
        "reuse_snapshot",
        "read_mode",
        "workers",
    )
    for k in keys:
        if (prev.get(k) or "") != (cur.get(k) or "") and k not in ("config_hash", "index_revision", "db_revision", "process_start_id"):
            # Empty env fingerprints: only enforce when both sides non-empty.
            if k in ("config_hash", "index_revision", "db_revision", "process_start_id"):
                if prev.get(k) and cur.get(k) and prev.get(k) != cur.get(k):
                    return False, f"manifest_mismatch:{k}"
            else:
                return False, f"manifest_mismatch:{k}"
        if k in ("git_revision", "golden_sha256", "reuse_snapshot", "read_mode", "workers"):
            if prev.get(k) != cur.get(k):
                return False, f"manifest_mismatch:{k}"
    return True, ""


def _extract_snapshot_id(search_envelope: dict) -> str | None:
    """Pull evidence_snapshot_id from search ok() meta or data."""
    if not isinstance(search_envelope, dict):
        return None
    meta = search_envelope.get("meta") or {}
    if isinstance(meta, dict) and meta.get("evidence_snapshot_id"):
        return str(meta["evidence_snapshot_id"])
    data = search_envelope.get("data")
    if isinstance(data, dict) and data.get("evidence_snapshot_id"):
        return str(data["evidence_snapshot_id"])
    return None


def _run_one_case(
    case: dict,
    *,
    reuse_snapshot: bool,
    read_mode: str,
    host: str,
    port: int,
) -> dict:
    """Run one Golden case with a fresh MCP session (worker-safe)."""
    client = MCPClient(host=host, port=port)
    if not client.initialize():
        raise RuntimeError(f"MCP initialize failed for {case.get('case_id')}")

    cid = case["case_id"]
    query = case["query"]

    search = client.call("search", {"query": query, "top_k": 5}, timeout=180)
    search_ms = search["latency_ms"]
    top_candidate_id = None
    cand_list: list = []
    env = search["envelope"]
    snap_id = None
    if env.get("ok"):
        data = env.get("data")
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
        snap_id = _extract_snapshot_id(env)
        if snap_id is None and isinstance(env.get("meta"), dict):
            snap_id = env["meta"].get("evidence_snapshot_id")

    # ask with optional snapshot reuse
    ask_args: dict[str, Any] = {"question": query}
    if reuse_snapshot and snap_id:
        ask_args["evidence_snapshot_id"] = snap_id
    ask = client.call("ask", ask_args, timeout=180)
    ask_ms = ask["latency_ms"]
    ask_data = (ask["envelope"] or {}).get("data") or {}
    snapshot_reused = bool(ask_data.get("snapshot_reused")) if isinstance(ask_data, dict) else False
    retrieval_count = ask_data.get("retrieval_count") if isinstance(ask_data, dict) else None
    if retrieval_count is None:
        retrieval_count = 0 if snapshot_reused else 1

    # read modes
    read_resp = None
    read_ms = 0.0
    if read_mode == "each" and top_candidate_id:
        read_resp = client.call("read", {"knowledge_id": top_candidate_id}, timeout=120)
        read_ms = read_resp["latency_ms"]

    result = {
        "case": case,
        "search": {"envelope": search["envelope"], "latency_ms": search_ms},
        "read": (
            {
                "envelope": read_resp["envelope"],
                "latency_ms": read_ms,
                "knowledge_id": top_candidate_id,
            }
            if read_resp
            else None
        ),
        "ask": {"envelope": ask["envelope"], "latency_ms": ask_ms},
        "top_candidate_id": top_candidate_id,
        "candidates": [
            {
                "knowledge_id": c.get("knowledge_id") or c.get("id"),
                "passage_id": c.get("passage_id"),
                "title": c.get("title"),
                "score": c.get("score"),
                "final_relevance_score": c.get("final_relevance_score"),
                "text": c.get("text"),
            }
            for c in cand_list
            if isinstance(c, dict)
        ],
        "search_ms": search_ms,
        "ask_ms": ask_ms,
        "read_ms": read_ms,
        "snapshot_reused": snapshot_reused,
        "snapshot_id": snap_id,
        "retrieval_count": retrieval_count,
        "answer_validation_decision": (
            ask_data.get("answer_validation_decision") if isinstance(ask_data, dict) else None
        ),
        "retrieval_decision": (
            ask_data.get("retrieval_decision") if isinstance(ask_data, dict) else None
        ),
    }
    return result


def run(
    golden_path: Path,
    out_dir: Path,
    *,
    resume: bool = False,
    reuse_snapshot: bool = True,
    read_mode: str = "unique",
    workers: int = 1,
    case_ids: list[str] | None = None,
    host: str = HOST,
    port: int = PORT,
    write_manifest: bool = True,
):
    out_dir.mkdir(parents=True, exist_ok=True)
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    cases = golden["cases"] if isinstance(golden, dict) and "cases" in golden else golden
    if case_ids:
        want = set(case_ids)
        cases = [c for c in cases if c.get("case_id") in want]

    manifest = _build_manifest(
        golden_path=golden_path,
        out_dir=out_dir,
        reuse_snapshot=reuse_snapshot,
        read_mode=read_mode,
        workers=workers,
        case_filter=case_ids,
    )
    manifest_path = out_dir / "manifest.json"
    if resume and manifest_path.exists():
        prev = json.loads(manifest_path.read_text(encoding="utf-8"))
        ok_m, reason = _manifest_compatible(prev, manifest)
        if not ok_m:
            raise RuntimeError(
                f"--resume rejected: {reason}. "
                "Fingerprints must match exactly; refuse silent reuse of old interactions."
            )
    if write_manifest:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # capabilities via a short-lived client
    boot = MCPClient(host=host, port=port)
    print("[step] initialize MCP session ...", end=" ", flush=True)
    if not boot.initialize():
        raise RuntimeError("MCP initialize failed")
    print(f"OK session={boot.session_id}")
    ping = boot.call("ping", {})
    caps = boot.call("kb_capabilities", {})
    (out_dir / "00_capabilities.json").write_text(
        json.dumps({"ping": ping, "kb_capabilities": caps}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[step] ping ok={ping['envelope'].get('ok')} caps ok={caps['envelope'].get('ok')}")
    print(
        f"[step] reuse_snapshot={reuse_snapshot} read_mode={read_mode} workers={workers}",
        flush=True,
    )

    results: list[dict] = []
    skipped = 0
    pending: list[dict] = []
    for case in cases:
        cid = case["case_id"]
        case_path = out_dir / f"{cid}.json"
        if resume and case_path.exists():
            try:
                prev = json.loads(case_path.read_text(encoding="utf-8"))
                results.append(prev)
                skipped += 1
                print(f"[resume] {cid} SKIP", flush=True)
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"[resume] {cid} load failed ({exc}); re-run", flush=True)
        pending.append(case)

    t_all0 = time.perf_counter()
    workers = max(1, int(workers))

    def _handle(result: dict, i: int, total: int) -> None:
        cid = result["case"]["case_id"]
        case_path = out_dir / f"{cid}.json"
        # Atomic write via temp then replace
        tmp = case_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(case_path)
        results.append(result)
        print(
            f"[{i}/{total}] {cid} top={result.get('top_candidate_id')} "
            f"search={result.get('search_ms', 0):.0f}ms "
            f"ask={result.get('ask_ms', 0):.0f}ms "
            f"reused={result.get('snapshot_reused')} "
            f"retrieval_count={result.get('retrieval_count')}",
            flush=True,
        )

    if workers == 1:
        for i, case in enumerate(pending, 1):
            r = _run_one_case(
                case,
                reuse_snapshot=reuse_snapshot,
                read_mode=read_mode,
                host=host,
                port=port,
            )
            _handle(r, i, len(pending))
    else:
        # Each worker: independent MCP session (no shared client).
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(
                    _run_one_case,
                    case,
                    reuse_snapshot=reuse_snapshot,
                    read_mode=read_mode,
                    host=host,
                    port=port,
                ): case
                for case in pending
            }
            done_i = 0
            for fut in as_completed(futs):
                done_i += 1
                case = futs[fut]
                try:
                    r = fut.result()
                except Exception as exc:  # noqa: BLE001
                    r = {
                        "case": case,
                        "error": str(exc),
                        "search": {"envelope": {"ok": False}, "latency_ms": 0},
                        "ask": {"envelope": {"ok": False}, "latency_ms": 0},
                        "read": None,
                        "top_candidate_id": None,
                        "candidates": [],
                        "search_ms": 0,
                        "ask_ms": 0,
                        "read_ms": 0,
                        "snapshot_reused": False,
                        "retrieval_count": 0,
                    }
                _handle(r, done_i, len(pending))

    # Deferred unique reads
    read_map: dict[str, Any] = {}
    if read_mode == "unique":
        kids: list[str] = []
        for r in results:
            kid = r.get("top_candidate_id")
            # Prefer source knowledge ids from ask if present.
            ask_data = ((r.get("ask") or {}).get("envelope") or {}).get("data") or {}
            if isinstance(ask_data, dict):
                for s in ask_data.get("sources") or []:
                    if isinstance(s, dict) and s.get("knowledge_id"):
                        kids.append(str(s["knowledge_id"]))
            if kid:
                kids.append(str(kid))
        unique_kids = sorted(set(kids))
        print(f"[step] unique read for {len(unique_kids)} knowledge ids", flush=True)
        reader = MCPClient(host=host, port=port)
        if reader.initialize():
            for kid in unique_kids:
                resp = reader.call("read", {"knowledge_id": kid}, timeout=120)
                read_map[kid] = {
                    "envelope": resp["envelope"],
                    "latency_ms": resp["latency_ms"],
                    "knowledge_id": kid,
                }
        (out_dir / "unique_reads.json").write_text(
            json.dumps(read_map, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    total_s = time.perf_counter() - t_all0
    summary = {
        "total": len(results),
        "skipped_resume": skipped,
        "reuse_snapshot": reuse_snapshot,
        "read_mode": read_mode,
        "workers": workers,
        "elapsed_s": round(total_s, 2),
        "search_ms_sum": round(sum(float(r.get("search_ms") or 0) for r in results), 2),
        "ask_ms_sum": round(sum(float(r.get("ask_ms") or 0) for r in results), 2),
        "read_ms_sum": round(sum(float(r.get("read_ms") or 0) for r in results), 2),
        "snapshot_reuse_hits": sum(1 for r in results if r.get("snapshot_reused")),
        "retrieval_count_sum": sum(int(r.get("retrieval_count") or 0) for r in results),
        "unique_reads": len(read_map),
        "cases": [
            {
                "case_id": r["case"]["case_id"],
                "category": r["case"].get("category"),
                "query": r["case"]["query"],
                "top_candidate_id": r.get("top_candidate_id"),
                "search_ok": (r.get("search") or {}).get("envelope", {}).get("ok"),
                "ask_ok": (r.get("ask") or {}).get("envelope", {}).get("ok"),
                "search_ms": r.get("search_ms"),
                "ask_ms": r.get("ask_ms"),
                "read_ms": r.get("read_ms"),
                "snapshot_reused": r.get("snapshot_reused"),
                "retrieval_count": r.get("retrieval_count"),
                "answer_validation_decision": r.get("answer_validation_decision"),
                "retrieval_decision": r.get("retrieval_decision"),
            }
            for r in results
        ],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if write_manifest:
        manifest["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        manifest["elapsed_s"] = summary["elapsed_s"]
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        f"\n[done] {len(results)} cases (skipped_resume={skipped}) "
        f"elapsed={total_s:.1f}s -> {out_dir}",
        flush=True,
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--resume",
        action="store_true",
        help="Skip cases that already have <CaseID>.json when manifest fingerprints match",
    )
    ap.add_argument(
        "--reuse-snapshot",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass evidence_snapshot_id from search to ask (default: true)",
    )
    ap.add_argument(
        "--read-mode",
        choices=["none", "unique", "each"],
        default="unique",
        help="read strategy (default unique)",
    )
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument(
        "--cases",
        default="",
        help="Comma-separated case_id filter (e.g. KB-001,KB-017)",
    )
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--manifest", action=argparse.BooleanOptionalAction, default=True)
    args = ap.parse_args()
    case_ids = [c.strip() for c in (args.cases or "").split(",") if c.strip()] or None
    run(
        Path(args.golden),
        Path(args.out),
        resume=bool(args.resume),
        reuse_snapshot=bool(args.reuse_snapshot),
        read_mode=str(args.read_mode),
        workers=int(args.workers),
        case_ids=case_ids,
        host=str(args.host),
        port=int(args.port),
        write_manifest=bool(args.manifest),
    )
