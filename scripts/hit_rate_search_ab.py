"""Capture and compare rerank modes on the same pre-rerank candidate pool.

This is an evaluation-only tool.  It keeps the expensive retrieval capture
separate from ranking replay so a fallback comparison cannot be mistaken for
two unrelated live MCP searches.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _candidate_id(row: dict[str, Any]) -> str:
    meta = row.get("metadata") or {}
    return str(row.get("knowledge_id") or meta.get("knowledge_id") or meta.get("page_id") or row.get("id") or "")


def _score(row: dict[str, Any]) -> float:
    for key in ("final_relevance_score", "score", "rrf_score", "vector_score", "fts_score"):
        try:
            return float(row.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


def _capture_manifest(golden_path: Path) -> dict[str, Any]:
    """Use the harness fingerprint schema for offline A/B capture too."""
    from scripts.hit_rate_test_harness import _build_manifest, _manifest_fingerprint

    manifest = _build_manifest(
        golden_path=golden_path,
        out_dir=golden_path.parent,
        reuse_snapshot=False,
        read_mode="none",
        workers=1,
        case_filter=None,
    )
    manifest["run_fingerprint"] = _manifest_fingerprint(manifest)
    return manifest


def deterministic_fallback(candidates: list[dict[str, Any]], *, top_k: int) -> list[dict[str, Any]]:
    """Backward-compatible score-only helper used by narrow unit fixtures."""
    indexed = list(enumerate(candidates))
    indexed.sort(key=lambda item: (-_score(item[1]), item[0], _candidate_id(item[1])))
    return [dict(row) for _, row in indexed[:top_k]]


def capture_raw_candidates(golden_path: Path, out_path: Path, *, top_k: int) -> dict[str, Any]:
    """Run raw retrieval once per answerable case, before reranking."""
    from src.core.container import create_container
    from src.retrieval.raw_retriever import build_deterministic_query_variants

    golden = _load(golden_path)
    cases = [c for c in golden["cases"] if not c.get("expected_no_answer")]
    container = create_container()
    raw = container.search_service._get_raw_retriever()
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    for case in cases:
        query = str(case["query"])
        queries = list(raw.rewrite_query(query) or [query])
        for variant in build_deterministic_query_variants(query):
            value = variant.get("query") or ""
            if value and value not in queries:
                queries.append(value)
        t0 = time.perf_counter()
        candidates = raw.raw_retrieve(queries[:6], query, max(top_k, 20))
        candidates = raw._boost_entity_predicate_hits(query, candidates)
        rows.append({
            "case_id": case["case_id"],
            "query": query,
            "expected_knowledge_ids": list(case.get("expected_knowledge_ids") or []),
            "queries": queries[:6],
            "capture_ms": round((time.perf_counter() - t0) * 1000, 2),
            "candidates": candidates,
        })
    payload = {
        "kind": "pre_rerank_candidate_capture",
        "golden": str(golden_path),
        "run_manifest": _capture_manifest(golden_path),
        "top_k_pool": max(top_k, 20),
        "elapsed_s": round(time.perf_counter() - started, 2),
        "rows": rows,
    }
    _write(out_path, payload)
    return payload


def replay(
    capture_path: Path,
    out_path: Path,
    *,
    mode: str,
    top_k: int,
    normal_probe_timeout_s: float = 5.0,
) -> dict[str, Any]:
    """Rank captured candidates with one explicitly named mode."""
    captured = _load(capture_path)
    if captured.get("kind") != "pre_rerank_candidate_capture":
        raise ValueError("capture is not a pre_rerank_candidate_capture")
    raw = None
    if mode == "normal-rerank":
        from src.core.container import create_container
        raw = create_container().search_service._get_raw_retriever()
        original_timeout = raw._stage_timeout
        raw._stage_timeout = lambda stage: min(  # type: ignore[method-assign]
            original_timeout(stage), normal_probe_timeout_s,
        )
    rows: list[dict[str, Any]] = []
    normal_available = True
    normal_errors: list[str] = []
    # Two actual failures are enough to establish a reranker outage.  Do not
    # spend 32× the provider deadline merely to rediscover the same condition.
    max_normal_failures = 2
    started = time.perf_counter()
    for entry in captured.get("rows") or []:
        candidates = [dict(c) for c in entry.get("candidates") or [] if isinstance(c, dict)]
        t0 = time.perf_counter()
        rank_error = ""
        if mode == "normal-rerank":
            if len(normal_errors) >= max_normal_failures:
                ranked = candidates[:top_k]
                normal_available = False
                rank_error = "normal_rerank_skipped_after_outage"
            else:
                try:
                    ranked = raw.timed_rerank(str(entry.get("query") or ""), candidates, top_k)  # type: ignore[union-attr]
                except Exception as exc:  # preserve outage rather than silently relabel fallback as normal
                    ranked = candidates[:top_k]
                    normal_available = False
                    rank_error = f"{type(exc).__name__}: {exc}"
                    normal_errors.append(rank_error)
        elif mode == "deterministic-fallback":
            from src.retrieval.raw_retriever import deterministic_fallback_rank
            ranked = deterministic_fallback_rank(
                str(entry.get("query") or ""), candidates, top_k=top_k,
            )
        else:
            raise ValueError(f"unsupported mode: {mode}")
        ids = [_candidate_id(row) for row in ranked]
        expected = set(entry.get("expected_knowledge_ids") or [])
        rows.append({
            "case_id": entry.get("case_id"),
            "mode": mode,
            "top_ids": ids,
            "top1_ok": bool(ids and ids[0] in expected),
            "recall5_ok": bool(expected & set(ids[:5])),
            "rank_ms": round((time.perf_counter() - t0) * 1000, 2),
            "rank_error": rank_error,
        })
    total = max(1, len(rows))
    payload = {
        "kind": "rerank_replay",
        "capture": str(capture_path),
        "capture_run_fingerprint": (captured.get("run_manifest") or {}).get("run_fingerprint"),
        "mode": mode,
        "top_k": top_k,
        "elapsed_s": round(time.perf_counter() - started, 2),
        "normal_rerank_available": normal_available if mode == "normal-rerank" else None,
        "normal_rerank_errors": normal_errors,
        "normal_probe_timeout_s": normal_probe_timeout_s if mode == "normal-rerank" else None,
        "top1": round(sum(1 for r in rows if r["top1_ok"]) / total, 4),
        "recall5": round(sum(1 for r in rows if r["recall5_ok"]) / total, 4),
        "rows": rows,
    }
    _write(out_path, payload)
    return payload


def compare(left_path: Path, right_path: Path, out_path: Path) -> dict[str, Any]:
    left = _load(left_path)
    right = _load(right_path)
    lrows = {str(row["case_id"]): row for row in left.get("rows") or []}
    rrows = {str(row["case_id"]): row for row in right.get("rows") or []}
    all_ids = sorted(set(lrows) | set(rrows))
    rows: list[dict[str, Any]] = []
    regressions: list[str] = []
    for case_id in all_ids:
        a, b = lrows.get(case_id, {}), rrows.get(case_id, {})
        recall_regressed = bool(a.get("recall5_ok")) and not bool(b.get("recall5_ok"))
        top1_regressed = bool(a.get("top1_ok")) and not bool(b.get("top1_ok"))
        if recall_regressed or top1_regressed:
            regressions.append(case_id)
        rows.append({
            "case_id": case_id,
            "left_top_ids": a.get("top_ids") or [],
            "right_top_ids": b.get("top_ids") or [],
            "top1_regressed": top1_regressed,
            "recall5_regressed": recall_regressed,
            "left_rank_ms": a.get("rank_ms"),
            "right_rank_ms": b.get("rank_ms"),
        })
    payload = {
        "left": str(left_path),
        "right": str(right_path),
        "left_mode": left.get("mode"),
        "right_mode": right.get("mode"),
        "left_top1": left.get("top1"),
        "right_top1": right.get("top1"),
        "left_recall5": left.get("recall5"),
        "right_recall5": right.get("recall5"),
        "capture_fingerprints_match": bool(left.get("capture_run_fingerprint")) and (
            left.get("capture_run_fingerprint") == right.get("capture_run_fingerprint")
        ),
        "regressions": regressions,
        "pass": bool(left.get("normal_rerank_available", True)) and bool(left.get("capture_run_fingerprint")) and left.get("capture_run_fingerprint") == right.get("capture_run_fingerprint") and not regressions and float(right.get("top1") or 0) >= float(left.get("top1") or 0) and float(right.get("recall5") or 0) >= float(left.get("recall5") or 0),
        "rows": rows,
    }
    _write(out_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--capture-raw-candidates", metavar="OUT")
    group.add_argument("--replay", metavar="CAPTURE")
    group.add_argument("--compare", nargs=2, metavar=("NORMAL", "FALLBACK"))
    parser.add_argument("--out")
    parser.add_argument("--golden", default="evals/golden_set_hit_rate.json")
    parser.add_argument("--mode", choices=["normal-rerank", "deterministic-fallback"])
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--normal-probe-timeout-s", type=float, default=5.0)
    args = parser.parse_args()
    if args.capture_raw_candidates:
        result = capture_raw_candidates(Path(args.golden), Path(args.capture_raw_candidates), top_k=args.top_k)
    elif args.replay:
        if not args.mode:
            parser.error("--replay requires --mode")
        if not args.out:
            parser.error("--replay requires --out")
        result = replay(
            Path(args.replay), Path(args.out), mode=args.mode, top_k=args.top_k,
            normal_probe_timeout_s=args.normal_probe_timeout_s,
        )
    else:
        if not args.out:
            parser.error("--compare requires --out")
        result = compare(Path(args.compare[0]), Path(args.compare[1]), Path(args.out))
    print(json.dumps({k: result.get(k) for k in ("kind", "mode", "top1", "recall5", "pass", "regressions", "elapsed_s")}, ensure_ascii=False, indent=2))
    return 0 if result.get("pass", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
