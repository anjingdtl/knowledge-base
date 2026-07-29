"""Produce a per-case retrieval-stage audit without altering production ranking.

The tool is intentionally file-backed (rather than a stdin snippet) so the
Windows embedding worker can be spawned reliably.  It records every channel
before fusion and the RawRetriever trace after rerank, making the first stage
where expected knowledge disappears explicit.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _identity(row: dict[str, Any]) -> str:
    meta = row.get("metadata") or {}
    return str(row.get("knowledge_id") or meta.get("knowledge_id") or meta.get("page_id") or row.get("id") or "")


def _ids(rows: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(value for value in (_identity(row) for row in rows) if value))


def _first_loss(expected: set[str], stages: list[tuple[str, list[str]]]) -> str | None:
    for name, ids in stages:
        if not expected.intersection(ids):
            return name
    return None


def run(golden_path: Path, out_path: Path, *, top_k: int = 5) -> dict[str, Any]:
    from src.core.container import create_container
    from src.retrieval.raw_retriever import build_deterministic_query_variants
    from src.services.hybrid_search import HybridSearcher

    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    cases = [case for case in golden["cases"] if not case.get("expected_no_answer")]
    container = create_container()
    service = container.search_service
    raw = service._get_raw_retriever()
    # The channel calls are evaluation observation only; production search
    # still flows through SearchService/RawRetriever unchanged.
    hybrid = HybridSearcher(service._db, service._block_store, service._config)
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    for case in cases:
        query = str(case["query"])
        expected = set(case.get("expected_knowledge_ids") or [])
        queries = list(raw.rewrite_query(query) or [query])
        for variant in build_deterministic_query_variants(query):
            value = str(variant.get("query") or "")
            if value and value not in queries:
                queries.append(value)
        queries = queries[:6]
        pool_k = max(top_k * 4, 20)
        t0 = time.perf_counter()
        vector_rows: list[dict[str, Any]] = []
        keyword_rows: list[dict[str, Any]] = []
        try:
            if hybrid._use_passages():
                vector_rows, _warnings = hybrid._passage_vector_search(queries, pool_k)
                keyword_rows = hybrid._passage_keyword_search(queries, pool_k)
            else:
                vector_rows, _warnings = hybrid._vector_search(queries, pool_k)
                keyword_rows = hybrid._keyword_search(queries, pool_k)
        except Exception as exc:  # evidence must retain channel failure, not hide it
            channel_error = f"{type(exc).__name__}: {exc}"
        else:
            channel_error = ""
        title_or_section_ids = [
            _identity(row) for row in keyword_rows
            if str(row.get("title") or (row.get("metadata") or {}).get("title") or "")
        ]
        fusion_rows = hybrid.search(queries, top_k=pool_k)
        fts_rows = raw.knowledge_fts_search(query, pool_k)
        result = raw.retrieve(query, top_k=top_k, include_legacy_wiki_fts=False)
        trace = dict(result.trace or {})
        stages = dict(trace.get("stages") or {})
        pre_rerank_ids = list(stages.get("pre_rerank_candidate_ids") or [])
        rerank_ids = list((stages.get("rerank") or {}).get("output_candidate_ids") or [])
        final_ids = _ids([dict(item) for item in result.candidates])
        stage_lists = [
            ("raw_vector", _ids(vector_rows)),
            ("raw_fts", _ids(keyword_rows) or _ids(fts_rows)),
            ("title_or_section", list(dict.fromkeys(value for value in title_or_section_ids if value))),
            ("fusion", _ids(fusion_rows)),
            ("rerank_input", pre_rerank_ids),
            ("rerank_output", rerank_ids),
            ("canonical_accepted", final_ids),
        ]
        rows.append({
            "case_id": case["case_id"],
            "query": query,
            "expected_knowledge_ids": sorted(expected),
            "expanded_queries": queries,
            "raw_fts_ids": _ids(keyword_rows) or _ids(fts_rows),
            "raw_vector_ids": _ids(vector_rows),
            "title_or_section_ids": list(dict.fromkeys(value for value in title_or_section_ids if value)),
            "expanded_query_ids": _ids(fusion_rows),
            "fusion_ids": _ids(fusion_rows),
            "rerank_input_ids": pre_rerank_ids,
            "rerank_output_ids": rerank_ids,
            "policy_filtered_ids": [],
            "policy_filter_reason": "not_applied_in_raw_retrieval_audit",
            "canonical_accepted_ids": final_ids,
            "first_expected_loss_stage": _first_loss(expected, stage_lists),
            "channel_error": channel_error,
            "trace": trace,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        })
    payload = {
        "kind": "retrieval_stage_audit",
        "golden": str(golden_path),
        "answerable_case_count": len(rows),
        "elapsed_s": round(time.perf_counter() - started, 2),
        "rows": rows,
    }
    _write(out_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default="evals/golden_set_hit_rate.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    result = run(Path(args.golden), Path(args.out), top_k=args.top_k)
    print(json.dumps({key: result[key] for key in ("answerable_case_count", "elapsed_s")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
