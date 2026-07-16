"""Production pilot evaluation harness (strict denominators).

Replaces deprecated golden scoring in scripts/final_closure_mcp_harness.py.
Can score offline result JSONL or run in-process search against formal DB
read-only for retrieval metrics.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.production_pilot_metrics import (  # noqa: E402
    metrics_to_jsonable,
    score_answer_citations,
    score_no_answer,
    score_numeric_units,
    score_retrieval,
    score_routing,
)

DATA = ROOT / "tests" / "eval" / "datasets"
ART = ROOT / "artifacts" / "production-pilot-final-validation"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def fts_search_ids(db_path: Path, query: str, limit: int = 10) -> list[str]:
    """Read-only FTS candidate fetch for offline retrieval scoring (not GT)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        # Prefer title/content LIKE for robust Chinese matching without FTS query syntax issues
        q = f"%{query}%"
        rows = con.execute(
            """
            SELECT id FROM knowledge_items
            WHERE (title LIKE ? OR content LIKE ?)
              AND (deleted_at IS NULL OR deleted_at = '')
            LIMIT ?
            """,
            (q, q, limit),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def eval_retrieval_offline(db: Path) -> dict[str, Any]:
    gold = load_jsonl(DATA / "production_pilot_retrieval.jsonl")
    scored = []
    for row in gold:
        got = fts_search_ids(db, row["query"], 10)
        scored.append(
            {
                **row,
                "got_ids": got,
                "response_ok": True,
                "channel": "fts_like_readonly",
            }
        )
    metrics = score_retrieval(scored)
    return {
        "channel": "fts_like_readonly",
        "n": len(scored),
        "metrics": metrics_to_jsonable(metrics),
        "rows": scored,
    }


def eval_no_answer_placeholder() -> dict[str, Any]:
    """Offline structural score only; real MCP results filled by Phase 8 harness."""
    gold = load_jsonl(DATA / "production_pilot_no_answer.jsonl")
    # Without runtime: mark as not_tested for ask/search outcomes
    scored = [
        {
            **row,
            "search_no_match": None,
            "ask_answer_mode": None,
            "answer": None,
            "sources": None,
            "excluded_from_metric": True,
            "not_tested": True,
        }
        for row in gold
    ]
    return {
        "status": "NOT_TESTED_runtime",
        "n": len(scored),
        "metrics": metrics_to_jsonable(score_no_answer([])),
        "note": "Runtime MCP results required; empty denominator until filled",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "kb.db")
    parser.add_argument("--out", type=Path, default=ART)
    args = parser.parse_args()

    ART.mkdir(parents=True, exist_ok=True)
    retrieval = eval_retrieval_offline(args.db)
    write_json(args.out / "retrieval-fts-offline.json", {
        "metrics": retrieval["metrics"],
        "n": retrieval["n"],
        "channel": retrieval["channel"],
    })
    # full rows separate
    with (args.out / "retrieval-fts-offline.jsonl").open("w", encoding="utf-8") as f:
        for r in retrieval["rows"]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    noa = eval_no_answer_placeholder()
    write_json(args.out / "no-answer-placeholder.json", noa)

    # Denominator report from pure metric unit fixtures + retrieval offline
    denoms = {
        "retrieval_fts_offline": retrieval["metrics"],
        "deprecated_note": (
            "scripts/final_closure_mcp_harness.py golden accuracy is DEPRECATED; "
            "do not use empty-expected full-score paths for pilot decision."
        ),
    }
    write_json(args.out / "metric-denominators.json", denoms)
    write_json(args.out / "metrics.json", {
        "retrieval_fts_offline": retrieval["metrics"],
        "status": "partial_offline",
    })

    print(json.dumps({
        "retrieval_recall_at_5": retrieval["metrics"]["recall_at_5"],
        "retrieval_mrr_at_10": retrieval["metrics"]["mrr_at_10"],
        "n_retrieval": retrieval["n"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
