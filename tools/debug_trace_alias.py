"""Trace alias_fts_match flow in package_raw_candidates."""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING)

import src.retrieval.raw_retriever as rr
_orig = rr.RawRetriever.package_raw_candidates

def _traced(self, query, candidates, top_k=10, citation_builder=None):
    print(f"TRACE: query={query!r} n_cands={len(candidates)}")
    for c in candidates[:3]:
        t = c.get("title") or (c.get("metadata") or {}).get("title") or ""
        print(f"  RAW: title={t[:50]!r}")
    out = _orig(self, query, candidates, top_k=top_k, citation_builder=citation_builder)
    for e in out[:3]:
        alias = e.get("alias_fts_match")
        title = (e.get("title") or "")[:50]
        print(f"  OUT: alias={alias} title={title!r}")
    return out

rr.RawRetriever.package_raw_candidates = _traced

from src.core.container import create_container
from src.application.candidate_retrieval_service import CandidateRetrievalService

container = create_container()
crs = CandidateRetrievalService(container)
cands = crs.retrieve_candidates("公司搞比赛给员工发奖金 上限是多少", fetch_k=10)
