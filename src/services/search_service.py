"""统一搜索服务 — MCP 和 API 共用"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

from src.services.query_rewriter import QueryRewriter
from src.services.hybrid_search import HybridSearcher
from src.services.reranker import LLMReranker
from src.services.citation_builder import CitationBuilder

logger = logging.getLogger(__name__)

# 各阶段超时（秒），可通过 config 覆盖
_STAGE_TIMEOUTS = {
    "query_rewrite": 15,   # LLM 改写查询
    "hybrid_search": 25,   # 向量 + 关键词检索
    "rerank": 20,          # 重排序
    "wiki_search": 5,      # Wiki FTS5 搜索
}


class SearchService:
    """统一搜索服务 — 封装完整搜索管线

    管线流程：查询改写 → 混合检索 → 重排序 → Wiki 优先
    优化：查询改写与 Wiki 搜索并行执行；各阶段有独立超时保护。
    """

    def __init__(self, config=None, db=None, block_store=None, embedding=None, llm=None):
        self._config = config or {}
        self._db = db
        self._block_store = block_store
        self._embedding = embedding
        self._llm = llm

    def _stage_timeout(self, stage: str) -> float:
        """获取阶段超时时间，支持 config 覆盖"""
        cfg_key = f"rag.stage_timeout.{stage}"
        custom = self._cfg(cfg_key)
        return float(custom or _STAGE_TIMEOUTS.get(stage, 30))

    def _cfg(self, key: str, default=None):
        """统一配置读取：支持 Config 对象或嵌套 dict"""
        if isinstance(self._config, dict):
            parts = key.split(".")
            obj = self._config
            for p in parts:
                if isinstance(obj, dict):
                    obj = obj.get(p)
                else:
                    return default
            return obj if obj is not None else default
        return self._config.get(key, default)

    def search(self, query: str, top_k: int = 5, query_spec=None) -> list[dict]:
        """完整搜索管线（带阶段超时保护）"""
        t0 = time.monotonic()

        if query_spec is not None:
            from src.services.query_executor import QueryExecutor
            from src.services.db import Database
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
                return structured

        output = []

        # ── 阶段 1: 查询改写 + Wiki 搜索（并行） ──
        queries = [query]  # 默认：不改写
        wiki_results = []

        with ThreadPoolExecutor(max_workers=2) as pool:
            # 提交查询改写
            rewrite_future = pool.submit(self._rewrite_query, query)
            # 提交 Wiki 搜索
            wiki_future = pool.submit(self._safe_wiki_search, query)

            # 收集查询改写结果
            try:
                queries = rewrite_future.result(timeout=self._stage_timeout("query_rewrite"))
            except FuturesTimeout:
                logger.warning("Query rewrite timed out, using original query")
                queries = [query]
            except Exception as e:
                logger.warning("Query rewrite failed: %s", e)
                queries = [query]

            # 收集 Wiki 搜索结果
            try:
                wiki_results = wiki_future.result(timeout=self._stage_timeout("wiki_search"))
            except FuturesTimeout:
                logger.warning("Wiki search timed out")
                wiki_results = []
            except Exception as e:
                logger.warning("Wiki search failed: %s", e)
                wiki_results = []

        # ── 阶段 2: 混合检索（带超时保护） ──
        try:
            candidates = self._timed_hybrid_search(queries, top_k)
        except Exception as e:
            logger.warning("Hybrid search failed, falling back to BlockStore: %s", e)
            try:
                candidates = self._block_store.search(query, top_k=top_k)
            except Exception:
                candidates = []

        if not candidates:
            candidates = self._knowledge_fts_search(query, top_k)

        # ── 阶段 3: 重排序（带超时保护） ──
        if candidates:
            try:
                candidates = self._timed_rerank(query, candidates, top_k)
            except FuturesTimeout:
                logger.warning("Rerank timed out, keeping original order")
            except Exception as e:
                logger.warning("Rerank failed: %s", e)

        # ── 阶段 4: 组装结果 ──
        output.extend(wiki_results)

        seen_blocks = set()
        citation_builder = CitationBuilder(self._db)
        for r in candidates:
            bid = r.get("id", "")
            if bid and bid in seen_blocks:
                continue
            if bid:
                seen_blocks.add(bid)

            kid = (r.get("metadata") or {}).get("page_id",
                  (r.get("metadata") or {}).get("knowledge_id", ""))

            item = self._db.get_knowledge(kid) if kid else None

            # 分数回退链: rerank_score > rrf_score > vector_score > distance
            # 使用显式 None 检查，0.0 是有效分数
            score = 0.0
            for key in ("rerank_score", "rrf_score", "vector_score", "distance"):
                val = r.get(key)
                if val is not None:
                    score = val
                    break

            # 构建 citation
            citation = citation_builder.build(r, item)

            output.append({
                "source": "knowledge",
                "block_id": bid,
                "knowledge_id": kid,
                "title": item["title"] if item else "未知",
                "text": r.get("text", ""),
                "score": score,
                "match_channels": r.get("match_channels", []),
                "warnings": r.get("warnings", []),
                "citation": citation.to_dict(),
            })

        elapsed = time.monotonic() - t0
        logger.info("Search completed in %.2fs: %d results for query=%r", elapsed, len(output), query[:50])
        return output

    def _timed_hybrid_search(self, queries: list[str], top_k: int) -> list[dict]:
        """带超时保护的混合检索"""
        timeout = self._stage_timeout("hybrid_search")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._hybrid_search, queries, top_k)
            return future.result(timeout=timeout)

    def _timed_rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """带超时保护的重排序"""
        if not self._cfg("rag.enable_rerank", False):
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
        enabled = self._cfg("rag.enable_rerank", False)
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
