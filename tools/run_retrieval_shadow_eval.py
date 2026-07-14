#!/usr/bin/env python3
"""Aggregate retrieval shadow cutover report (legacy primary vs unified).

Usage:
  python tools/run_retrieval_shadow_eval.py
  python tools/run_retrieval_shadow_eval.py --out evals/reports/retrieval-shadow-YYYY-MM-DD.json

Scenarios are synthetic (mocked channels) covering Spec §7.2 WP1-T5 cases.
Does not log full document text.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.search_execution import SearchExecution  # noqa: E402
from src.retrieval.orchestrator import RetrievalOrchestrator, resolve_orchestrator_mode  # noqa: E402
from src.retrieval.shadow_comparator import compare_executions, meets_cutover_gates  # noqa: E402
from src.services.search_service import SearchService  # noqa: E402


def _cfg(mode: str = "shadow", *, verified: bool = False) -> Mock:
    data = {
        "retrieval.orchestrator": mode,
        "rag.enable_query_rewriting": False,
        "rag.enable_rerank": False,
        "rag.verified_knowledge.enabled": verified,
        "rag.verified_knowledge.wiki_weight": 0.4,
        "rag.verified_knowledge.raw_weight": 0.6,
        "rag.verified_knowledge.empty_wiki_fallback_to_raw": True,
        "knowledge_workflow.mode": "verified" if verified else "evidence_only",
    }
    cfg = Mock()
    cfg.get.side_effect = lambda key, default=None: data.get(key, default)
    return cfg


def _raw_hit(bid: str = "b1", kid: str = "k1", text: str = "raw evidence", score: float = 0.9):
    return {
        "id": bid,
        "text": text,
        "metadata": {"page_id": kid, "title": "Doc", "knowledge_id": kid},
        "rrf_score": score,
    }


def _service(*, verified: bool = False) -> SearchService:
    db = Mock()
    db.search_wiki_fts.return_value = []
    db.get_knowledge.return_value = {"id": "k1", "title": "Doc"}
    db.search_knowledge.return_value = []
    wiki_repo = Mock() if verified else None
    if wiki_repo is not None:
        wiki_repo.list_claims.return_value = []
    return SearchService(
        _cfg(verified=verified),
        db,
        Mock(),
        Mock(),
        Mock(),
        wiki_repository=wiki_repo,
        wiki_serving_gate=Mock() if verified else None,
    )


def _run_pair(
    name: str,
    service: SearchService,
    query: str,
    *,
    patches: list | None = None,
) -> dict[str, Any]:
    orch_legacy = RetrievalOrchestrator(
        service, {"retrieval": {"orchestrator": "legacy"}},
    )
    orch_unified = RetrievalOrchestrator(
        service, {"retrieval": {"orchestrator": "unified"}},
    )
    ctx = patches or []
    with ExitStack() as stack:
        for p in ctx:
            stack.enter_context(p)
        t0 = time.monotonic()
        primary = orch_legacy.search(query, top_k=5)
        p_ms = (time.monotonic() - t0) * 1000
        t1 = time.monotonic()
        candidate = orch_unified.search(query, top_k=5)
        c_ms = (time.monotonic() - t1) * 1000

    diff = compare_executions(
        primary,
        candidate,
        top_k=5,
        latency_ms_primary=p_ms,
        latency_ms_candidate=c_ms,
    )
    return {
        "scenario": name,
        "query": query[:80],
        "meets_gates": meets_cutover_gates(diff),
        "source_id_overlap_top_k": diff.source_id_overlap_top_k,
        "claim_ids_match": diff.claim_ids_match,
        "conflicts_match": diff.conflicts_match,
        "fallbacks_match": diff.fallbacks_match,
        "citation_keys_match": diff.citation_keys_match,
        "exception_types": list(diff.exception_types),
        "latency_ms_primary": round(p_ms, 2),
        "latency_ms_candidate": round(c_ms, 2),
        "notes": list(diff.notes),
        "primary_source_ids": list(diff.primary_source_ids),
        "candidate_source_ids": list(diff.candidate_source_ids),
        "primary_result_count": len(primary.results),
        "candidate_result_count": len(candidate.results),
    }


def collect_scenarios() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    hit = _raw_hit()

    # Evidence-only happy path
    svc = _service(verified=False)
    results.append(
        _run_pair(
            "evidence_only",
            svc,
            "FTTR 规格",
            patches=[
                patch.object(svc, "_rewrite_query", return_value=["FTTR 规格"]),
                patch.object(svc, "_hybrid_search", return_value=[hit]),
                patch.object(svc, "_rerank", side_effect=lambda q, c, top_k: c),
            ],
        ),
    )

    # Verified hybrid with empty wiki → raw fallback
    svc_v = _service(verified=True)
    with patch.object(svc_v, "_should_use_verified_hybrid", return_value=True):
        results.append(
            _run_pair(
                "verified_empty_wiki",
                svc_v,
                "FTTR 可达带宽",
                patches=[
                    patch.object(svc_v, "_should_use_verified_hybrid", return_value=True),
                    patch.object(svc_v, "_rewrite_query", return_value=["FTTR"]),
                    patch.object(svc_v, "_timed_hybrid_search", return_value=[hit]),
                    patch.object(
                        svc_v, "_timed_rerank", side_effect=lambda q, c, top_k: c,
                    ),
                    patch.object(svc_v, "_safe_verified_claim_retrieve", return_value=[]),
                ],
            ),
        )

    # Wiki exception does not block raw
    svc_e = _service(verified=True)
    results.append(
        _run_pair(
            "wiki_exception",
            svc_e,
            "网络故障场景",
            patches=[
                patch.object(svc_e, "_should_use_verified_hybrid", return_value=True),
                patch.object(svc_e, "_rewrite_query", return_value=["网络"]),
                patch.object(svc_e, "_timed_hybrid_search", return_value=[hit]),
                patch.object(
                    svc_e, "_timed_rerank", side_effect=lambda q, c, top_k: c,
                ),
                patch.object(
                    svc_e,
                    "_safe_verified_claim_retrieve",
                    side_effect=RuntimeError("wiki down"),
                ),
            ],
        ),
    )

    # Query rewrite timeout path (falls back to original query)
    svc_t = _service(verified=False)

    def _slow_rewrite(q):
        time.sleep(0.01)
        return [q, q + " alt"]

    results.append(
        _run_pair(
            "query_rewrite_timeout_safe",
            svc_t,
            "timeout rewrite",
            patches=[
                patch.object(svc_t, "_rewrite_query", side_effect=_slow_rewrite),
                patch.object(svc_t, "_hybrid_search", return_value=[hit]),
                patch.object(svc_t, "_rerank", side_effect=lambda q, c, top_k: c),
                patch.object(svc_t, "_stage_timeout", return_value=30.0),
            ],
        ),
    )

    # Rerank timeout keeps candidates (warning path)
    svc_r = _service(verified=False)
    results.append(
        _run_pair(
            "rerank_timeout",
            svc_r,
            "rerank case",
            patches=[
                patch.object(svc_r, "_rewrite_query", return_value=["rerank case"]),
                patch.object(svc_r, "_hybrid_search", return_value=[hit]),
                patch.object(
                    svc_r,
                    "_rerank",
                    side_effect=TimeoutError("rerank"),
                ),
            ],
        ),
    )

    # QuerySpec empty → fall through (both paths)
    svc_q = _service(verified=False)
    empty_spec = Mock()
    results.append(
        _run_pair(
            "query_spec_empty_fallback",
            svc_q,
            "spec empty",
            patches=[
                patch.object(
                    svc_q,
                    "execute_query_spec",
                    return_value=SearchExecution(results=(), trace={"mode": "query_spec"}),
                ),
                patch.object(svc_q, "_rewrite_query", return_value=["spec empty"]),
                patch.object(svc_q, "_hybrid_search", return_value=[hit]),
                patch.object(svc_q, "_rerank", side_effect=lambda q, c, top_k: c),
            ],
        ),
    )
    # Force query_spec through orchestrator for last scenario by wrapping
    # (use same service with query_spec on both)
    orch_l = RetrievalOrchestrator(
        svc_q, {"retrieval": {"orchestrator": "legacy"}},
    )
    orch_u = RetrievalOrchestrator(
        svc_q, {"retrieval": {"orchestrator": "unified"}},
    )
    with (
        patch.object(
            svc_q,
            "execute_query_spec",
            return_value=SearchExecution(results=(), trace={"mode": "query_spec"}),
        ),
        patch.object(svc_q, "_rewrite_query", return_value=["spec empty"]),
        patch.object(svc_q, "_hybrid_search", return_value=[hit]),
        patch.object(svc_q, "_rerank", side_effect=lambda q, c, top_k: c),
    ):
        p = orch_l.search("spec empty", top_k=5, query_spec=empty_spec)
        c = orch_u.search("spec empty", top_k=5, query_spec=empty_spec)
    d = compare_executions(p, c, top_k=5)
    results[-1] = {
        "scenario": "query_spec_empty_fallback",
        "query": "spec empty",
        "meets_gates": meets_cutover_gates(d),
        "source_id_overlap_top_k": d.source_id_overlap_top_k,
        "claim_ids_match": d.claim_ids_match,
        "conflicts_match": d.conflicts_match,
        "fallbacks_match": d.fallbacks_match,
        "citation_keys_match": d.citation_keys_match,
        "exception_types": list(d.exception_types),
        "latency_ms_primary": 0.0,
        "latency_ms_candidate": 0.0,
        "notes": list(d.notes),
        "primary_source_ids": list(d.primary_source_ids),
        "candidate_source_ids": list(d.candidate_source_ids),
        "primary_result_count": len(p.results),
        "candidate_result_count": len(c.results),
    }

    return results


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows) or 1
    gate_pass = sum(1 for r in rows if r["meets_gates"])
    overlaps = [r["source_id_overlap_top_k"] for r in rows]
    claim_ok = sum(1 for r in rows if r["claim_ids_match"])
    conflict_ok = sum(1 for r in rows if r["conflicts_match"])
    fallback_ok = sum(1 for r in rows if r["fallbacks_match"])
    citation_ok = sum(1 for r in rows if r["citation_keys_match"])
    exceptions = [e for r in rows for e in r["exception_types"]]
    lat_ratio = []
    for r in rows:
        p, c = r["latency_ms_primary"], r["latency_ms_candidate"]
        if p > 0:
            lat_ratio.append((c - p) / p)

    summary = {
        "scenarios": len(rows),
        "gate_pass_count": gate_pass,
        "gate_pass_rate": gate_pass / n,
        "min_top5_overlap": min(overlaps) if overlaps else 1.0,
        "eligible_claim_match_rate": claim_ok / n,
        "conflict_match_rate": conflict_ok / n,
        "fallback_match_rate": fallback_ok / n,
        "citation_key_match_rate": citation_ok / n,
        "exception_types": sorted(set(exceptions)),
        "p95_latency_increase_ratio": (
            sorted(lat_ratio)[int(0.95 * (len(lat_ratio) - 1))] if lat_ratio else 0.0
        ),
        "default_orchestrator": resolve_orchestrator_mode(None),
    }
    summary["thresholds"] = {
        "top5_overlap_min": 0.95,
        "claim_match": 1.0,
        "conflict_match": 1.0,
        "fallback_match": 1.0,
        "citation_match": 1.0,
        "exceptions": 0,
        "p95_latency_increase_max": 0.10,
    }
    summary["pass"] = (
        summary["min_top5_overlap"] >= 0.95
        and summary["eligible_claim_match_rate"] >= 1.0
        and summary["conflict_match_rate"] >= 1.0
        and summary["fallback_match_rate"] >= 1.0
        and summary["citation_key_match_rate"] >= 1.0
        and len(summary["exception_types"]) == 0
        and summary["gate_pass_count"] == summary["scenarios"]
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retrieval shadow aggregate eval")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "evals" / "reports" / f"retrieval-shadow-{date.today().isoformat()}.json",
    )
    args = parser.parse_args(argv)

    rows = collect_scenarios()
    summary = summarize(rows)
    report = {
        "date": date.today().isoformat(),
        "summary": summary,
        "scenarios": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Wrote {args.out}")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
