"""Dual-review CLI for hit-rate Golden V2 candidates.

Reuses the production-pilot review philosophy:
- Agent never invents reviewer identities or timestamps.
- Reviewers must be distinct real humans provided via CLI.
- Output goes to reviewed/ only.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.hit_rate_v2.validation import (  # noqa: E402
    load_jsonl,
    validate_case_schema,
    write_jsonl,
)

DATA_ROOT = ROOT / "tests" / "eval" / "datasets" / "hit_rate"
CANDIDATES_DIR = DATA_ROOT / "candidates"
REVIEWED_DIR = DATA_ROOT / "reviewed"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def apply_review(
    row: dict[str, Any],
    *,
    reviewer: str,
    role: str,
    status: str,
    notes: str = "",
    disagreement: bool = False,
    adjudicator: str = "",
) -> dict[str, Any]:
    """Apply a single human review event. Does not fabricate timestamps/names."""
    if not str(reviewer or "").strip():
        raise ValueError("reviewer is required; refuse to invent reviewer identity")
    if role not in {"primary", "secondary", "adjudicator"}:
        raise ValueError("role must be primary|secondary|adjudicator")

    out = dict(row)
    review = dict(out.get("review") or {})
    now = utc_now()

    if role == "primary":
        review["primary_reviewer"] = reviewer
        review["primary_reviewed_at"] = now
    elif role == "secondary":
        review["secondary_reviewer"] = reviewer
        review["secondary_reviewed_at"] = now
    else:
        if not adjudicator and not reviewer:
            raise ValueError("adjudicator required")
        review["adjudicator"] = adjudicator or reviewer
        review["adjudicated_at"] = now

    review["status"] = status
    review["disagreement"] = bool(disagreement)
    if notes:
        review["decision_notes"] = notes
    out["review"] = review

    primary = str(review.get("primary_reviewer") or "").strip()
    secondary = str(review.get("secondary_reviewer") or "").strip()
    if (
        primary
        and secondary
        and primary != secondary
        and status == "approved"
        and not disagreement
    ):
        out["annotation_source"] = "human_reviewed"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True, help="candidates JSONL")
    ap.add_argument("--output", type=Path, required=True, help="reviewed JSONL")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--reviewer", required=True, help="Real human reviewer id/name")
    ap.add_argument(
        "--role",
        choices=["primary", "secondary", "adjudicator"],
        required=True,
    )
    ap.add_argument(
        "--status",
        choices=["approved", "rejected", "needs_adjudication", "disputed"],
        required=True,
    )
    ap.add_argument("--notes", default="")
    ap.add_argument("--disagreement", action="store_true")
    ap.add_argument("--adjudicator", default="")
    args = ap.parse_args(argv)

    # Safety: never write into frozen from this tool
    if "frozen" in args.output.resolve().parts:
        print("ERROR: review tool must not write frozen/", file=sys.stderr)
        return 2

    rows = load_jsonl(args.input)
    if args.output.exists():
        # Merge into existing reviewed file when present
        existing = {r.get("case_id"): r for r in load_jsonl(args.output)}
        for r in rows:
            existing.setdefault(r.get("case_id"), r)
        rows = list(existing.values())

    found = False
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        if row.get("case_id") != args.case_id:
            out_rows.append(row)
            continue
        found = True
        schema_errs = validate_case_schema(row)
        if schema_errs and args.status == "approved":
            print(f"schema errors (still recording review): {schema_errs}")
        updated = apply_review(
            row,
            reviewer=args.reviewer,
            role=args.role,
            status=args.status,
            notes=args.notes,
            disagreement=bool(args.disagreement),
            adjudicator=args.adjudicator,
        )
        out_rows.append(updated)
        print(json.dumps(updated.get("review"), ensure_ascii=False, indent=2))

    if not found:
        print(f"case not found: {args.case_id}", file=sys.stderr)
        return 1

    write_jsonl(args.output, out_rows)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
