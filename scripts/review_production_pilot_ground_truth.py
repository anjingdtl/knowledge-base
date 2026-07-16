"""Interactive, decision-neutral production-pilot ground-truth review CLI."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "tests" / "eval" / "datasets"
DATASETS = ("retrieval", "no_answer", "numeric_units", "routing", "answer_citations")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


class CorpusReader:
    """Read-only corpus helper; never opens SQLite in write mode."""

    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def document(self, knowledge_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, title, content FROM knowledge_items WHERE id = ?",
            (knowledge_id,),
        ).fetchone()
        return dict(row) if row else None

    def blocks(self, knowledge_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, content FROM blocks WHERE page_id = ? ORDER BY order_idx",
            (knowledge_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def block(self, block_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, page_id, content FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        return dict(row) if row else None


def _candidate_ids(row: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for field in (
        "candidate_expected_ids",
        "candidate_acceptable_ids",
        "candidate_forbidden_ids",
        "candidate_known_distractor_ids",
    ):
        ids.extend(str(value) for value in row.get(field) or [])
    for fact in row.get("candidate_expected_answer_facts") or []:
        ids.extend(str(value) for value in fact.get("supporting_knowledge_ids") or [])
    return list(dict.fromkeys(ids))


def _show_document(reader: CorpusReader, knowledge_id: str) -> dict[str, Any] | None:
    doc = reader.document(knowledge_id)
    if doc is None:
        print(f"  [{knowledge_id}] MISSING FROM SNAPSHOT")
        return None
    body = str(doc.get("content") or "")
    print(f"  [{knowledge_id}] {doc.get('title')}")
    print(f"  summary: {body[:500].replace(chr(10), ' ')}")
    if input("  Open full body? [y/N]: ").strip().lower() == "y":
        print(body)
    return doc


def _decision_for_document(reader: CorpusReader, knowledge_id: str) -> dict[str, Any]:
    doc = _show_document(reader, knowledge_id)
    while True:
        choice = input("  Decision expected/acceptable/forbidden/irrelevant [e/a/f/i]: ").strip().lower()
        if choice in {"e", "a", "f", "i"}:
            break
    decision = {"e": "expected", "a": "acceptable", "f": "forbidden", "i": "irrelevant"}[choice]
    reason = ""
    while not reason:
        reason = input("  Evidence-based reason (required): ").strip()
    return {
        "knowledge_id": knowledge_id,
        "title": str((doc or {}).get("title") or ""),
        "decision": decision,
        "reason": reason,
        "checked_title": doc is not None,
        "checked_body": doc is not None,
    }


def _explicit_hypothesis_decision(candidate: dict[str, Any]) -> bool:
    print(json.dumps(candidate, ensure_ascii=False, indent=2))
    while True:
        value = input("Type APPROVE to confirm this hypothesis or REJECT: ").strip().upper()
        if value in {"APPROVE", "REJECT"}:
            return value == "APPROVE"


def _copy_candidate_fields(candidate: dict[str, Any], dataset: str) -> dict[str, Any]:
    row = {key: value for key, value in candidate.items() if not key.startswith("candidate_")}
    if dataset == "no_answer":
        row.update(
            expected_no_answer=candidate.get("candidate_expected_no_answer"),
            reason=candidate.get("candidate_reason"),
            known_distractor_ids=candidate.get("candidate_known_distractor_ids") or [],
        )
    elif dataset == "routing":
        for field in (
            "expected_mode",
            "expected_tool",
            "required_argument_keys",
            "forbidden_tool",
            "expected_task_outcome",
        ):
            row[field] = candidate.get(f"candidate_{field}")
    elif dataset == "numeric_units":
        for field in (
            "expected_ids",
            "expected_units",
            "forbidden_units",
            "forbidden_ids",
            "expected_no_answer",
        ):
            row[field] = candidate.get(f"candidate_{field}") or ([] if field != "expected_no_answer" else False)
    elif dataset == "answer_citations":
        row["expected_answer_facts"] = candidate.get("candidate_expected_answer_facts") or []
        row["forbidden_claims"] = candidate.get("candidate_forbidden_claims") or []
        row["minimum_sources"] = candidate.get("candidate_minimum_sources") or 1
    return row


def primary_review(
    candidate: dict[str, Any], dataset: str, reviewer: str, reader: CorpusReader
) -> dict[str, Any]:
    print(f"\n{candidate.get('id')}: {candidate.get('query') or candidate.get('question')}")
    evidence: list[dict[str, Any]] = []
    decisions: dict[str, list[str]] = {key: [] for key in ("expected", "acceptable", "forbidden")}
    for knowledge_id in _candidate_ids(candidate):
        item = _decision_for_document(reader, knowledge_id)
        evidence.append(item)
        if item["decision"] in decisions:
            decisions[item["decision"]].append(knowledge_id)

    accepted = True
    if dataset in {"no_answer", "routing"}:
        accepted = _explicit_hypothesis_decision(candidate)
    row = _copy_candidate_fields(candidate, dataset)
    if dataset == "retrieval":
        row["expected_ids"] = decisions["expected"]
        row["acceptable_ids"] = decisions["acceptable"]
        row["forbidden_ids"] = decisions["forbidden"]
    elif dataset == "numeric_units":
        row["expected_ids"] = decisions["expected"]
        row["forbidden_ids"] = decisions["forbidden"]

    if dataset == "answer_citations" and accepted:
        for fact in row.get("expected_answer_facts") or []:
            block_ids: list[str] = []
            quotes: list[dict[str, str]] = []
            print(f"Fact: {fact.get('statement')}")
            for knowledge_id in fact.get("supporting_knowledge_ids") or []:
                for block in reader.blocks(str(knowledge_id))[:20]:
                    print(f"  block {block['id']}: {str(block.get('content') or '')[:240]}")
                block_id = input("  Supporting block id (blank rejects fact): ").strip()
                if not block_id:
                    accepted = False
                    continue
                block = reader.block(block_id)
                quote = input("  Exact supporting quote: ").strip()
                reason = input("  Quote reason: ").strip()
                if not block or block.get("page_id") != knowledge_id or quote not in str(block.get("content") or ""):
                    print("  Block/quote does not match corpus; sample marked rejected.")
                    accepted = False
                    continue
                block_ids.append(block_id)
                quotes.append(
                    {"knowledge_id": knowledge_id, "block_id": block_id, "quote": quote, "reason": reason}
                )
            fact["supporting_block_ids"] = block_ids
            fact["supporting_quotes"] = quotes

    notes = input("Decision notes: ").strip()
    row["annotation_source"] = "human_reviewed"
    row["review"] = {
        "status": "approved" if accepted else "rejected",
        "primary_reviewer": reviewer,
        "primary_reviewed_at": utc_now(),
        "secondary_reviewer": "",
        "secondary_reviewed_at": "",
        "adjudicator": "",
        "adjudicated_at": "",
        "decision_notes": notes,
        "evidence_checked": evidence,
        "disagreement": False,
    }
    return row


def secondary_review(row: dict[str, Any], reviewer: str, reader: CorpusReader) -> dict[str, Any]:
    review = row.get("review") or {}
    if reviewer == review.get("primary_reviewer"):
        raise ValueError("secondary reviewer must differ from primary reviewer")
    print(json.dumps(row, ensure_ascii=False, indent=2))
    for knowledge_id in _candidate_ids(row) or list({*row.get("expected_ids", []), *row.get("acceptable_ids", []), *row.get("forbidden_ids", [])}):
        _show_document(reader, str(knowledge_id))
    while True:
        choice = input("Secondary decision APPROVE / DISAGREE / REJECT: ").strip().upper()
        if choice in {"APPROVE", "DISAGREE", "REJECT"}:
            break
    review["secondary_reviewer"] = reviewer
    review["secondary_reviewed_at"] = utc_now()
    review["disagreement"] = choice == "DISAGREE"
    review["status"] = {"APPROVE": review.get("status", "approved"), "DISAGREE": "needs_adjudication", "REJECT": "rejected"}[choice]
    row["review"] = review
    return row


def adjudicate(row: dict[str, Any], reviewer: str) -> dict[str, Any]:
    review = row.get("review") or {}
    if reviewer in {review.get("primary_reviewer"), review.get("secondary_reviewer")}:
        raise ValueError("adjudicator must be independent of both reviewers")
    print(json.dumps(row, ensure_ascii=False, indent=2))
    while True:
        choice = input("Adjudication APPROVE / REJECT: ").strip().upper()
        if choice in {"APPROVE", "REJECT"}:
            break
    review["adjudicator"] = reviewer
    review["adjudicated_at"] = utc_now()
    review["status"] = "approved" if choice == "APPROVE" else "rejected"
    row["review"] = review
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=DATASETS, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "kb.db")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--second-review", action="store_true")
    mode.add_argument("--adjudicate", action="store_true")
    args = parser.parse_args()

    candidate_path = DATA_ROOT / "candidates" / f"production_pilot_{args.dataset}.candidates.jsonl"
    reviewed_path = DATA_ROOT / "reviewed" / f"production_pilot_{args.dataset}.reviewed.jsonl"
    reviewed = load_jsonl(reviewed_path)
    reader = CorpusReader(args.db)
    try:
        if args.second_review or args.adjudicate:
            if not reviewed:
                raise SystemExit("no primary-reviewed rows exist")
            output = []
            for row in reviewed:
                output.append(adjudicate(row, args.reviewer) if args.adjudicate else secondary_review(row, args.reviewer, reader))
        else:
            candidates = load_jsonl(candidate_path)
            if not candidates:
                raise SystemExit(f"no candidates found: {candidate_path}")
            existing = {row.get("id"): row for row in reviewed}
            output = []
            for candidate in candidates:
                if candidate.get("id") in existing:
                    output.append(existing[candidate["id"]])
                    continue
                output.append(primary_review(candidate, args.dataset, args.reviewer, reader))
        write_jsonl(reviewed_path, output)
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
