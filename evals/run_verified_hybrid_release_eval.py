"""Same-source release A/B using real SearchService + VerifiedAnswerService."""
from __future__ import annotations

import argparse
import json
from typing import Any

from evals.verified_hybrid_release.dataset import build_cases, validate_cases
from src.models.wiki_v2 import Claim, ClaimServingValidation, ClaimStatus, Evidence, EvidenceStance, normalize_statement
from src.services.search_service import SearchService
from src.services.verified_answer import VerifiedAnswerService
from src.services.wiki_claim_extractor import compute_excerpt_hash
from src.services.wiki_serving_gate import ServingGateConfig, WikiServingGate


class _Blocks:
    def __init__(self, case: dict) -> None: self._case = case
    def search(self, query: str, top_k: int = 5) -> list[dict]:
        return [{"id": self._case["block_id"], "text": self._case["block_text"], "score": 1.0,
                 "metadata": {"knowledge_id": self._case["knowledge_id"], "block_id": self._case["block_id"], "title": "release"}}]


class _Repo:
    def __init__(self, claim: Claim) -> None: self._claim = claim
    def list_claims(self) -> list[Claim]: return [self._claim]


def _claim(case: dict) -> Claim:
    evidence = Evidence(evidence_id=f"ev-{case['id']}", stance=EvidenceStance.SUPPORTS,
                        knowledge_id=case["knowledge_id"], block_id=case["block_id"], excerpt_hash=compute_excerpt_hash(case["block_text"]), observed_at="t")
    claim = Claim(schema_version=1, claim_id=f"claim-{case['id']}", statement=case["claim_statement"],
                  normalized_statement=normalize_statement(case["claim_statement"]), claim_type="fact", status=ClaimStatus.ACTIVE,
                  confidence=1.0, valid_from=None, valid_to=None, subject_refs=[], predicate="is", object_refs=[], evidence=[evidence], relations=[], created_at="t", updated_at="t", revision=1)
    claim.serving_validation = ClaimServingValidation(True, True, 1, 1, [evidence.evidence_id], "release-eval/v1", "t")
    return claim


def _ask(case: dict, hybrid: bool) -> dict[str, Any]:
    blocks = _Blocks(case)
    config = {"knowledge_workflow": {"mode": "verified" if hybrid else "evidence_only"}, "rag": {"enable_rerank": False, "verified_knowledge": {"enabled": hybrid}}}
    claim = _claim(case)
    gate = WikiServingGate(config=ServingGateConfig(require_validation_passed=True, require_review_approved=True, require_published_revision=True),
                           get_block=lambda _: {"id": case["block_id"], "content": case["block_text"]},
                           get_knowledge=lambda _: {"id": case["knowledge_id"], "deleted_at": None})
    search = SearchService(config=config, block_store=blocks, wiki_repository=_Repo(claim) if hybrid else None, wiki_serving_gate=gate)
    return VerifiedAnswerService(search, config=config).ask(case["question"], use_llm=False)


def run() -> dict[str, Any]:
    cases = build_cases()
    errors = validate_cases(cases)
    rows = []
    for case in cases:
        raw, hybrid = _ask(case, False), _ask(case, True)
        raw_text = "\n".join(str(source.get("text", "")) for source in raw.get("sources", []))
        hybrid_text = "\n".join(str(source.get("text", "")) for source in hybrid.get("sources", []))
        rows.append({"id": case["id"], "category": case["category"], "raw_mode": raw["answer_mode"], "hybrid_mode": hybrid["answer_mode"],
                     "raw_correct": case["expected"] in raw_text, "hybrid_correct": case["expected"] in hybrid_text,
                     "verified_claim_count": sum(1 for source in hybrid.get("sources", []) if source.get("source_layer") == "canonical")})
    raw_accuracy = sum(row["raw_correct"] for row in rows) / len(rows)
    hybrid_accuracy = sum(row["hybrid_correct"] for row in rows) / len(rows)
    benefit = [row for row in rows if row["category"] == "claim_benefit"]
    verified = sum(row["verified_claim_count"] for row in rows)
    lift = (sum(row["hybrid_correct"] for row in benefit) - sum(row["raw_correct"] for row in benefit)) / len(benefit)
    return {"total": len(rows), "raw_accuracy": raw_accuracy, "hybrid_accuracy": hybrid_accuracy, "claim_benefit_lift": lift, "verified_claim_count": verified, "rows": rows, "errors": errors,
            "overall_pass": not errors and hybrid_accuracy >= raw_accuracy and lift >= 0.05 and verified > 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    report = run()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        from pathlib import Path
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["overall_pass"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
