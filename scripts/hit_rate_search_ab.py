"""Search-only A/B for 32 answerable cases (SPEC v6 §5.2).

Baseline: current MCP search (with circuit/rerank as live).
Writes Top-1 / Recall@5 vs expected knowledge IDs for each case.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from scripts.hit_rate_test_harness import MCPClient


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default="evals/golden_set_hit_rate.json")
    ap.add_argument("--out", default="artifacts/hit_rate_test_v6/rerank_ab")
    ap.add_argument("--label", default="live")
    args = ap.parse_args()
    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    cases = [c for c in golden["cases"] if not c.get("expected_no_answer")]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    client = MCPClient()
    if not client.initialize():
        print("MCP init failed")
        return 1
    client.call("ping", {})

    rows = []
    t0 = time.time()
    for i, case in enumerate(cases, 1):
        cid = case["case_id"]
        q = case["query"]
        expected = set(case.get("expected_knowledge_ids") or [])
        resp = client.call("search", {"query": q, "top_k": 5}, timeout=180)
        env = resp.get("envelope") or {}
        data = env.get("data") if env.get("ok") else None
        if not isinstance(data, list):
            data = []
        top_ids = [str(c.get("knowledge_id") or "") for c in data[:5] if isinstance(c, dict)]
        top1 = top_ids[0] if top_ids else ""
        top1_ok = bool(top1 and top1 in expected) if expected else False
        recall = bool(expected & set(top_ids)) if expected else False
        row = {
            "case_id": cid,
            "label": args.label,
            "query": q,
            "expected_knowledge_ids": list(expected),
            "top_ids": top_ids,
            "top1_ok": top1_ok,
            "recall5_ok": recall,
            "search_ms": resp.get("latency_ms"),
            "n_results": len(data),
            "snapshot_fingerprint": env.get("snapshot_fingerprint")
            if isinstance(env, dict)
            else None,
        }
        # envelope may put fingerprint at top-level of tool response
        if isinstance(env, dict) and not row["snapshot_fingerprint"]:
            row["snapshot_fingerprint"] = env.get("snapshot_fingerprint")
        rows.append(row)
        print(
            f"[{i}/{len(cases)}] {cid} top1={top1_ok} recall5={recall} "
            f"ms={resp.get('latency_ms')}"
        )
        (out / f"{cid}.json").write_text(
            json.dumps({"case": case, "search": resp, "row": row}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    n = max(1, len(rows))
    summary = {
        "label": args.label,
        "n": len(rows),
        "top1": round(sum(1 for r in rows if r["top1_ok"]) / n, 4),
        "recall5": round(sum(1 for r in rows if r["recall5_ok"]) / n, 4),
        "elapsed_s": round(time.time() - t0, 2),
        "rows": rows,
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: summary[k] for k in ("label", "n", "top1", "recall5", "elapsed_s")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
