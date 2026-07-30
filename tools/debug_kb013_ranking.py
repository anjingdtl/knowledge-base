"""Detailed diagnosis of KB-013 ranking: 劳动竞赛 vs 技能竞赛."""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.WARNING)

QUERY = "公司搞比赛给员工发奖金 上限是多少"


def main() -> int:
    from src.core.container import create_container
    from src.application.candidate_retrieval_service import CandidateRetrievalService
    from src.services.relevance_gate import evaluate_evidence, score_candidate_relevance

    container = create_container()
    crs = CandidateRetrievalService(container)
    cands = crs.retrieve_candidates(QUERY, fetch_k=10)

    for c in cands:
        t = c.get("title") or ""
        if "竞赛" in t:
            kid = c.get("knowledge_id", "")[:12]
            alias = c.get("alias_fts_match")
            score = c.get("score")
            text = (c.get("text") or "")[:120]
            print(f"kid={kid} alias={alias} score={score:.4f}")
            print(f"  title: {t[:60]}")
            print(f"  text: {text!r}")
            scored = score_candidate_relevance(QUERY, c)
            print(f"  final_relevance: {scored.get('final_relevance_score')}")
            print(f"  query_term_coverage: {scored.get('query_term_coverage')}")
            print(f"  title_score: {scored.get('title_score')}")
            print(f"  semantic_score: {scored.get('semantic_score')}")
            print()

    decision = evaluate_evidence(QUERY, cands, threshold=0.35)
    print(f"accept={decision.get('accept')} top_score={decision.get('top_score')}")
    for item in decision.get("items", [])[:5]:
        kid = item.get("knowledge_id", "")[:12]
        final = item.get("final_relevance_score")
        title = (item.get("title") or "")[:40]
        print(f"  kid={kid} final={final} title={title!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
