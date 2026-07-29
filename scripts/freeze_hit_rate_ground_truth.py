"""Strict freeze gate for hit-rate Golden V2 reviewed rows.

Never invents annotation decisions. Copies only rows that pass every audit
gate into datasets/hit_rate/frozen/. Fail closed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.hit_rate_v2.validation import (  # noqa: E402
    assert_split_isolation,
    corpus_snapshot_token,
    dataset_hash,
    load_jsonl,
    validate_freeze_row,
    write_jsonl,
)

DATA_ROOT = ROOT / "tests" / "eval" / "datasets" / "hit_rate"
REVIEWED_DIR = DATA_ROOT / "reviewed"
FROZEN_DIR = DATA_ROOT / "frozen"


def freeze_rows(
    rows: list[dict[str, Any]],
    *,
    expected_corpus_sha: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        errors = validate_freeze_row(row, expected_corpus_sha=expected_corpus_sha)
        if errors:
            rejected.append({"case_id": row.get("case_id"), "errors": errors})
        else:
            accepted.append(row)
    isolation = assert_split_isolation(accepted)
    if isolation:
        # Fail closed: if isolation fails, reject all
        for row in accepted:
            rejected.append(
                {"case_id": row.get("case_id"), "errors": isolation}
            )
        accepted = []
    return accepted, rejected


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True, help="reviewed JSONL")
    ap.add_argument(
        "--output",
        type=Path,
        default=FROZEN_DIR / "hit_rate_v2.frozen.jsonl",
    )
    ap.add_argument(
        "--db",
        type=Path,
        default=ROOT / "data" / "kb.db",
        help="Readonly KB path used for corpus snapshot token",
    )
    ap.add_argument(
        "--corpus-sha",
        default="",
        help="Optional precomputed corpus token (kb.db:xxxxxxxx); "
        "if empty, computed from --db",
    )
    args = ap.parse_args(argv)

    if "candidates" in args.output.resolve().parts:
        print("ERROR: freeze output must not target candidates/", file=sys.stderr)
        return 2

    rows = load_jsonl(args.input)
    if not rows:
        print("no reviewed rows", file=sys.stderr)
        summary = {
            "accepted": 0,
            "rejected": 0,
            "note": "Phase 1 engineering complete; formal dataset freeze blocked by human review.",
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1

    if args.corpus_sha:
        corpus_sha = args.corpus_sha
    else:
        if not args.db.exists():
            print(f"db missing for corpus snapshot: {args.db}", file=sys.stderr)
            return 2
        # Read-only hash of db file
        corpus_sha = corpus_snapshot_token(args.db)

    accepted, rejected = freeze_rows(rows, expected_corpus_sha=corpus_sha)
    if accepted:
        write_jsonl(args.output, accepted)

    summary = {
        "input": str(args.input),
        "output": str(args.output) if accepted else None,
        "corpus_sha": corpus_sha,
        "accepted": len(accepted),
        "rejected": len(rejected),
        "rejected_detail": rejected[:50],
        "dataset_hash": dataset_hash(accepted) if accepted else "",
        "note": (
            "Freeze succeeded."
            if accepted and not rejected
            else "Phase 1 engineering complete; formal dataset freeze blocked by human review."
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if accepted and not rejected else 1


if __name__ == "__main__":
    raise SystemExit(main())
