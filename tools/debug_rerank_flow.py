"""Diagnose rerank_score flow for KB-009 / KB-011 / KB-013.

Checks whether ApiReranker is actually invoked and whether rerank_score
propagates from RawRetriever → CandidateRetrievalService → EvidenceSnapshotService.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("diag")

CASES = {
    "KB-009": "员工出差的住宿费和伙食补助每天能报多少",
    "KB-011": "公司和外部商家合作卖东西的线上店铺入驻门槛",
    "KB-013": "公司搞比赛给员工发奖金 上限是多少",
}


def main() -> int:
    from src.core.container import create_container
    from src.services.rerankers.factory import create_reranker

    container = create_container()
    config = container.config

    # 1. Verify reranker instance type from factory.
    reranker = create_reranker(config=config, llm=container.llm)
    LOG.warning("factory reranker type: %s", type(reranker).__name__)

    # 2. Invoke reranker directly with a tiny candidate set to confirm it sets
    #    rerank_score.
    probe_candidates = [
        {"knowledge_id": "x1", "title": "劳动竞赛奖励办法", "text": "本办法规范劳动竞赛奖励的发放上限。"},
        {"knowledge_id": "x2", "title": "差旅费管理办法", "text": "本办法规范差旅费报销标准。"},
    ]
    out = reranker.rerank("公司搞比赛给员工发奖金 上限是多少", probe_candidates, top_n=2)
    for c in out:
        LOG.warning("  direct rerank cand=%s rerank_score=%s",
                    c.get("knowledge_id"), c.get("rerank_score"))

    # 3. Now run the actual retrieval path for each case and inspect the
    #    candidates that reach the snapshot.
    from src.application.candidate_retrieval_service import CandidateRetrievalService
    from src.application.evidence_snapshot_service import (
        EvidenceSnapshotService,
        SnapshotBuildRequest,
    )

    crs = CandidateRetrievalService(container)
    ess = EvidenceSnapshotService(crs, config=config, container=container)

    for case_id, query in CASES.items():
        print("\n" + "=" * 60)
        print(f"{case_id}: {query}")
        print("=" * 60)
        try:
            cands = crs.retrieve_candidates(query, fetch_k=10)
        except Exception as exc:
            LOG.exception("retrieve_candidates failed: %s", exc)
            continue

        print(f"  retrieved {len(cands)} candidates")
        for i, c in enumerate(cands[:6]):
            print(f"    [{i}] kid={c.get('knowledge_id','')[:8]}.. "
                  f"title={(c.get('title') or '')[:40]!r}")
            print(f"        score={c.get('score')} "
                  f"vector={c.get('vector_score')} "
                  f"fts={c.get('fts_score')} "
                  f"rerank={c.get('rerank_score')} "
                  f"alias={c.get('alias_fts_match')}")

        snap = ess.build(SnapshotBuildRequest(query=query, top_k=5, threshold=0.35))
        print(f"  snapshot: accept={snap.get('accept')} "
              f"top_score={snap.get('top_score')} reason={snap.get('reason')}")
        accepted = snap.get("accepted_items") or []
        print(f"  accepted {len(accepted)} items")
        for i, it in enumerate(accepted[:3]):
            print(f"    [acc {i}] kid={it.get('knowledge_id','')[:8]}.. "
                  f"final_rel={it.get('final_relevance_score')} "
                  f"title={(it.get('title') or '')[:40]!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
