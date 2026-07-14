"""Raw evidence retriever — algorithm authority for evidence-only path.

WP1-T1 maintainability closure: query rewrite, hybrid/FTS fallback, rerank,
diversity, packaging, and stage traces live here. Construct with explicit
dependencies only (no whole search-service instance). Does not touch MCP,
Graph, Memory, or Wiki Authoring.
"""
from __future__ import annotations

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, Callable, cast

from src.retrieval.models import RawRetrievalResult
from src.services.citation_builder import CitationBuilder
from src.services.hybrid_search import HybridSearcher
from src.services.query_rewriter import QueryRewriter
from src.services.reranker import LLMReranker

logger = logging.getLogger(__name__)

_STAGE_TIMEOUTS = {
    "query_rewrite": 15,
    "hybrid_search": 25,
    "rerank": 20,
    "wiki_search": 5,
}


class RawRetriever:
    """Evidence retrieval capability with explicit constructor dependencies."""

    def __init__(
        self,
        *,
        config: Any = None,
        db: Any = None,
        block_store: Any = None,
        llm: Any = None,
        hybrid_searcher: Any = None,
        query_rewriter: Callable[[str], list[str]] | None = None,
        reranker: Callable[[str, list[dict], int], list[dict]] | None = None,
        hybrid_search_fn: Callable[[list[str], int], list[dict]] | None = None,
        knowledge_fts_fn: Callable[[str, int], list[dict]] | None = None,
        wiki_search_fn: Callable[[str], list[dict]] | None = None,
        package_raw_fn: Callable[..., list[dict]] | None = None,
        diversity_fn: Callable[..., list[dict]] | None = None,
        citation_builder_factory: Callable[[Any], Any] | None = None,
        stage_timeout_fn: Callable[[str], float] | None = None,
    ):
        self._config = config if config is not None else {}
        self._db = db
        self._block_store = block_store
        self._llm = llm
        self._hybrid_searcher = hybrid_searcher
        self._query_rewriter_fn = query_rewriter
        self._reranker_fn = reranker
        self._hybrid_search_fn = hybrid_search_fn
        self._knowledge_fts_fn = knowledge_fts_fn
        self._wiki_search_fn = wiki_search_fn
        self._package_raw_fn = package_raw_fn
        self._diversity_fn = diversity_fn
        self._citation_builder_factory = citation_builder_factory
        self._stage_timeout_fn = stage_timeout_fn

    def _cfg(self, key: str, default: Any = None) -> Any:
        if isinstance(self._config, dict):
            parts = key.split(".")
            obj: object = self._config
            for p in parts:
                if isinstance(obj, dict):
                    obj = obj.get(p)
                else:
                    return default
            return obj if obj is not None else default
        getter = getattr(self._config, "get", None)
        if callable(getter):
            return getter(key, default)
        return default

    def _stage_timeout(self, stage: str) -> float:
        if self._stage_timeout_fn is not None:
            return float(self._stage_timeout_fn(stage))
        cfg_key = f"rag.stage_timeout.{stage}"
        custom = self._cfg(cfg_key)
        return float(custom or _STAGE_TIMEOUTS.get(stage, 30))

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        include_legacy_wiki_fts: bool = True,
    ) -> RawRetrievalResult:
        """Run raw retrieval pipeline (rewrite → hybrid/fallback → rerank → diversity → package)."""
        t0 = time.monotonic()
        trace: dict[str, Any] = {
            "mode": "legacy_raw",
            "query": (query or "")[:200],
            "stages": {},
        }
        warnings: list[str] = []
        fallbacks: list[dict[str, Any]] = []

        queries = [query]
        wiki_results: list[dict] = []

        with ThreadPoolExecutor(max_workers=2) as pool:
            rewrite_future = pool.submit(self.rewrite_query, query)
            wiki_future = (
                pool.submit(self.safe_wiki_search, query)
                if include_legacy_wiki_fts
                else None
            )

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

            if wiki_future is not None:
                try:
                    wiki_results = wiki_future.result(
                        timeout=self._stage_timeout("wiki_search"),
                    )
                except FuturesTimeout:
                    logger.warning("Wiki search timed out")
                    wiki_results = []
                except Exception as e:
                    logger.warning("Wiki search failed: %s", e)
                    wiki_results = []

        trace["stages"]["query_rewrite"] = {"count": len(queries)}
        if include_legacy_wiki_fts:
            trace["stages"]["legacy_wiki_fts"] = {"count": len(wiki_results)}

        candidates = self.raw_retrieve(queries, query, top_k)
        trace["stages"]["raw_retrieval"] = {"count": len(candidates)}

        if candidates:
            try:
                candidates = self.timed_rerank(query, candidates, top_k)
            except FuturesTimeout:
                logger.warning("Rerank timed out, keeping original order")
                warnings.append("rerank_timeout")
            except Exception as e:
                logger.warning("Rerank failed: %s", e)
                warnings.append(f"rerank_failed:{e}")

        if candidates:
            candidates = self.diversity_filter(candidates, threshold=0.8)

        output: list[dict] = []
        if include_legacy_wiki_fts:
            output.extend(wiki_results)
        output.extend(self.package_raw_candidates(query, candidates, top_k=top_k))

        elapsed = time.monotonic() - t0
        logger.info(
            "Raw retrieval completed in %.2fs: %d results for query=%r",
            elapsed,
            len(output),
            (query or "")[:50],
        )
        trace["elapsed_ms"] = round(elapsed * 1000, 2)
        trace["result_count"] = len(output)

        return RawRetrievalResult(
            candidates=tuple(output),
            trace=trace,
            warnings=tuple(warnings),
            fallbacks=tuple(fallbacks),
        )

    def rewrite_query(self, query: str) -> list[str]:
        if self._query_rewriter_fn is not None:
            return self._query_rewriter_fn(query)
        enabled = self._cfg("rag.enable_query_rewriting", False)
        if not enabled:
            return [query]
        try:
            rewriter = QueryRewriter(self._llm, self._config)
            return rewriter.rewrite(query)
        except Exception as e:
            logger.warning("Query rewrite failed: %s", e)
            return [query]

    def raw_retrieve(self, queries: list[str], query: str, top_k: int) -> list[dict]:
        try:
            candidates = self.timed_hybrid_search(queries, top_k)
        except Exception as e:
            logger.warning("Hybrid search failed, falling back to BlockStore: %s", e)
            try:
                candidates = (
                    self._block_store.search(query, top_k=top_k)
                    if self._block_store
                    else []
                )
            except Exception:
                candidates = []

        if not candidates:
            candidates = self.knowledge_fts_search(query, top_k)
        return candidates

    def timed_hybrid_search(self, queries: list[str], top_k: int) -> list[dict]:
        timeout = self._stage_timeout("hybrid_search")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.hybrid_search, queries, top_k)
            return future.result(timeout=timeout)

    def hybrid_search(self, queries: list[str], top_k: int) -> list[dict]:
        if self._hybrid_search_fn is not None:
            return self._hybrid_search_fn(queries, top_k)
        if self._hybrid_searcher is not None:
            return cast("list[dict]", self._hybrid_searcher.search(queries, top_k=top_k))
        searcher = HybridSearcher(self._db, self._block_store, self._config)
        return searcher.search(queries, top_k=top_k)

    def timed_rerank(
        self, query: str, candidates: list[dict], top_k: int,
    ) -> list[dict]:
        if not self._cfg("rag.enable_rerank", True):
            return candidates
        timeout = self._stage_timeout("rerank")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.rerank, query, candidates, top_k)
            return future.result(timeout=timeout)

    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        if self._reranker_fn is not None:
            return self._reranker_fn(query, candidates, top_k)
        enabled = self._cfg("rag.enable_rerank", True)
        if not enabled:
            return candidates
        try:
            reranker = LLMReranker(self._llm, self._config)
            return reranker.rerank(query, candidates, top_n=top_k)
        except Exception as e:
            logger.warning("Rerank failed: %s", e)
            return candidates

    def knowledge_fts_search(self, query: str, top_k: int) -> list[dict]:
        if self._knowledge_fts_fn is not None:
            return self._knowledge_fts_fn(query, top_k)
        if self._db is None:
            return []
        try:
            rows = self._db.search_knowledge(query, limit=top_k, offset=0)
        except Exception as e:
            logger.warning("Knowledge FTS fallback failed: %s", e)
            return []

        if rows is None:
            return []
        try:
            iter(rows)
        except TypeError:
            return []

        results = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            kid = row.get("id", "")
            results.append({
                "id": "",
                "text": row.get("content", ""),
                "metadata": {
                    "page_id": kid,
                    "knowledge_id": kid,
                    "title": row.get("title", ""),
                    "block_type": "knowledge",
                    "properties": {},
                },
                "score": row.get("fts_rank", 0),
            })
        return results

    def safe_wiki_search(self, query: str) -> list[dict]:
        if self._wiki_search_fn is not None:
            return self._wiki_search_fn(query)
        return self.wiki_search(query)

    def wiki_search(self, query: str) -> list[dict]:
        if self._db is None:
            return []
        try:
            wiki_results = self._db.search_wiki_fts(query, limit=3)
            if not wiki_results:
                return []
            try:
                iter(wiki_results)
            except TypeError:
                return []
            output = []
            for wr in wiki_results:
                if not isinstance(wr, dict):
                    continue
                summary = wr.get("concept_summary", "")
                content_preview = (wr.get("content", "") or "")[:300]
                output.append({
                    "source": "wiki",
                    "knowledge_id": wr.get("id", ""),
                    "title": wr["title"],
                    "summary": summary,
                    "text": f"[Wiki] {wr['title']}: {summary}\n{content_preview}",
                    "score": wr.get("fts_rank", 0),
                })
            return output
        except Exception as e:
            logger.warning("Wiki search failed: %s", e)
            return []

    def package_raw_candidates(
        self, query: str, candidates: list[dict], *, top_k: int,
    ) -> list[dict]:
        if self._package_raw_fn is not None:
            return self._package_raw_fn(query, candidates, top_k=top_k)
        output: list[dict] = []
        seen_blocks: set = set()
        knowledge_doc_counts: dict[str, int] = {}
        max_per_doc = 3
        citation_builder = None
        if self._citation_builder_factory is not None and self._db is not None:
            citation_builder = self._citation_builder_factory(self._db)
        elif self._db is not None:
            citation_builder = CitationBuilder(self._db)

        for r in candidates:
            bid = r.get("id", "")
            if bid and bid in seen_blocks:
                continue
            if bid:
                seen_blocks.add(bid)

            kid = (r.get("metadata") or {}).get(
                "page_id",
                (r.get("metadata") or {}).get("knowledge_id", ""),
            )

            if kid:
                doc_count = knowledge_doc_counts.get(kid, 0)
                if doc_count >= max_per_doc:
                    continue
                knowledge_doc_counts[kid] = doc_count + 1

            item = self._db.get_knowledge(kid) if kid and self._db is not None else None

            score = 0.0
            score_key = ""
            for key in ("rerank_score", "rrf_score", "vector_score", "distance"):
                val = r.get(key)
                if val is not None:
                    score = val
                    score_key = key
                    break

            title = "未知"
            if item and item.get("title"):
                title = item["title"]
            elif (r.get("metadata") or {}).get("title"):
                title = r["metadata"]["title"]
            elif kid and self._db is not None:
                try:
                    row = self._db.get_conn().execute(
                        "SELECT title FROM knowledge_items WHERE id = ? AND deleted_at IS NULL",
                        (kid,),
                    ).fetchone()
                    if row and row[0]:
                        title = row[0]
                except Exception:
                    pass

            title_boost = self._cfg("rag.title_boost", 0.15)
            if title_boost > 0 and title != "未知" and score_key != "distance":
                query_lower = query.lower()
                query_chars = set(query_lower) - {
                    " ", "的", "了", "是", "在", "和", "与", "或", "有", "中", "及",
                }
                title_lower = title.lower()
                overlap = sum(1 for c in query_chars if c in title_lower)
                if overlap > 0 and len(query_chars) > 0:
                    boost_ratio = min(overlap / len(query_chars), 1.0) * title_boost
                    score = min(score + boost_ratio, 1.0)
                    r.setdefault("match_channels", [])
                    if "title_boost" not in r["match_channels"]:
                        r["match_channels"].append("title_boost")

            entry = {
                "source": "knowledge",
                "block_id": bid,
                "knowledge_id": kid,
                "title": title,
                "text": r.get("text", ""),
                "score": score,
                "match_channels": r.get("match_channels", []),
                "warnings": r.get("warnings", []),
            }
            if citation_builder is not None:
                entry["citation"] = citation_builder.build(r, item).to_dict()
            output.append(entry)
            if len(output) >= top_k and not any(
                x.get("source") == "wiki" for x in output
            ):
                pass
        return output

    @staticmethod
    def minhash(text: str, num_perm: int = 64) -> list[int]:
        if not text:
            return [0] * num_perm
        if len(text) >= 2:
            tokens = [text[i:i + 2] for i in range(len(text) - 1)]
        else:
            tokens = [text]
        if not tokens:
            return [0] * num_perm
        signature = []
        for i in range(num_perm):
            min_hash = 2 ** 32
            for token in tokens:
                h = int(
                    hashlib.md5(
                        f"{i}:{token}".encode("utf-8", errors="replace"),
                    ).hexdigest()[:8],
                    16,
                )
                if h < min_hash:
                    min_hash = h
            signature.append(min_hash)
        return signature

    @classmethod
    def jaccard_similarity(cls, sig_a: list[int], sig_b: list[int]) -> float:
        if not sig_a or not sig_b or len(sig_a) != len(sig_b):
            return 0.0
        return sum(1 for a, b in zip(sig_a, sig_b) if a == b) / len(sig_a)

    @staticmethod
    def candidate_score(c: dict) -> float:
        for key in ("rerank_score", "rrf_score", "final_score", "score"):
            v = c.get(key)
            if v is not None:
                return float(v)
        return 0

    def diversity_filter(
        self, candidates: list[dict], threshold: float = 0.8,
    ) -> list[dict]:
        if self._diversity_fn is not None:
            return self._diversity_fn(candidates, threshold=threshold)
        if len(candidates) <= 1:
            return candidates

        signatures = []
        for c in candidates:
            text = c.get("text") or ""
            signatures.append(self.minhash(text[:500]))

        removed: set[int] = set()
        for i in range(len(candidates)):
            if i in removed:
                continue
            for j in range(i + 1, len(candidates)):
                if j in removed:
                    continue
                sim = self.jaccard_similarity(signatures[i], signatures[j])
                if sim > threshold:
                    score_i = self.candidate_score(candidates[i])
                    score_j = self.candidate_score(candidates[j])
                    if score_i >= score_j:
                        removed.add(j)
                    else:
                        removed.add(i)
                        break

        if removed:
            logger.debug(
                "Diversity filter: removed %d near-duplicate results (threshold=%s)",
                len(removed),
                threshold,
            )

        return [c for i, c in enumerate(candidates) if i not in removed]
