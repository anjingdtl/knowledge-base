"""Freeze only fully audited production-pilot ground truth.

The command never makes annotation decisions.  It validates reviewed JSONL and
copies only rows that pass every audit gate into ``datasets/frozen``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "tests" / "eval" / "datasets"
REVIEWED_DIR = DATA_ROOT / "reviewed"
FROZEN_DIR = DATA_ROOT / "frozen"
ARTIFACT = ROOT / "artifacts" / "foundation-three-fixes" / "dataset-freeze-summary.json"
DATASETS = ("retrieval", "no_answer", "numeric_units", "routing", "answer_citations")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def corpus_snapshot(db_path: Path) -> str:
    digest = hashlib.sha256()
    with db_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return f"kb.db:{digest.hexdigest()[:16]}"


def _is_iso8601(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _annotation_ids(row: dict[str, Any]) -> dict[str, str]:
    decisions: dict[str, str] = {}
    for field, decision in (
        ("expected_ids", "expected"),
        ("acceptable_ids", "acceptable"),
        ("forbidden_ids", "forbidden"),
    ):
        for knowledge_id in row.get(field) or []:
            decisions[str(knowledge_id)] = decision
    for fact in row.get("expected_answer_facts") or []:
        for knowledge_id in fact.get("supporting_knowledge_ids") or []:
            decisions.setdefault(str(knowledge_id), "expected")
    for knowledge_id in row.get("known_distractor_ids") or []:
        decisions.setdefault(str(knowledge_id), "forbidden")
    return decisions


def validate_reviewed_row(
    row: dict[str, Any], dataset: str, expected_snapshot: str
) -> list[str]:
    """Return stable field/error codes; an empty result means freeze-eligible."""
    errors: list[str] = []
    if row.get("annotation_source") != "human_reviewed":
        errors.append("annotation_source")
    if row.get("corpus_snapshot_sha") != expected_snapshot:
        errors.append("corpus_snapshot_sha_mismatch")

    review = row.get("review")
    if not isinstance(review, dict):
        return errors + ["review"]
    if review.get("status") != "approved":
        errors.append("review.status_not_approved")

    for field in ("primary_reviewer", "secondary_reviewer"):
        if not str(review.get(field) or "").strip():
            errors.append(f"review.{field}")
    if (
        review.get("primary_reviewer")
        and review.get("primary_reviewer") == review.get("secondary_reviewer")
    ):
        errors.append("review.reviewers_must_differ")
    for field in ("primary_reviewed_at", "secondary_reviewed_at"):
        if not _is_iso8601(review.get(field)):
            errors.append(f"review.{field}")

    if review.get("status") == "needs_adjudication":
        errors.append("review.needs_adjudication")
    if review.get("disagreement") and not (
        str(review.get("adjudicator") or "").strip()
        and _is_iso8601(review.get("adjudicated_at"))
    ):
        errors.append("review.adjudication_incomplete")

    if dataset == "retrieval" and not row.get("expected_ids"):
        errors.append("expected_ids")
    if dataset == "no_answer":
        if row.get("expected_no_answer") is not True:
            errors.append("expected_no_answer")
        if not str(row.get("reason") or "").strip():
            errors.append("reason")
    if dataset == "numeric_units" and not row.get("expected_no_answer"):
        if not (row.get("expected_ids") or row.get("expected_units")):
            errors.append("expected_ids_or_units")
    if dataset == "routing":
        if row.get("expected_mode") not in {"structured", "graph", "hybrid"}:
            errors.append("expected_mode")
        if not str(row.get("expected_tool") or "").strip():
            errors.append("expected_tool")
        if not str(row.get("expected_task_outcome") or "").strip():
            errors.append("expected_task_outcome")

    evidence = review.get("evidence_checked")
    if not isinstance(evidence, list):
        errors.append("review.evidence_checked")
        evidence = []
    evidence_by_id: dict[str, dict[str, Any]] = {
        str(item.get("knowledge_id")): item
        for item in evidence
        if isinstance(item, dict) and item.get("knowledge_id")
    }
    for knowledge_id, expected_decision in _annotation_ids(row).items():
        item = evidence_by_id.get(knowledge_id)
        if item is None:
            errors.append(f"review.evidence_checked[{knowledge_id}]")
            continue
        if item.get("decision") != expected_decision:
            errors.append(f"review.evidence_checked[{knowledge_id}].decision")
        if item.get("checked_title") is not True:
            errors.append(f"review.evidence_checked[{knowledge_id}].checked_title")
        if item.get("checked_body") is not True:
            errors.append(f"review.evidence_checked[{knowledge_id}].checked_body")
        if not str(item.get("reason") or "").strip():
            errors.append(f"review.evidence_checked[{knowledge_id}].reason")

    if dataset == "answer_citations":
        facts = row.get("expected_answer_facts") or []
        if not facts:
            errors.append("expected_answer_facts")
        for index, fact in enumerate(facts):
            blocks = {str(value) for value in fact.get("supporting_block_ids") or []}
            quotes = fact.get("supporting_quotes") or []
            if not blocks:
                errors.append(f"expected_answer_facts[{index}].supporting_block_ids")
            if not quotes:
                errors.append(f"expected_answer_facts[{index}].supporting_quotes")
            knowledge_ids = {
                str(value) for value in fact.get("supporting_knowledge_ids") or []
            }
            for quote in quotes:
                if str(quote.get("block_id") or "") not in blocks:
                    errors.append(
                        f"expected_answer_facts[{index}].supporting_quote_block_mismatch"
                    )
                if str(quote.get("knowledge_id") or "") not in knowledge_ids:
                    errors.append(
                        f"expected_answer_facts[{index}].supporting_quote_knowledge_mismatch"
                    )
                if not str(quote.get("quote") or "").strip():
                    errors.append(f"expected_answer_facts[{index}].supporting_quote_empty")
                if not str(quote.get("reason") or "").strip():
                    errors.append(f"expected_answer_facts[{index}].supporting_quote_reason")
    return list(dict.fromkeys(errors))


def validate_corpus_evidence(
    row: dict[str, Any], dataset: str, conn: Any
) -> list[str]:
    """Verify reviewed IDs and answer quotes against the frozen DB snapshot."""
    errors: list[str] = []
    for knowledge_id in _annotation_ids(row):
        found = conn.execute(
            "SELECT 1 FROM knowledge_items WHERE id = ?", (knowledge_id,)
        ).fetchone()
        if found is None:
            errors.append(f"corpus.knowledge_missing[{knowledge_id}]")
    if dataset == "answer_citations":
        for index, fact in enumerate(row.get("expected_answer_facts") or []):
            for quote in fact.get("supporting_quotes") or []:
                block = conn.execute(
                    "SELECT page_id, content FROM blocks WHERE id = ?",
                    (str(quote.get("block_id") or ""),),
                ).fetchone()
                if block is None:
                    errors.append(f"corpus.block_missing[{quote.get('block_id')}]")
                    continue
                if str(block[0]) != str(quote.get("knowledge_id") or ""):
                    errors.append(
                        f"expected_answer_facts[{index}].supporting_quote_knowledge_mismatch"
                    )
                if str(quote.get("quote") or "") not in str(block[1] or ""):
                    errors.append(
                        f"expected_answer_facts[{index}].supporting_quote_not_in_block"
                    )
    return list(dict.fromkeys(errors))


def freeze_all(*, db_path: Path, strict: bool = False) -> dict[str, Any]:
    import sqlite3

    snapshot = corpus_snapshot(db_path)
    candidates_dir = DATA_ROOT / "candidates"
    summary: dict[str, Any] = {
        "candidate_count": 0,
        "reviewed_count": 0,
        "approved_count": 0,
        "rejected_count": 0,
        "adjudicated_count": 0,
        "frozen_count": 0,
        "reviewer_counts": {},
        "missing_review_fields": [],
        "corpus_snapshot_sha": snapshot,
        "datasets": {},
    }
    reviewer_counts: Counter[str] = Counter()
    missing: list[dict[str, Any]] = []

    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    try:
        for dataset in DATASETS:
            candidate_path = candidates_dir / f"production_pilot_{dataset}.candidates.jsonl"
            reviewed_path = REVIEWED_DIR / f"production_pilot_{dataset}.reviewed.jsonl"
            frozen_path = FROZEN_DIR / f"production_pilot_{dataset}.jsonl"
            candidates = load_jsonl(candidate_path)
            reviewed = load_jsonl(reviewed_path)
            reviewed_ids = {row.get("id") for row in reviewed}
            for candidate in candidates:
                if candidate.get("id") not in reviewed_ids:
                    missing.append(
                        {
                            "dataset": dataset,
                            "id": candidate.get("id"),
                            "errors": ["review_record_missing"],
                        }
                    )
            frozen: list[dict[str, Any]] = []
            approved = rejected = adjudicated = 0
            for row in reviewed:
                review = row.get("review") if isinstance(row.get("review"), dict) else {}
                status = review.get("status")
                approved += int(status == "approved")
                rejected += int(status == "rejected")
                adjudicated += int(bool(review.get("adjudicator")))
                for field in ("primary_reviewer", "secondary_reviewer", "adjudicator"):
                    reviewer = str(review.get(field) or "").strip()
                    if reviewer:
                        reviewer_counts[reviewer] += 1
                errors = validate_reviewed_row(row, dataset, snapshot)
                errors.extend(validate_corpus_evidence(row, dataset, conn))
                errors = list(dict.fromkeys(errors))
                if not errors:
                    frozen.append(row)
                else:
                    missing.append({"dataset": dataset, "id": row.get("id"), "errors": errors})
            write_jsonl(frozen_path, frozen)
            summary["candidate_count"] += len(candidates)
            summary["reviewed_count"] += len(reviewed)
            summary["approved_count"] += approved
            summary["rejected_count"] += rejected
            summary["adjudicated_count"] += adjudicated
            summary["frozen_count"] += len(frozen)
            summary["datasets"][dataset] = {
                "candidate_count": len(candidates),
                "reviewed_count": len(reviewed),
                "frozen_count": len(frozen),
            }
    finally:
        conn.close()

    summary["reviewer_counts"] = dict(sorted(reviewer_counts.items()))
    summary["missing_review_fields"] = missing
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if strict and (summary["frozen_count"] != summary["candidate_count"] or missing):
        raise SystemExit("not all candidates have complete audited review")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "kb.db")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    summary = freeze_all(db_path=args.db.resolve(), strict=args.strict)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
