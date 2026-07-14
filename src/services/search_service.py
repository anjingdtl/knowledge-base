"""统一搜索服务 — MCP 和 API 共用

Phase 3: Verified Hybrid 编排（Raw + Gate 通过的 Claim）在 search/execute 内融合。

Phase-1 maintainability: 一次请求的 results/trace/claims/conflicts/fallbacks
收敛到 SearchExecution；禁止在实例上保存 last_* 请求状态。

Phase-2 / closure WP1–WP5: execute() 经 RetrievalOrchestrator unified；
RawRetriever / VerifiedFusion 为算法权威；本类退化为稳定 Facade。
"""
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import cast

from src.models.search_execution import SearchExecution
from src.retrieval.packaging import SearchRequestState, to_execution
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

# Back-compat alias for tests/imports that used the private name
_SearchRequestState = SearchRequestState


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
        # Phase-2 orchestrator (no request state; lazy cache only)
        self._orchestrator = None
        # WP1: algorithm authorities
        self._raw_retriever = None
        self._verified_fusion = None

    def _get_orchestrator(self):
        if self._orchestrator is None:
            from src.retrieval.orchestrator import RetrievalOrchestrator

            self._orchestrator = RetrievalOrchestrator(self, self._config)
        return self._orchestrator

    def _get_raw_retriever(self):
        """Lazy RawRetriever with explicit deps (not a whole search-service object).

        Inject callables via lambdas so unit tests that patch SearchService
        private helpers still hit the same algorithm path.
        """
        if self._raw_retriever is None:
            from src.retrieval.raw_retriever import RawRetriever

            self._raw_retriever = RawRetriever(
                config=self._config,
                db=self._db,
                block_store=self._block_store,
                llm=self._llm,
                stage_timeout_fn=lambda stage: self._stage_timeout(stage),
                query_rewriter=lambda q: self._rewrite_query(q),
                hybrid_search_fn=lambda queries, top_k: self._hybrid_search(
                    queries, top_k,
                ),
                reranker=lambda q, cands, top_k: self._rerank(q, cands, top_k),
                knowledge_fts_fn=lambda q, top_k: self._knowledge_fts_search(
                    q, top_k,
                ),
                wiki_search_fn=lambda q: self._safe_wiki_search(q),
                package_raw_fn=lambda q, cands, top_k=5: self._package_raw_candidates(
                    q, cands, top_k=top_k,
                ),
                diversity_fn=lambda cands, threshold=0.8: self._diversity_filter(
                    cands, threshold=threshold,
                ),
            )
        return self._raw_retriever

    def _get_verified_fusion(self):
        """Lazy VerifiedFusion — verified hybrid algorithm authority."""
        if self._verified_fusion is None:
            from src.retrieval.fusion import VerifiedFusion

            self._verified_fusion = VerifiedFusion(
                config=self._config,
                db=self._db,
                block_store=self._block_store,
                stage_timeout_fn=lambda stage: self._stage_timeout(stage),
                verified_cfg_fn=lambda key, default=None: self._verified_cfg(
                    key, default,
                ),
                rewrite_fn=lambda q: self._rewrite_query(q),
                timed_hybrid_fn=lambda qs, k: self._timed_hybrid_search(qs, k),
                timed_rerank_fn=lambda q, c, k: self._timed_rerank(q, c, k),
                diversity_fn=lambda c, threshold=0.8: self._diversity_filter(
                    c, threshold=threshold,
                ),
                knowledge_fts_fn=lambda q, k: self._knowledge_fts_search(q, k),
                claim_retrieve_fn=lambda q, limit, state: (
                    self._safe_verified_claim_retrieve(q, limit, state)
                ),
            )
        return self._verified_fusion

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
        return cast(
            list,
            repo.list_servable_claims(
                gate=gate,
                include_disclose=include_disclose,
                limit=limit,
            ),
        )

    def _should_use_verified_hybrid(self) -> bool:
        """Whether to run Phase 3 Verified Hybrid fusion path."""
        if self._wiki_repository is None:
            return False
        try:
            from src.utils.knowledge_settings import resolve_effective_knowledge_settings

            settings = resolve_effective_knowledge_settings(self._config)
            return settings.verified_hybrid_enabled and settings.wiki_read_enabled
        except Exception:  # noqa: BLE001
            # A malformed config must not make the Verified path reachable.
            return False

    def _verified_cfg(self, key: str, default=None):
        return self._cfg(f"rag.verified_knowledge.{key}", default)

    def execute(
        self,
        query: str,
        top_k: int = 5,
        query_spec=None,
    ) -> SearchExecution:
        """完整搜索管线，返回请求级 SearchExecution（results + trace + side-channels）。

        经 RetrievalOrchestrator 统一路径（v1.10.0：仅 unified）。
        """
        return cast(SearchExecution, self._get_orchestrator().search(
            query, top_k=top_k, query_spec=query_spec,
        ))

    def execute_query_spec(self, query_spec, *, top_k: int = 5) -> SearchExecution:
        """Structured query_spec path.

        返回空 results 时，调用方应回落普通检索。
        """
        state = SearchRequestState(
            trace={
                "mode": "query_spec",
                "query": "",
                "stages": {},
            },
        )
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
            state.trace["result_count"] = len(structured)
            return to_execution(structured, state)
        state.trace["result_count"] = 0
        return to_execution([], state)

    def execute_evidence_only(
        self,
        query: str,
        *,
        top_k: int = 5,
        query_spec=None,
    ) -> SearchExecution:
        """Compatibility entry — EvidenceOnlyPolicy composition."""
        from src.retrieval.policies.evidence_only import EvidenceOnlyPolicy

        return EvidenceOnlyPolicy(self).execute(
            query, top_k=top_k, query_spec=query_spec,
        )

    def execute_verified(
        self,
        query: str,
        *,
        top_k: int = 5,
        query_spec=None,
    ) -> SearchExecution:
        """Compatibility entry — VerifiedPolicy composition."""
        from src.retrieval.policies.verified import VerifiedPolicy

        return VerifiedPolicy(self).execute(
            query, top_k=top_k, query_spec=query_spec,
        )

    def search(self, query: str, top_k: int = 5, query_spec=None) -> list[dict]:
        """兼容入口：返回 results 列表（内部委托 execute）。"""
        return list(
            self.execute(
                query=query,
                top_k=top_k,
                query_spec=query_spec,
            ).results,
        )

    @staticmethod
    def _to_execution(output: list[dict], state: SearchRequestState) -> SearchExecution:
        return to_execution(output, state)

    def _safe_verified_claim_retrieve(
        self,
        query: str,
        limit: int,
        state: SearchRequestState | None = None,
    ) -> list:
        """Gate-filtered claim pairs via VerifiedProvider; never raises to caller thread."""
        from src.retrieval.verified_provider import VerifiedProvider

        provider = VerifiedProvider(
            wiki_repository=self._wiki_repository,
            wiki_serving_gate=self._wiki_serving_gate,
            config=self._config,
        )
        result = provider.serve(query, limit=limit)
        if state is not None and result.fallback_reason:
            state.claim_error = result.fallback_reason
        return list(result.claim_pairs)

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
