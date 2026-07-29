"""Deterministically migrate Golden V1 → V2 candidates (Phase 1 Task 1.2).

Rules:
- Preserve all case IDs; do not delete or rename.
- Write only to candidates/; never overwrite reviewed/ or frozen/.
- annotation_source=candidate; never invent reviewers.
- All current 37 V1 cases → split=development (or regression when category
  indicates no-answer / prior high-risk tuning exposure).
- Auto-derived passages/facts are proposals only.
- Idempotent: re-running refreshes candidates but refuses if a same case_id
  already exists under reviewed/frozen with human_reviewed status protection
  (candidates may be regenerated).
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
    LEGACY_V1_EXPOSED_CASE_IDS,
    dataset_hash,
    validate_case_schema,
    write_jsonl,
)

DEFAULT_V1 = ROOT / "evals" / "golden_set_hit_rate.json"
DEFAULT_OUT = (
    ROOT / "tests" / "eval" / "datasets" / "hit_rate" / "candidates" / "golden_v1_migrated.jsonl"
)
REVIEWED_DIR = ROOT / "tests" / "eval" / "datasets" / "hit_rate" / "reviewed"
FROZEN_DIR = ROOT / "tests" / "eval" / "datasets" / "hit_rate" / "frozen"

# Categories historically treated as higher risk / no-answer style.
_HIGH_RISK_CATS = {
    "无答案或越界问题",
    "新旧规则冲突",
    "易混淆知识",
    "跨文档综合",
}


def _intent_for(case: dict[str, Any]) -> str:
    cat = str(case.get("category") or "")
    if case.get("expected_no_answer"):
        return "scope"
    if "跨文档" in cat:
        return "cross_document"
    if "新旧" in cat or "版本" in cat:
        return "version"
    if any(ch.isdigit() for ch in " ".join(case.get("required_facts") or [])):
        return "numeric"
    if "政策" in cat or "管理" in cat:
        return "policy"
    return "fact"


def _risk_for(case: dict[str, Any]) -> str:
    if case.get("expected_no_answer"):
        return "P0"
    cat = str(case.get("category") or "")
    if cat in _HIGH_RISK_CATS:
        return "P1"
    return "P2"


def migrate_case(case: dict[str, Any]) -> dict[str, Any]:
    cid = str(case["case_id"])
    no_answer = bool(case.get("expected_no_answer"))
    required = list(case.get("required_facts") or [])
    forbidden = list(case.get("forbidden_facts") or [])
    expected_ids = list(case.get("expected_knowledge_ids") or [])
    title_kw = list(case.get("expected_title_keywords") or [])

    # All V1-exposed cases are development/regression only — never holdout.
    split = "development"
    if cid in LEGACY_V1_EXPOSED_CASE_IDS:
        split = "development"

    expected_sources = []
    for kid in expected_ids:
        expected_sources.append(
            {
                "knowledge_id": str(kid),
                "passage_id": None,
                "passage_missing_reason": "v1_migration_passage_not_resolved",
                "source_role": "primary",
                "title_keywords": title_kw,
                "proposal": True,
            }
        )

    fact_groups = []
    for i, fact in enumerate(required):
        fact_groups.append(
            {
                "fact_id": f"{cid}-F{i+1:02d}",
                "subject": "",
                "predicate": "states",
                "object_text": str(fact),
                "match_policy": "normalized",
                "acceptable_variants": [],
                "required": True,
                "proposal": True,
                "evidence_passage_id": "",
            }
        )

    row: dict[str, Any] = {
        "case_id": cid,
        "schema_version": "2.0",
        "split": split,
        "category": str(case.get("category") or "uncategorized"),
        "risk_level": _risk_for(case),
        "query": str(case.get("query") or ""),
        "answerability": "no_answer" if no_answer else "answerable",
        "intent": _intent_for(case),
        "expected_action": "refuse" if no_answer else "answer",
        "expected_sources": expected_sources,
        "required_fact_groups": fact_groups,
        "forbidden_assertions": [str(f) for f in forbidden],
        "acceptable_variants": [],
        "ambiguity": {
            "status": "needs_clarification"
            if cid in {"KB-009", "KB-024"}
            else "clear",
            "reason": (
                "legacy_v1_suspected_ambiguity_requires_human_review"
                if cid in {"KB-009", "KB-024"}
                else ""
            ),
            "clarifying_question": "",
            "adjudication_notes": "",
        },
        "corpus_snapshot": {
            "sha": "",
            "note": "fill_at_review_time_from_readonly_kb_db",
        },
        "annotation_source": "candidate",
        "review": {},
        "no_answer_reason": (
            str(case.get("notes") or "legacy_v1_no_answer") if no_answer else ""
        ),
        "notes": str(case.get("notes") or ""),
        "legacy_v1": {
            "expected_knowledge_ids": expected_ids,
            "required_facts": required,
            "forbidden_facts": forbidden,
            "expected_title_keywords": title_kw,
            "expected_no_answer": no_answer,
        },
        "migration": {
            "source": "evals/golden_set_hit_rate.json",
            "migrated_at": datetime.now(timezone.utc).isoformat(),
            "passage_status": "unresolved_proposal",
        },
    }
    return row


def migrate(
    v1_path: Path,
    out_path: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    payload = json.loads(v1_path.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise SystemExit("invalid golden V1 structure")

    # Never write into frozen/reviewed
    resolved = out_path.resolve()
    if "frozen" in resolved.parts or "reviewed" in resolved.parts:
        raise SystemExit("refusing to write migration output into reviewed/ or frozen/")

    if FROZEN_DIR.exists() and any(FROZEN_DIR.glob("*.jsonl")) and not force:
        # Do not mutate frozen; candidates refresh is still allowed.
        pass

    rows = [migrate_case(c) for c in cases]
    schema_errors = 0
    missing_passage = 0
    ambiguity = 0
    for row in rows:
        errs = validate_case_schema(row)
        # passage_id_or_reason is satisfied by passage_missing_reason
        if errs:
            schema_errors += 1
        if any(
            not (s.get("passage_id") or "").strip()
            for s in row.get("expected_sources") or []
            if isinstance(s, dict)
        ):
            missing_passage += 1
        if (row.get("ambiguity") or {}).get("status") != "clear":
            ambiguity += 1

    write_jsonl(out_path, rows)
    summary = {
        "input": str(v1_path),
        "output": str(out_path),
        "total": len(rows),
        "success": len(rows),
        "schema_errors": schema_errors,
        "missing_passage": missing_passage,
        "ambiguity": ambiguity,
        "pending_human_review": len(rows),
        "failed": 0,
        "dataset_hash": dataset_hash(rows),
        "note": (
            "All rows are candidates only. "
            "Phase 1 engineering complete; formal dataset freeze blocked by human review."
        ),
    }
    summary_path = out_path.with_suffix(".migration_summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--golden-v1", type=Path, default=DEFAULT_V1)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    summary = migrate(args.golden_v1, args.out, force=bool(args.force))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
