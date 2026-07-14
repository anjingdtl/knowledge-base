"""Verified Hybrid fusion orchestration (algorithm-equivalent extraction).

Scoring formulas remain in ``src.services.verified_hybrid_fusion`` —
this module only owns channel coordination, packaging, stale filter,
conflict scan, and SearchExecution side-channels.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, Callable

from src.retrieval.packaging import SearchRequestState
from src.services.citation_builder import CitationBuilder

logger = logging.getLogger(__name__)


class VerifiedFusion:
    """Coordinate Raw channel + VerifiedProvider claims → fused primary list."""

    def __init__(
        self,
        *,
        config: Any = None,
        db: Any = None,
        block_store: Any = None,
        stage_timeout_fn: Callable[[str], float] | None = None,
        verified_cfg_fn: Callable[[str, Any], Any] | None = None,
        rewrite_fn: Callable[[str], list[str]] | None = None,
        timed_hybrid_fn: Callable[[list[str], int], list[dict]] | None = None,
        timed_rerank_fn: Callable[[str, list[dict], int], list[dict]] | None = None,
        diversity_fn: Callable[..., list[dict]] | None = None,
        knowledge_fts_fn: Callable[[str, int], list[dict]] | None = None,
        claim_retrieve_fn: Callable[..., list] | None = None,
    ):
        self._config = config if config is not None else {}
        self._db = db
        self._block_store = block_store
        self._stage_timeout_fn = stage_timeout_fn
        self._verified_cfg_fn = verified_cfg_fn
        self._rewrite_fn = rewrite_fn
        self._timed_hybrid_fn = timed_hybrid_fn
        self._timed_rerank_fn = timed_rerank_fn
        self._diversity_fn = diversity_fn
        self._knowledge_fts_fn = knowledge_fts_fn
        self._claim_retrieve_fn = claim_retrieve_fn

    def _stage_timeout(self, stage: str) -> float:
        if self._stage_timeout_fn is not None:
            return float(self._stage_timeout_fn(stage))
        return 30.0

    def _verified_cfg(self, key: str, default: Any = None) -> Any:
        if self._verified_cfg_fn is not None:
            return self._verified_cfg_fn(key, default)
        return default

    def run(
        self,
        query: str,
        *,
        top_k: int,
        state: SearchRequestState,
        t0: float | None = None,
    ) -> list[dict]:
        """Full verified-hybrid pipeline (line-equivalent to former SearchService method)."""
        from src.services.verified_hybrid_fusion import (
            claims_to_candidates,
            fuse_verified_and_raw,
            normalize_raw_candidate,
            package_fused_result,
        )
        from src.services.verified_query_router import merge_route_with_config, route_query

        _ = t0  # reserved for caller wall-clock logging
        state.trace["mode"] = "hybrid_verified"
        route = route_query(query)
        route = merge_route_with_config(
            route,
            config_wiki_weight=float(self._verified_cfg("wiki_weight", 0.40)),
            config_raw_weight=float(self._verified_cfg("raw_weight", 0.60)),
        )
        state.trace["route"] = route.to_dict()

        raw_mult = int(self._verified_cfg("raw_candidate_multiplier", 3) or 3)
        wiki_mult = int(self._verified_cfg("wiki_candidate_multiplier", 2) or 2)
        raw_top = max(top_k * raw_mult, top_k)
        wiki_limit = max(top_k * wiki_mult, top_k)

        queries = [query]
        claim_pairs: list = []
        wiki_error: str | None = None
        raw_candidates: list[dict] = []
        state.claim_error = None

        rewrite_fn = self._rewrite_fn or (lambda q: [q])
        timed_hybrid = self._timed_hybrid_fn or (lambda qs, k: [])
        claim_fn = self._claim_retrieve_fn

        with ThreadPoolExecutor(max_workers=3) as pool:
            rewrite_future = pool.submit(rewrite_fn, query)
            claim_future = (
                pool.submit(claim_fn, query, wiki_limit, state)
                if claim_fn is not None
                else None
            )
            hybrid_future = pool.submit(timed_hybrid, [query], raw_top)

            try:
                queries = rewrite_future.result(
                    timeout=self._stage_timeout("query_rewrite"),
                )
            except FuturesTimeout:
                logger.warning("Query rewrite timed out, using original query")
                queries = [query]
            except Exception as e:
                logger.warning("Query rewrite failed: %s", e)
                queries = [query]
            state.trace.setdefault("stages", {})["query_rewrite"] = {
                "count": len(queries),
            }

            if claim_future is not None:
                try:
                    claim_pairs = claim_future.result(
                        timeout=self._stage_timeout("wiki_search"),
                    )
                except FuturesTimeout:
                    wiki_error = "wiki_claim_timeout"
                    logger.warning("Verified claim retrieval timed out — raw continues")
                    claim_pairs = []
                except Exception as e:
                    wiki_error = f"wiki_claim_error:{e}"
                    logger.warning(
                        "Verified claim retrieval failed: %s — raw continues", e,
                    )
                    claim_pairs = []
                if wiki_error is None and state.claim_error:
                    wiki_error = f"wiki_claim_error:{state.claim_error}"

            try:
                raw_candidates = hybrid_future.result(
                    timeout=self._stage_timeout("hybrid_search"),
                )
            except FuturesTimeout:
                logger.warning("Hybrid search timed out in verified path")
                raw_candidates = []
            except Exception as e:
                logger.warning("Hybrid search failed in verified path: %s", e)
                raw_candidates = []

        if len(queries) > 1 and raw_candidates:
            try:
                more = timed_hybrid(queries, raw_top)
                by_id = {
                    str(c.get("id")): c for c in raw_candidates if c.get("id")
                }
                for c in more:
                    cid = str(c.get("id") or "")
                    if not cid:
                        continue
                    prev = by_id.get(cid)
                    if prev is None or float(c.get("rrf_score") or 0) > float(
                        prev.get("rrf_score") or 0,
                    ):
                        by_id[cid] = c
                raw_candidates = list(by_id.values())
            except Exception as e:  # noqa: BLE001
                logger.debug("Secondary hybrid with rewrites skipped: %s", e)

        if not raw_candidates:
            try:
                raw_candidates = (
                    self._block_store.search(query, top_k=raw_top)
                    if self._block_store
                    else []
                )
            except Exception:
                raw_candidates = []
        if not raw_candidates and self._knowledge_fts_fn is not None:
            raw_candidates = self._knowledge_fts_fn(query, raw_top)

        if raw_candidates and self._timed_rerank_fn is not None:
            try:
                raw_candidates = self._timed_rerank_fn(query, raw_candidates, raw_top)
            except FuturesTimeout:
                logger.warning("Rerank timed out in verified path")
                state.warnings.append("rerank_timeout")
            except Exception as e:
                logger.warning("Rerank failed in verified path: %s", e)
                state.warnings.append(f"rerank_failed:{e}")
            if self._diversity_fn is not None:
                raw_candidates = self._diversity_fn(raw_candidates, threshold=0.8)

        return self.package_fused_channels(
            query=query,
            top_k=top_k,
            claim_pairs=claim_pairs,
            raw_candidates=raw_candidates,
            wiki_error=wiki_error,
            wiki_limit=wiki_limit,
            route=route,
            state=state,
            claims_to_candidates=claims_to_candidates,
            fuse_verified_and_raw=fuse_verified_and_raw,
            normalize_raw_candidate=normalize_raw_candidate,
            package_fused_result=package_fused_result,
        )

    def package_fused_channels(
        self,
        *,
        query: str,
        top_k: int,
        claim_pairs: list,
        raw_candidates: list[dict],
        wiki_error: str | None,
        wiki_limit: int,
        route: Any,
        state: SearchRequestState,
        claims_to_candidates,
        fuse_verified_and_raw,
        normalize_raw_candidate,
        package_fused_result,
    ) -> list[dict]:
        """Normalize → fuse → package → stale filter → conflict scan."""
        claim_cands = claims_to_candidates(claim_pairs, query=query, limit=wiki_limit)
        raw_norm = [
            normalize_raw_candidate(r, rank=i)
            for i, r in enumerate(raw_candidates)
        ]

        state.trace.setdefault("stages", {})["verified_wiki"] = {
            "pairs": len(claim_pairs),
            "candidates": len(claim_cands),
            "error": wiki_error,
        }
        state.trace["stages"]["raw_retrieval"] = {"count": len(raw_norm)}
        if wiki_error:
            state.warnings.append(f"wiki_degraded:{wiki_error}")
            state.fallbacks.append({
                "from": "verified_wiki",
                "to": "raw_retrieval",
                "reason": str(wiki_error),
            })

        empty_wiki_ok = bool(self._verified_cfg("empty_wiki_fallback_to_raw", True))
        if not claim_cands and empty_wiki_ok:
            state.record_fallback("empty_wiki_to_raw")

        fused = fuse_verified_and_raw(
            claim_cands,
            raw_norm,
            wiki_weight=route.wiki_weight,
            raw_weight=route.raw_weight,
            top_n=top_k * 2,
        )
        state.trace["stages"]["fusion"] = {
            "count": len(fused),
            "wiki_weight": route.wiki_weight,
            "raw_weight": route.raw_weight,
        }

        citation_builder = CitationBuilder(self._db) if self._db is not None else None
        output: list[dict] = []
        disclose_rows: list[dict] = []
        seen = set()
        for cand in fused:
            key = (
                cand.get("candidate_type"),
                cand.get("candidate_id") or cand.get("id"),
            )
            if key in seen:
                continue
            seen.add(key)
            if cand.get("disclose_only") and cand.get("candidate_type") == "claim":
                state.trace.setdefault("disclose_claims", []).append(
                    cand.get("claim_id"),
                )
                packaged_disclose = package_fused_result(
                    cand, db=self._db, citation_builder=citation_builder, query=query,
                )
                packaged_disclose["disclose_only"] = True
                disclose_rows.append(packaged_disclose)
                continue
            packaged = package_fused_result(
                cand, db=self._db, citation_builder=citation_builder, query=query,
            )
            if packaged.get("source") == "verified_claim":
                if not packaged.get("evidence"):
                    continue
            output.append(packaged)
            if len(output) >= top_k:
                break

        if not output and raw_norm:
            state.record_fallback("fusion_empty_to_raw")
            for cand in raw_norm:
                output.append(package_fused_result(
                    cand, db=self._db, citation_builder=citation_builder, query=query,
                ))
                if len(output) >= top_k:
                    break

        try:
            from src.services.verified_conflict import (
                filter_stale_claims,
                is_freshness_sensitive_query,
            )
            if is_freshness_sensitive_query(query):
                claims = [r for r in output if r.get("source") == "verified_claim"]
                non_claims = [r for r in output if r.get("source") != "verified_claim"]
                kept, dropped = filter_stale_claims(claims, drop_stale=True)
                if dropped:
                    state.trace.setdefault("stages", {})["freshness_filter"] = {
                        "dropped_stale": len(dropped),
                        "ids": [d.get("claim_id") for d in dropped],
                    }
                    output = kept + non_claims
                    if not kept and non_claims:
                        state.record_fallback("stale_claims_to_raw")
        except Exception as e:  # noqa: BLE001
            logger.debug("freshness filter skipped: %s", e)

        try:
            from src.services.verified_conflict import detect_claim_conflicts

            claim_rows = [
                r for r in output if r.get("source") == "verified_claim"
            ] + disclose_rows
            conflicts = detect_claim_conflicts(claim_rows)
            if conflicts:
                state.conflicts = list(conflicts)
                state.trace["conflicts"] = conflicts
                state.trace["conflict_disclosed"] = True
        except Exception as e:  # noqa: BLE001
            logger.debug("conflict scan skipped: %s", e)

        state.disclose_claims = disclose_rows
        state.trace["result_count"] = len(output)
        state.trace["sources"] = {
            "verified_claim": sum(
                1 for r in output if r.get("source") == "verified_claim"
            ),
            "knowledge": sum(1 for r in output if r.get("source") == "knowledge"),
            "disclose_only": len(disclose_rows),
        }
        return output
