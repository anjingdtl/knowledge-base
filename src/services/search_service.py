"""统一搜索服务 — MCP 和 API 共用

Phase 3: Verified Hybrid 编排（Raw + Gate 通过的 Claim）在 search() 内融合。
编排源唯一为本服务；不重写 HybridSearcher / Raw 算法。
"""
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

from src.services.citation_builder import CitationBuilder
from src.services.hybrid_search import HybridSearcher
from src.services.query_rewriter import QueryRewriter
from src.services.reranker import LLMReranker

logger = logging.getLogger(__name__)

# 各阶段超时（秒），可通过 config 覆盖
_STAGE_TIMEOUTS = {
    "query_rewrite": 15,   # LLM 改写查询
    "hybrid_search": 25,   # 向量 + 关键词检索
    "rerank": 20,          # 重排序
    "wiki_search": 5,      # Wiki FTS5 搜索 / verified claim retrieval
}


class SearchService:
    """统一搜索服务 — 封装完整搜索管线

    管线流程：查询改写 → 混合检索 → 重排序 → Wiki 优先
    优化：查询改写与 Wiki 搜索并行执行；各阶段有独立超时保护。
    """

    def __init__(
        self,
        config=None,
        db=None,
        block_store=None,
        embedding=None,
        llm=None,
        wiki_repository=None,
        wiki_serving_gate=None,
    ):
        self._config = config or {}
        self._db = db
        self._block_store = block_store
        self._embedding = embedding
        self._llm = llm
        # Phase 2/3: unique Claim Serving entry + fusion
        self._wiki_repository = wiki_repository
        self._wiki_serving_gate = wiki_serving_gate
        self.last_search_trace: dict[str, Any] = {}

    def _stage_timeout(self, stage: str) -> float:
        """获取阶段超时时间，支持 config 覆盖"""
        cfg_key = f"rag.stage_timeout.{stage}"
        custom = self._cfg(cfg_key)
        return float(custom or _STAGE_TIMEOUTS.get(stage, 30))

    def _cfg(self, key: str, default=None):
        """统一配置读取：支持 Config 对象或嵌套 dict"""
        if isinstance(self._config, dict):
            parts = key.split(".")
            obj: object = self._config
            for p in parts:
                if isinstance(obj, dict):
                    obj = obj.get(p)
                else:
                    return default
            return obj if obj is not None else default
        return self._config.get(key, default)

    def list_servable_wiki_claims(
        self,
        *,
        include_disclose: bool = False,
        limit: int | None = None,
    ) -> list:
        """Search/Ask 唯一允许的 Wiki Claim 入口（Phase 2 Serving Gate）。"""
        repo = self._wiki_repository
        gate = self._wiki_serving_gate
        if repo is None:
            return []
        return repo.list_servable_claims(
            gate=gate,
            include_disclose=include_disclose,
            limit=limit,
        )

    def _should_use_verified_hybrid(self) -> bool:
        """Whether to run Phase 3 Verified Hybrid fusion path."""
        if not self._cfg("rag.verified_knowledge.enabled", False):
            return False
        if self._wiki_repository is None:
            return False
        try:
            from src.utils.knowledge_mode import allows_wiki_read, get_configured_knowledge_mode

            if not allows_wiki_read(get_configured_knowledge_mode()):
                return False
        except Exception:  # noqa: BLE001
            # Config missing mode → treat as verified-capable if flag set
            pass
        return True

    def _verified_cfg(self, key: str, default=None):
        return self._cfg(f"rag.verified_knowledge.{key}", default)

    def search(self, query: str, top_k: int = 5, query_spec=None) -> list[dict]:
        """完整搜索管线（带阶段超时保护）

        Phase 3: 当 ``rag.verified_knowledge.enabled`` 时，在本服务内融合
        Raw Retrieval 与 Gate 通过的 Claim；Wiki 异常永不阻断 Raw。
        """
        t0 = time.monotonic()
        self.last_search_trace = {
            "mode": "legacy",
            "query": (query or "")[:200],
            "stages": {},
        }

        if query_spec is not None:
            from src.services.db import Database
            from src.services.query_executor import QueryExecutor
            executor = QueryExecutor(db=self._db or Database)
            spec_results = executor.execute(query_spec)
            structured = []
            for row in spec_results[:top_k]:
                structured.append({
                    "source": "knowledge",
                    "block_id": None,
                    "knowledge_id": row["id"],
                    "title": row.get("title", ""),
                    "text": row.get("content", ""),
                    "score": 1.0,
                })
            if structured:
                self.last_search_trace["mode"] = "query_spec"
                return structured

        if self._should_use_verified_hybrid():
            output = self._search_verified_hybrid(query, top_k=top_k, t0=t0)
            elapsed = time.monotonic() - t0
            logger.info(
                "Verified hybrid search in %.2fs: %d results for query=%r",
                elapsed, len(output), query[:50],
            )
            self.last_search_trace["elapsed_ms"] = round(elapsed * 1000, 2)
            return output

        return self._search_legacy_pipeline(query, top_k=top_k, t0=t0)

    def _search_legacy_pipeline(self, query: str, top_k: int, t0: float) -> list[dict]:
        """原有管线：改写 + legacy wiki FTS + hybrid + rerank（evidence_only / 未开 fusion）。"""
        self.last_search_trace["mode"] = "legacy_raw"
        output: list[dict] = []

        # ── 阶段 1: 查询改写 + Wiki 搜索（并行） ──
        queries = [query]
        wiki_results: list[dict] = []

        with ThreadPoolExecutor(max_workers=2) as pool:
            rewrite_future = pool.submit(self._rewrite_query, query)
            wiki_future = pool.submit(self._safe_wiki_search, query)

            try:
                queries = rewrite_future.result(timeout=self._stage_timeout("query_rewrite"))
            except FuturesTimeout:
                logger.warning("Query rewrite timed out, using original query")
                queries = [query]
            except Exception as e:
                logger.warning("Query rewrite failed: %s", e)
                queries = [query]

            try:
                wiki_results = wiki_future.result(timeout=self._stage_timeout("wiki_search"))
            except FuturesTimeout:
                logger.warning("Wiki search timed out")
                wiki_results = []
            except Exception as e:
                logger.warning("Wiki search failed: %s", e)
                wiki_results = []

        self.last_search_trace["stages"]["query_rewrite"] = {"count": len(queries)}
        self.last_search_trace["stages"]["legacy_wiki_fts"] = {"count": len(wiki_results)}

        candidates = self._raw_retrieve(queries, query, top_k)
        self.last_search_trace["stages"]["raw_retrieval"] = {"count": len(candidates)}

        if candidates:
            try:
                candidates = self._timed_rerank(query, candidates, top_k)
            except FuturesTimeout:
                logger.warning("Rerank timed out, keeping original order")
            except Exception as e:
                logger.warning("Rerank failed: %s", e)

        if candidates:
            candidates = self._diversity_filter(candidates, threshold=0.8)

        output.extend(wiki_results)
        output.extend(self._package_raw_candidates(query, candidates, top_k=top_k))

        elapsed = time.monotonic() - t0
        logger.info("Search completed in %.2fs: %d results for query=%r", elapsed, len(output), query[:50])
        self.last_search_trace["elapsed_ms"] = round(elapsed * 1000, 2)
        self.last_search_trace["result_count"] = len(output)
        return output

    def _search_verified_hybrid(self, query: str, top_k: int, t0: float) -> list[dict]:
        """Phase 3: Router + parallel Raw/Claim + Gate + normalize + RRF fuse."""
        from src.services.verified_hybrid_fusion import (
            claims_to_candidates,
            fuse_verified_and_raw,
            normalize_raw_candidate,
            package_fused_result,
        )
        from src.services.verified_query_router import merge_route_with_config, route_query

        self.last_search_trace["mode"] = "hybrid_verified"
        route = route_query(query)
        route = merge_route_with_config(
            route,
            config_wiki_weight=float(self._verified_cfg("wiki_weight", 0.40)),
            config_raw_weight=float(self._verified_cfg("raw_weight", 0.60)),
        )
        self.last_search_trace["route"] = route.to_dict()

        raw_mult = int(self._verified_cfg("raw_candidate_multiplier", 3) or 3)
        wiki_mult = int(self._verified_cfg("wiki_candidate_multiplier", 2) or 2)
        raw_top = max(top_k * raw_mult, top_k)
        wiki_limit = max(top_k * wiki_mult, top_k)

        queries = [query]
        claim_pairs: list = []
        wiki_error: str | None = None
        raw_candidates: list[dict] = []
        self._last_claim_error: str | None = None

        with ThreadPoolExecutor(max_workers=3) as pool:
            rewrite_future = pool.submit(self._rewrite_query, query)
            claim_future = pool.submit(
                self._safe_verified_claim_retrieve, query, wiki_limit,
            )
            # Raw starts after rewrite when possible; also submit hybrid on original
            # immediately so rewrite timeout cannot block raw.
            hybrid_future = pool.submit(self._timed_hybrid_search, [query], raw_top)

            try:
                queries = rewrite_future.result(timeout=self._stage_timeout("query_rewrite"))
            except FuturesTimeout:
                logger.warning("Query rewrite timed out, using original query")
                queries = [query]
            except Exception as e:
                logger.warning("Query rewrite failed: %s", e)
                queries = [query]
            self.last_search_trace["stages"]["query_rewrite"] = {"count": len(queries)}

            try:
                claim_pairs = claim_future.result(timeout=self._stage_timeout("wiki_search"))
            except FuturesTimeout:
                wiki_error = "wiki_claim_timeout"
                logger.warning("Verified claim retrieval timed out — raw continues")
                claim_pairs = []
            except Exception as e:
                wiki_error = f"wiki_claim_error:{e}"
                logger.warning("Verified claim retrieval failed: %s — raw continues", e)
                claim_pairs = []
            if wiki_error is None and getattr(self, "_last_claim_error", None):
                wiki_error = f"wiki_claim_error:{self._last_claim_error}"

            try:
                raw_candidates = hybrid_future.result(timeout=self._stage_timeout("hybrid_search"))
            except FuturesTimeout:
                logger.warning("Hybrid search timed out in verified path")
                raw_candidates = []
            except Exception as e:
                logger.warning("Hybrid search failed in verified path: %s", e)
                raw_candidates = []

        # If rewrite produced extras and first hybrid was only original, optional second pass
        if len(queries) > 1 and raw_candidates:
            try:
                more = self._timed_hybrid_search(queries, raw_top)
                # merge by id, prefer higher rrf
                by_id = {str(c.get("id")): c for c in raw_candidates if c.get("id")}
                for c in more:
                    cid = str(c.get("id") or "")
                    if not cid:
                        continue
                    prev = by_id.get(cid)
                    if prev is None or float(c.get("rrf_score") or 0) > float(prev.get("rrf_score") or 0):
                        by_id[cid] = c
                raw_candidates = list(by_id.values())
            except Exception as e:  # noqa: BLE001
                logger.debug("Secondary hybrid with rewrites skipped: %s", e)

        if not raw_candidates:
            try:
                raw_candidates = self._block_store.search(query, top_k=raw_top) if self._block_store else []
            except Exception:
                raw_candidates = []
        if not raw_candidates:
            raw_candidates = self._knowledge_fts_search(query, raw_top)

        # Rerank raw channel only (do not require LLM for claims)
        if raw_candidates:
            try:
                raw_candidates = self._timed_rerank(query, raw_candidates, raw_top)
            except FuturesTimeout:
                logger.warning("Rerank timed out in verified path")
            except Exception as e:
                logger.warning("Rerank failed in verified path: %s", e)
            raw_candidates = self._diversity_filter(raw_candidates, threshold=0.8)

        claim_cands = claims_to_candidates(claim_pairs, query=query, limit=wiki_limit)
        raw_norm = [
            normalize_raw_candidate(r, rank=i) for i, r in enumerate(raw_candidates)
        ]

        self.last_search_trace["stages"]["verified_wiki"] = {
            "pairs": len(claim_pairs),
            "candidates": len(claim_cands),
            "error": wiki_error,
        }
        self.last_search_trace["stages"]["raw_retrieval"] = {"count": len(raw_norm)}

        empty_wiki_ok = bool(self._verified_cfg("empty_wiki_fallback_to_raw", True))
        if not claim_cands and empty_wiki_ok:
            self.last_search_trace["stages"]["fallback"] = "empty_wiki_to_raw"

        fused = fuse_verified_and_raw(
            claim_cands,
            raw_norm,
            wiki_weight=route.wiki_weight,
            raw_weight=route.raw_weight,
            top_n=top_k * 2,
        )
        self.last_search_trace["stages"]["fusion"] = {
            "count": len(fused),
            "wiki_weight": route.wiki_weight,
            "raw_weight": route.raw_weight,
        }

        citation_builder = CitationBuilder(self._db) if self._db is not None else None
        output: list[dict] = []
        seen = set()
        for cand in fused:
            key = (cand.get("candidate_type"), cand.get("candidate_id") or cand.get("id"))
            if key in seen:
                continue
            seen.add(key)
            # Primary list excludes disclose_only unless nothing else (Phase 4 expands)
            if cand.get("disclose_only") and cand.get("candidate_type") == "claim":
                self.last_search_trace.setdefault("disclose_claims", []).append(
                    cand.get("claim_id"),
                )
                continue
            packaged = package_fused_result(
                cand, db=self._db, citation_builder=citation_builder, query=query,
            )
            # Contract: claim must carry evidence
            if packaged.get("source") == "verified_claim":
                if not packaged.get("evidence"):
                    continue
            output.append(packaged)
            if len(output) >= top_k:
                break

        # Guarantee Raw fallback when fusion produced nothing but raw exists
        if not output and raw_norm:
            self.last_search_trace["stages"]["fallback"] = "fusion_empty_to_raw"
            for i, cand in enumerate(raw_norm):
                output.append(package_fused_result(
                    cand, db=self._db, citation_builder=citation_builder, query=query,
                ))
                if len(output) >= top_k:
                    break

        self.last_search_trace["result_count"] = len(output)
        self.last_search_trace["sources"] = {
            "verified_claim": sum(1 for r in output if r.get("source") == "verified_claim"),
            "knowledge": sum(1 for r in output if r.get("source") == "knowledge"),
        }
        return output

    def _safe_verified_claim_retrieve(self, query: str, limit: int) -> list:
        """Gate-filtered claim pairs; never raises to caller thread."""
        try:
            repo = self._wiki_repository
            gate = self._wiki_serving_gate
            if repo is None:
                return []
            claims = repo.list_claims()
            if gate is None:
                from src.services.wiki_serving_gate import WikiServingGate
                gate = WikiServingGate()
            pairs = gate.filter_servable(
                claims, include_disclose=False, limit=limit,
            )
            # Optional: light query filter by lexical score to shrink set
            from src.services.verified_hybrid_fusion import claim_retrieval_score

            scored = [
                (claim_retrieval_score(query, c), c, d) for c, d in pairs
            ]
            scored.sort(key=lambda x: x[0], reverse=True)
            return [(c, d) for s, c, d in scored if s > 0 or len(scored) <= limit][:limit]
        except Exception as e:  # noqa: BLE001
            self._last_claim_error = str(e)
            logger.warning("Verified claim retrieve internal error: %s", e)
            return []

    def _raw_retrieve(self, queries: list[str], query: str, top_k: int) -> list[dict]:
        """Existing raw retrieval path (hybrid → block_store → knowledge FTS)."""
        try:
            candidates = self._timed_hybrid_search(queries, top_k)
        except Exception as e:
            logger.warning("Hybrid search failed, falling back to BlockStore: %s", e)
            try:
                candidates = self._block_store.search(query, top_k=top_k) if self._block_store else []
            except Exception:
                candidates = []

        if not candidates:
            candidates = self._knowledge_fts_search(query, top_k)
        return candidates

    def _package_raw_candidates(
        self, query: str, candidates: list[dict], *, top_k: int,
    ) -> list[dict]:
        """Package hybrid hits into legacy search result items."""
        output: list[dict] = []
        seen_blocks: set = set()
        knowledge_doc_counts: dict[str, int] = {}
        max_per_doc = 3
        citation_builder = CitationBuilder(self._db) if self._db is not None else None
        for r in candidates:
            bid = r.get("id", "")
            if bid and bid in seen_blocks:
                continue
            if bid:
                seen_blocks.add(bid)

            kid = (r.get("metadata") or {}).get("page_id",
                  (r.get("metadata") or {}).get("knowledge_id", ""))

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
                query_chars = set(query_lower) - {' ', '的', '了', '是', '在', '和', '与', '或', '有', '中', '及'}
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
                # top_k applies to knowledge items after wiki prepend in legacy
                pass
        return output

    def _timed_hybrid_search(self, queries: list[str], top_k: int) -> list[dict]:
        """带超时保护的混合检索"""
        timeout = self._stage_timeout("hybrid_search")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._hybrid_search, queries, top_k)
            return future.result(timeout=timeout)

    def _timed_rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """带超时保护的重排序"""
        # BUG-2 fix: 默认启用rerank而非关闭，提升搜索排序相关性
        if not self._cfg("rag.enable_rerank", True):
            return candidates
        timeout = self._stage_timeout("rerank")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._rerank, query, candidates, top_k)
            return future.result(timeout=timeout)

    def _safe_wiki_search(self, query: str) -> list[dict]:
        """包装 wiki 搜索以便在 ThreadPoolExecutor 中安全调用"""
        return self._wiki_search(query)

    def _rewrite_query(self, query: str) -> list[str]:
        """查询改写，失败回退 [query]"""
        enabled = self._cfg("rag.enable_query_rewriting", False)
        if not enabled:
            return [query]
        try:
            rewriter = QueryRewriter(self._llm, self._config)
            return rewriter.rewrite(query)
        except Exception as e:
            logger.warning("Query rewrite failed: %s", e)
            return [query]

    def _hybrid_search(self, queries: list[str], top_k: int) -> list[dict]:
        """混合检索"""
        searcher = HybridSearcher(self._db, self._block_store, self._config)
        return searcher.search(queries, top_k=top_k)

    def _rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """重排序，失败保留原序"""
        enabled = self._cfg("rag.enable_rerank", True)
        if not enabled:
            return candidates
        try:
            reranker = LLMReranker(self._llm, self._config)
            return reranker.rerank(query, candidates, top_n=top_k)
        except Exception as e:
            logger.warning("Rerank failed: %s", e)
            return candidates

    def _knowledge_fts_search(self, query: str, top_k: int) -> list[dict]:
        """Fallback to item-level FTS when block/vector search yields nothing."""
        try:
            rows = self._db.search_knowledge(query, limit=top_k, offset=0)
        except Exception as e:
            logger.warning("Knowledge FTS fallback failed: %s", e)
            return []

        results = []
        for row in rows:
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

    def _wiki_search(self, query: str) -> list[dict]:
        """Wiki 搜索（FTS5 全文检索）"""
        try:
            wiki_results = self._db.search_wiki_fts(query, limit=3)
            output = []
            for wr in wiki_results:
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

    @staticmethod
    def _minhash(text: str, num_perm: int = 64) -> list[int]:
        """简易 minhash 签名：对文本按字符 bigram 做 hash 取最小值。

        不依赖外部库，用 hashlib.md5 模拟多个 hash 函数。
        """
        if not text:
            return [0] * num_perm
        # 字符 bigrams；文本过短（< 2 字符）无法形成 bigram 时，用整串作为唯一
        # token，避免不同的短文本（如标签块、单字标题块）退化成全 0 签名，
        # 被多样性过滤误判为 100% 相似而合并丢失。
        if len(text) >= 2:
            tokens = [text[i:i + 2] for i in range(len(text) - 1)]
        else:
            tokens = [text]
        if not tokens:
            return [0] * num_perm
        signature = []
        for i in range(num_perm):
            min_hash = 2 ** 32  # int 哨兵(大于任何 8 位 hex 哈希 0xFFFFFFFF),保 signature 为 list[int]
            for token in tokens:
                h = int(hashlib.md5(f"{i}:{token}".encode("utf-8", errors="replace")).hexdigest()[:8], 16)
                if h < min_hash:
                    min_hash = h
            signature.append(min_hash)
        return signature

    @classmethod
    def _jaccard_similarity(cls, sig_a: list[int], sig_b: list[int]) -> float:
        """基于 minhash 签名的 Jaccard 相似度估计"""
        if not sig_a or not sig_b or len(sig_a) != len(sig_b):
            return 0.0
        return sum(1 for a, b in zip(sig_a, sig_b) if a == b) / len(sig_a)

    @staticmethod
    def _candidate_score(c: dict) -> float:
        """取候选的有效分数，回退链与最终打分一致：
        rerank_score > rrf_score > final_score > score > 0。
        用显式 None 检查——0.0 是有效分数，不能被当作"无分"跳过。
        """
        for key in ("rerank_score", "rrf_score", "final_score", "score"):
            v = c.get(key)
            if v is not None:
                return float(v)
        return 0

    def _diversity_filter(self, candidates: list[dict], threshold: float = 0.8) -> list[dict]:
        """Phase 2: 多样性过滤 — 内容极度相似的 block 合并保留最高分。

        当两个结果的内容 minhash Jaccard > threshold 时，只保留分数更高的那个。
        这样可以消除同一文档中因分块重叠导致的高相似重复结果。
        """
        if len(candidates) <= 1:
            return candidates

        # 预计算 minhash 签名
        signatures = []
        for c in candidates:
            # c['text'] 显式为 None（block content 为 NULL）时 .get('text','') 仍返回
            # None，下标 text[:500] 会抛 TypeError；用 `or ""` 把 None 一并兜底。
            text = c.get("text") or ""
            # 截取前 500 字做签名，避免长文本开销过大
            signatures.append(self._minhash(text[:500]))

        # 逐对比较，标记要移除的
        removed = set()
        for i in range(len(candidates)):
            if i in removed:
                continue
            for j in range(i + 1, len(candidates)):
                if j in removed:
                    continue
                sim = self._jaccard_similarity(signatures[i], signatures[j])
                if sim > threshold:
                    # 保留分数更高的。回退链须与 search() 最终打分一致
                    # （rerank_score > rrf_score > final_score > score > 0），
                    # 否则 rerank 之后仍按旧 rrf_score 决策，会误杀 reranker
                    # 钦定的高分结果；FTS fallback 候选也能经 "score" 命中。
                    score_i = self._candidate_score(candidates[i])
                    score_j = self._candidate_score(candidates[j])
                    if score_i >= score_j:
                        removed.add(j)
                    else:
                        removed.add(i)
                        break  # i 被移除了，不需要再比

        if removed:
            logger.debug(f"Diversity filter: removed {len(removed)} near-duplicate results (threshold={threshold})")

        return [c for i, c in enumerate(candidates) if i not in removed]
