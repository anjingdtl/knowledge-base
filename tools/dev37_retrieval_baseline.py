"""Phase 3.0 development-37 non-formal retrieval baseline.

Builds a deterministic, in-process retrieval-only baseline for the 37 Golden V2
candidate cases. NO live MCP server, NO LLM calls — only the search path via
the application layer (SearchUseCase → SearchService → RawRetriever).

Produces a sanitized artifact under ``.local/eval-runs/phase3-dev-baseline/``
clearly marked ``non_formal=true``. Does NOT touch formal/frozen paths.

Usage:
    python tools/dev37_retrieval_baseline.py
    python tools/dev37_retrieval_baseline.py --cases KB-009,KB-013

Failure taxonomy (case-level):
    missing_direct_hit  — expected_knowledge_id not in top-5
    wrong_product       — top-1 is a different regulation/product
    wrong_version       — top-1 is the right product but wrong year/version
    wrong_family        — top-1 is a related family member (e.g. branch vs HQ)
    contract_failure    — search returned no_match/low_confidence or routing
                          misfired (e.g. requires_current_external_data)
    not_assessed        — ask-side category (needs LLM); not evaluated here

The ask-side categories (unsupported_claim, stale_evidence, no_answer_failure,
citation_failure) are NOT assessed in this retrieval-only baseline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOG = logging.getLogger("dev37_baseline")

GOLDEN_PATH = ROOT / "evals" / "golden_set_hit_rate.json"
KB_DB_PATH = ROOT / "data" / "kb.db"
OUT_DIR = ROOT / ".local" / "eval-runs" / "phase3-dev-baseline"


# --------------------------------------------------------------------------- #
# Case-level failure taxonomy                                                 #
# --------------------------------------------------------------------------- #


def classify_case(
    case: dict[str, Any],
    search_result: dict[str, Any],
) -> dict[str, Any]:
    """Classify one case into the failure taxonomy.

    Returns a dict with:
        failure_category: one of missing_direct_hit / wrong_product /
            wrong_version / wrong_family / contract_failure / none
        failure_reason: human-readable explanation
        top1_id / top1_title: top-1 candidate metadata
        expected_in_top5: bool
        no_match: bool (search returned no_match=true)
        low_confidence: bool (search returned low_confidence=true)
    """
    expected_ids = set(case.get("expected_knowledge_ids") or [])
    expected_keywords = [
        kw.lower() for kw in (case.get("expected_title_keywords") or [])
    ]

    data = search_result.get("data") or []
    meta = search_result.get("meta") or {}
    no_match = bool(meta.get("no_match"))
    low_confidence = bool(meta.get("low_confidence"))
    source_path = str(meta.get("source_path") or "")

    top1 = data[0] if data else {}
    top1_id = str(top1.get("knowledge_id") or top1.get("id") or "")
    top1_title = str(top1.get("title") or "")
    top1_title_l = top1_title.lower()

    cand_ids = [
        str(c.get("knowledge_id") or c.get("id") or "").strip()
        for c in data
        if c.get("knowledge_id") or c.get("id")
    ]
    expected_in_top5 = any(eid in cand_ids for eid in expected_ids) if expected_ids else False

    category = "none"
    reason = ""

    # Contract failure: search short-circuited (no_match / current_info_gate)
    if no_match or source_path == "current_info_gate":
        category = "contract_failure"
        reason = f"search returned no_match/current_info_gate (source_path={source_path})"
    elif low_confidence:
        category = "contract_failure"
        reason = f"search returned low_confidence (source_path={source_path})"
    elif not data:
        category = "missing_direct_hit"
        reason = "search returned empty data"
    elif expected_ids and not expected_in_top5:
        category = "missing_direct_hit"
        reason = "expected_knowledge_ids not in top-5"
    elif expected_ids and expected_in_top5:
        # Hit — check if top-1 is the expected one.
        if top1_id in expected_ids:
            category = "none"
            reason = "top-1 is an expected_knowledge_id"
        else:
            # Top-1 is NOT expected but expected is in top-5.
            # Decide wrong_product vs wrong_version vs wrong_family.
            # Heuristic: check expected_title_keywords against top-1 title.
            if expected_keywords:
                kw_hits = sum(1 for kw in expected_keywords if kw in top1_title_l)
                if kw_hits == 0:
                    category = "wrong_product"
                    reason = (
                        f"top-1 '{top1_title}' shares no expected title keyword; "
                        "likely a different regulation."
                    )
                else:
                    # Has some keyword overlap → likely same product, wrong version/family.
                    # Distinguish by year token in title.
                    import re

                    top1_year = re.search(r"(20\d{2})", top1_title)
                    expected_years = [
                        re.search(r"(20\d{2})", kw) for kw in expected_keywords
                    ]
                    expected_years = [m.group(1) for m in expected_years if m]
                    if (
                        top1_year
                        and expected_years
                        and top1_year.group(1) not in expected_years
                    ):
                        category = "wrong_version"
                        reason = (
                            f"top-1 year {top1_year.group(1)} not in expected "
                            f"years {expected_years}"
                        )
                    else:
                        category = "wrong_family"
                        reason = (
                            f"top-1 '{top1_title}' shares keywords but is a "
                            "different family member (e.g. branch vs HQ)."
                        )
            else:
                category = "wrong_family"
                reason = "top-1 is not expected but no keywords to classify further"

    return {
        "failure_category": category,
        "failure_reason": reason,
        "top1_id": top1_id,
        "top1_title": top1_title,
        "top5_ids": cand_ids,
        "expected_in_top5": expected_in_top5,
        "no_match": no_match,
        "low_confidence": low_confidence,
        "source_path": source_path,
        "top_score": meta.get("top_score"),
    }


# --------------------------------------------------------------------------- #
# Search execution                                                             #
# --------------------------------------------------------------------------- #


def run_search(container: Any, query: str, top_k: int = 5) -> dict[str, Any]:
    """Run one search via the same gated path as MCP ``search`` tool.

    Uses ``is_current_information_query`` (live-external gate) +
    ``EvidenceSnapshotService.build`` (canonical snapshot with threshold 0.35,
    freshness, adjacent allowlist) — identical to the MCP ``search`` tool path.
    """
    from src.services.relevance_gate import is_current_information_query

    threshold = 0.35

    # Gate 1: live-external short-circuit (same as MCP search tool).
    if is_current_information_query(query):
        return {
            "ok": True,
            "data": [],
            "meta": {
                "no_match": True,
                "low_confidence": False,
                "source_path": "current_info_gate",
                "top_score": 0.0,
                "threshold": threshold,
                "reason": "requires_current_external_data",
            },
        }

    # Gate 2: canonical snapshot with threshold + freshness + allowlist.
    try:
        from src.application.evidence_snapshot_service import SnapshotBuildRequest
        snapshot_svc = getattr(container, "evidence_snapshot_service", None)
        if snapshot_svc is None:
            # Fallback: construct directly (test doubles)
            from src.application.candidate_retrieval_service import (
                CandidateRetrievalService,
            )
            from src.application.evidence_snapshot_service import (
                EvidenceSnapshotService,
            )
            snapshot_svc = EvidenceSnapshotService(
                CandidateRetrievalService(container),
                config=getattr(container, "config", None),
                container=container,
            )

        snapshot = snapshot_svc.build(
            SnapshotBuildRequest(query=query, top_k=top_k, threshold=threshold),
        )

        if snapshot.get("accept") and snapshot.get("accepted_items"):
            data = list(snapshot["accepted_items"])[:top_k]
            return {
                "ok": True,
                "data": data,
                "meta": {
                    "no_match": False,
                    "low_confidence": False,
                    "source_path": "canonical_snapshot",
                    "top_score": snapshot.get("top_score", 0.0),
                    "threshold": threshold,
                    "reason": snapshot.get("reason", ""),
                    "intent": snapshot.get("intent"),
                    "accepted_knowledge_ids": list(
                        snapshot.get("accepted_knowledge_ids") or []
                    ),
                },
            }

        return {
            "ok": True,
            "data": [],
            "meta": {
                "no_match": True,
                "low_confidence": False,
                "source_path": "canonical_snapshot",
                "top_score": snapshot.get("top_score", 0.0),
                "threshold": threshold,
                "reason": snapshot.get("reason") or "all_candidates_below_threshold",
            },
        }
    except Exception as exc:  # noqa: BLE001
        LOG.exception("search failed for query=%r", query[:60])
        return {
            "ok": False,
            "data": [],
            "meta": {"no_match": True, "source_path": "exception"},
            "error": f"{type(exc).__name__}: {exc}",
        }


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #


def _kb_db_sha256() -> str:
    try:
        data = KB_DB_PATH.read_bytes()
        return hashlib.sha256(data).hexdigest()
    except Exception as exc:  # noqa: BLE001
        return f"unreadable: {exc}"


def _init_container() -> Any:
    """Initialize the production container in-process (read-only)."""
    from src.core.container import create_container

    container = create_container()
    return container


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--cases",
        default="",
        help="Comma-separated case_id filter (e.g. KB-009,KB-013)",
    )
    ap.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Search top_k (default 5)",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    case_filter = (
        {c.strip() for c in args.cases.split(",") if c.strip()}
        if args.cases
        else None
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    LOG.info("Loading golden set from %s", GOLDEN_PATH)
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = golden.get("cases") or []
    if case_filter:
        cases = [c for c in cases if c.get("case_id") in case_filter]
    LOG.info("Evaluating %d cases (filter=%s)", len(cases), case_filter)

    kb_hash = _kb_db_sha256()
    LOG.info("data/kb.db SHA256=%s", kb_hash)

    LOG.info("Initializing container (read-only)...")
    container = _init_container()
    LOG.info("Container ready; search_service=%s", type(container.search_service).__name__)

    started = time.monotonic()
    case_details: list[dict[str, Any]] = []
    taxonomy_counts: dict[str, int] = {
        "none": 0,
        "missing_direct_hit": 0,
        "wrong_product": 0,
        "wrong_version": 0,
        "wrong_family": 0,
        "contract_failure": 0,
    }

    for case in cases:
        case_id = case.get("case_id") or case.get("id") or "?"
        query = case.get("query") or ""
        expected_ids = case.get("expected_knowledge_ids") or []
        expected_no_answer = bool(case.get("expected_no_answer"))

        LOG.info("Case %s: %s", case_id, query[:60])
        search_result = run_search(container, query, top_k=args.top_k)
        classification = classify_case(case, search_result)

        # Track expected_no_answer cases separately.
        if expected_no_answer:
            # For no-answer cases, a no_match/low_confidence result is CORRECT.
            if classification["no_match"] or classification["low_confidence"]:
                classification["failure_category"] = "none"
                classification["failure_reason"] = (
                    "expected_no_answer case correctly returned no_match/low_confidence"
                )
            elif classification["failure_category"] == "none":
                # Search returned hits but case expects no answer — possible FP.
                classification["failure_category"] = "contract_failure"
                classification["failure_reason"] = (
                    "expected_no_answer but search returned accepted hits (possible false positive)"
                )

        taxonomy_counts[classification["failure_category"]] = (
            taxonomy_counts.get(classification["failure_category"], 0) + 1
        )

        case_details.append(
            {
                "case_id": case_id,
                "category": case.get("category"),
                "query": query,
                "expected_knowledge_ids": expected_ids,
                "expected_title_keywords": case.get("expected_title_keywords") or [],
                "required_facts": case.get("required_facts") or [],
                "expected_no_answer": expected_no_answer,
                "search_ok": search_result.get("ok", False),
                "search_error": search_result.get("error"),
                "search_meta": search_result.get("meta") or {},
                "top1_id": classification["top1_id"],
                "top1_title": classification["top1_title"],
                "top5_ids": classification["top5_ids"],
                "expected_in_top5": classification["expected_in_top5"],
                "failure_category": classification["failure_category"],
                "failure_reason": classification["failure_reason"],
                "not_assessed": [
                    "unsupported_claim",
                    "stale_evidence",
                    "no_answer_failure",
                    "citation_failure",
                ],
            }
        )

    elapsed = time.monotonic() - started

    # Aggregate metrics (retrieval-only).
    answerable = [c for c in case_details if not c["expected_no_answer"]]
    no_answer = [c for c in case_details if c["expected_no_answer"]]
    answerable_hit = sum(1 for c in answerable if c["expected_in_top5"])
    answerable_top1 = sum(
        1
        for c in answerable
        if c["expected_in_top5"] and c["top1_id"] in c["expected_knowledge_ids"]
    )
    no_answer_correct = sum(
        1
        for c in no_answer
        if c["failure_category"] == "none"
    )

    artifact = {
        "meta": {
            "artifact_type": "development_retrieval_baseline",
            "non_formal": True,
            "dev_only": True,
            "formal": False,
            "metric_contract_version": "2.0",
            "scorer": "Scorer V2 (retrieval-only subset)",
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "golden_path": str(GOLDEN_PATH.relative_to(ROOT)),
            "kb_db_sha256": kb_hash,
            "golden_v2": {
                "candidates": len(case_details),
                "reviewed": 0,
                "frozen": 0,
            },
            "evaluated_cases": len(case_details),
            "elapsed_s": round(elapsed, 2),
            "top_k": args.top_k,
            "case_filter": sorted(case_filter) if case_filter else None,
            "release_verdict": "NO-GO (frozen=0; development/non-formal only)",
            "note": (
                "Retrieval-only in-process baseline. Ask/E2E/citation categories "
                "are NOT assessed (require live LLM). Do not interpret as formal "
                "release metrics."
            ),
        },
        "metrics": {
            "answerable_total": len(answerable),
            "no_answer_total": len(no_answer),
            "recall5_correct": answerable_hit,
            "top1_correct": answerable_top1,
            "no_answer_correct": no_answer_correct,
            "Recall@5": round(answerable_hit / len(answerable), 4) if answerable else None,
            "Top-1 Accuracy": round(answerable_top1 / len(answerable), 4) if answerable else None,
            "No-Answer Correct": round(no_answer_correct / len(no_answer), 4) if no_answer else None,
        },
        "failure_taxonomy": taxonomy_counts,
        "not_assessed_categories": [
            "unsupported_claim",
            "stale_evidence",
            "no_answer_failure",
            "citation_failure",
        ],
        "case_details": case_details,
    }

    out_path = OUT_DIR / "dev37_retrieval_baseline.sanitized.json"
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("Wrote %s", out_path)

    # Console summary.
    print("\n" + "=" * 60)
    print("Development-37 Retrieval Baseline (NON-FORMAL)")
    print("=" * 60)
    print(f"Cases evaluated: {len(case_details)}")
    print(f"  answerable:    {len(answerable)}")
    print(f"  no_answer:     {len(no_answer)}")
    print(f"kb.db SHA256:    {kb_hash}")
    print(f"elapsed:         {elapsed:.2f}s")
    print()
    print("Retrieval metrics (dev only, NOT formal):")
    print(f"  Recall@5:        {answerable_hit}/{len(answerable)} "
          f"= {artifact['metrics']['Recall@5']}")
    print(f"  Top-1 Accuracy:  {answerable_top1}/{len(answerable)} "
          f"= {artifact['metrics']['Top-1 Accuracy']}")
    if no_answer:
        print(f"  No-Answer Correct: {no_answer_correct}/{len(no_answer)} "
              f"= {artifact['metrics']['No-Answer Correct']}")
    print()
    print("Failure taxonomy (case-level):")
    for cat, count in sorted(taxonomy_counts.items(), key=lambda x: -x[1]):
        if count:
            print(f"  {cat:25s} {count}")
    print()
    print(f"NOT assessed (require LLM): unsupported_claim, stale_evidence, "
          f"no_answer_failure, citation_failure")
    print(f"Artifact: {out_path}")
    print(f"Formal release verdict: {artifact['meta']['release_verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
