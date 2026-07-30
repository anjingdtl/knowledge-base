"""Diagnose why KB-013 劳动竞赛 doc is rejected by relevance gate."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

QUERY = "公司搞比赛给员工发奖金 上限是多少"


def main() -> int:
    from src.core.container import create_container
    from src.services.relevance_gate import (
        evaluate_evidence,
        extract_query_terms,
        score_candidate_relevance,
    )

    container = create_container()
    from src.application.candidate_retrieval_service import CandidateRetrievalService

    crs = CandidateRetrievalService(container)
    cands = crs.retrieve_candidates(QUERY, fetch_k=10)

    # Find the 劳动竞赛 candidate.
    target = None
    for c in cands:
        title = c.get("title") or ""
        if "劳动竞赛" in title:
            target = c
            break

    if target is None:
        print("劳动竞赛 candidate NOT FOUND in retrieved set")
        return 1

    print("=" * 60)
    print("Target candidate (劳动竞赛):")
    print(f"  kid: {target.get('knowledge_id')}")
    print(f"  title: {target.get('title')}")
    print(f"  text[:200]: {(target.get('text') or '')[:200]!r}")
    print(f"  score: {target.get('score')}")
    print(f"  alias_fts_match: {target.get('alias_fts_match')}")
    print(f"  vector_score: {target.get('vector_score')}")
    print(f"  fts_score: {target.get('fts_score')}")
    print(f"  rerank_score: {target.get('rerank_score')}")

    terms = extract_query_terms(QUERY)
    print(f"\n  query terms ({len(terms)}): {sorted(terms)}")

    # Score this single candidate.
    scored = score_candidate_relevance(QUERY, target, threshold=0.35)
    print(f"\n  score_candidate_relevance result:")
    for k, v in sorted(scored.items()):
        print(f"    {k}: {v}")

    # Evaluate evidence with just this one candidate.
    decision = evaluate_evidence(QUERY, [target], threshold=0.35)
    print(f"\n  evaluate_evidence decision:")
    print(f"    accept: {decision.get('accept')}")
    print(f"    reason: {decision.get('reason')}")
    print(f"    top_score: {decision.get('top_score')}")
    items = decision.get("items") or []
    for it in items:
        print(f"    item kid={it.get('knowledge_id','')[:8]} "
              f"final_rel={it.get('final_relevance_score')} "
              f"reason={it.get('rejection_reason')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
