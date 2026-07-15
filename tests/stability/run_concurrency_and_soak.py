"""Phase 8 concurrency + soak harness (temporary DB, no formal data/kb.db).

Concurrency: threaded calls against in-process MCP tool functions.
Soak: default 2 hours; set SOAK_SECONDS env to shorten for dry runs.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "stability-repair"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def setup_temp_env():
    import sys

    sys.path.insert(0, str(ROOT))
    tmp = tempfile.mkdtemp(prefix="shinehe-soak-")
    db_path = os.path.join(tmp, "test.db")
    from src.services.db import Database
    from src.utils.config import Config

    Config.load()
    Config.set("storage.data_dir", tmp)
    Config.set("storage.db_name", "test.db")
    Config.set("knowledge_workflow.wiki_dir", os.path.join(tmp, "wiki"))
    Config.set("security.allowed_ingest_dirs", [tmp, tempfile.gettempdir()])
    Config.set("rag.ask.total_timeout", 2)
    Config.set("mcp.enable_legacy_aliases", False)
    Database._instance = None
    Database.connect(db_path)
    # seed a few docs
    import uuid
    from datetime import datetime as dt

    for i in range(5):
        kid = str(uuid.uuid4())
        Database.insert_knowledge({
            "id": kid,
            "title": f"Soak Doc {i}",
            "content": f"内容 {i} 企微 检索 60 米 测试",
            "source_type": "manual",
            "source_path": "",
            "file_type": "md",
            "file_size": 0,
            "content_hash": kid,
            "file_created_at": "",
            "file_modified_at": "",
            "tags": '["soak"]',
            "version": 1,
            "created_at": dt.now().isoformat(),
            "updated_at": dt.now().isoformat(),
        })
    return tmp, db_path


def patch_container():
    from types import SimpleNamespace

    import src.mcp.tools.graph as graph
    import src.mcp.tools.ingest as ingest
    import src.mcp.tools.retrieval as retrieval
    from src.services.db import Database
    from src.services.graph_backend.sqlite_backend import SQLiteGraphBackend

    c = SimpleNamespace(
        db=Database,
        graph_backend=SQLiteGraphBackend(db=Database),
        llm=None,
        search_service=None,
        rag_pipeline=SimpleNamespace(query=lambda q, timeout=None: {
            "answer": "", "sources": [], "route": {"mode": "hybrid"},
            "warnings": [], "query_plan": {}, "block_contexts": {},
            "wiki_context": "", "trace_id": "", "answer_mode": "no_answer",
        }),
    )
    retrieval._get_container = lambda: c
    ingest._get_container = lambda: c
    graph._get_container = lambda: c
    return c


def run_concurrency():
    from src.mcp.tools.retrieval import search

    results = {"search": {}, "errors": []}
    for conc in (1, 5, 10):
        latencies = []
        errors = 0
        db_locked = 0

        def one(_i):
            t0 = time.perf_counter()
            try:
                r = search(query="企微", limit=5)
                ok = bool(r.get("ok"))
            except Exception as e:
                return time.perf_counter() - t0, False, str(e)
            return time.perf_counter() - t0, ok, None

        with ThreadPoolExecutor(max_workers=conc) as pool:
            futs = [pool.submit(one, i) for i in range(100 if conc <= 10 else 50)]
            for f in as_completed(futs):
                lat, ok, err = f.result()
                latencies.append(lat * 1000)
                if not ok:
                    errors += 1
                    if err and "locked" in err.lower():
                        db_locked += 1
                    if err:
                        results["errors"].append(err)
        latencies.sort()
        def pct(p):
            if not latencies:
                return None
            idx = min(len(latencies) - 1, int(round((p / 100) * (len(latencies) - 1))))
            return round(latencies[idx], 2)
        results["search"][str(conc)] = {
            "n": len(latencies),
            "errors": errors,
            "db_locked": db_locked,
            "p50_ms": pct(50),
            "p95_ms": pct(95),
            "p99_ms": pct(99),
            "max_ms": round(max(latencies), 2) if latencies else None,
            "qps": round(len(latencies) / max(sum(latencies) / 1000, 0.001), 2),
        }
    # light ask concurrency (mocked pipeline)
    from src.mcp.tools.retrieval import ask
    ask_err = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = [pool.submit(lambda: ask(question="测试问题")) for _ in range(30)]
        for f in as_completed(futs):
            try:
                r = f.result()
                if not r.get("ok"):
                    ask_err += 1
            except Exception:
                ask_err += 1
    results["ask_concurrency_5"] = {"n": 30, "errors": ask_err}
    return results


def run_soak(seconds: float):
    from src.mcp.tools.ingest import list_knowledge, tags
    from src.mcp.tools.retrieval import ask, ping, search

    start = time.time()
    samples = []
    errors = []
    thread_baseline = threading.active_count()
    ops = ["ping", "search", "ask", "list_knowledge", "tags"]
    idx = 0
    while time.time() - start < seconds:
        op = ops[idx % len(ops)]
        idx += 1
        t0 = time.perf_counter()
        try:
            if op == "ping":
                r = ping()
            elif op == "search":
                r = search(query="检索", limit=3)
            elif op == "ask":
                r = ask(question="总结主要问题")
            elif op == "list_knowledge":
                r = list_knowledge(limit=5)
            else:
                r = tags(limit=10, offset=0)
            ok = bool(r.get("ok", True))
            if not ok:
                errors.append({"op": op, "result": r, "t": utc_now()})
        except Exception as e:
            ok = False
            errors.append({"op": op, "error": str(e), "t": utc_now()})
        lat = (time.perf_counter() - t0) * 1000
        if idx % 5 == 0 or time.time() - start < 2:
            samples.append({
                "t": utc_now(),
                "elapsed_s": round(time.time() - start, 1),
                "op": op,
                "latency_ms": round(lat, 2),
                "ok": ok,
                "threads": threading.active_count(),
                "thread_delta": threading.active_count() - thread_baseline,
            })
        # ~1 op per second cadence for long soak
        time.sleep(max(0.0, 1.0 - (time.perf_counter() - t0)))

    lats = [s["latency_ms"] for s in samples]
    lats_sorted = sorted(lats)
    def pct(p):
        if not lats_sorted:
            return None
        i = min(len(lats_sorted) - 1, int(round((p / 100) * (len(lats_sorted) - 1))))
        return lats_sorted[i]
    return {
        "duration_s": round(time.time() - start, 1),
        "samples": len(samples),
        "errors": len(errors),
        "error_examples": errors[:10],
        "p50_ms": pct(50),
        "p95_ms": pct(95),
        "p99_ms": pct(99),
        "thread_baseline": thread_baseline,
        "thread_final": threading.active_count(),
        "thread_delta": threading.active_count() - thread_baseline,
        "resource_samples": samples[:: max(1, len(samples) // 50)] if samples else [],
    }


def main():
    ART.mkdir(parents=True, exist_ok=True)
    tmp, db_path = setup_temp_env()
    patch_container()
    formal = ROOT / "data" / "kb.db"
    formal_size_before = formal.stat().st_size if formal.exists() else None

    conc = run_concurrency()
    (ART / "concurrency-results.json").write_text(
        json.dumps({"ts": utc_now(), "tmp_db": db_path, **conc}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    soak_seconds = float(os.environ.get("SOAK_SECONDS", str(2 * 3600)))
    soak = run_soak(soak_seconds)
    formal_size_after = formal.stat().st_size if formal.exists() else None
    soak["formal_db_size_before"] = formal_size_before
    soak["formal_db_size_after"] = formal_size_after
    soak["formal_db_unchanged"] = formal_size_before == formal_size_after
    (ART / "soak-results.json").write_text(
        json.dumps({"ts": utc_now(), **soak}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"concurrency": conc["search"], "soak_errors": soak["errors"], "soak_p95": soak["p95_ms"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
