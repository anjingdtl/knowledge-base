"""统一搜索服务 — MCP 和 API 共用"""
import logging
from src.services.query_rewriter import QueryRewriter
from src.services.hybrid_search import HybridSearcher
from src.services.reranker import LLMReranker

logger = logging.getLogger(__name__)


class SearchService:
    """统一搜索服务 — 封装完整搜索管线

    管线流程：查询改写 → 混合检索 → 重排序 → Wiki 优先
    """

    def __init__(self, config, db, block_store, embedding, llm):
        self._config = config
        self._db = db
        self._block_store = block_store
        self._embedding = embedding
        self._llm = llm

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """完整搜索管线"""
        output = []

        # 1. 查询改写
        queries = self._rewrite_query(query)

        # 2. 混合检索（HybridSearcher: 向量 + 关键词 blend + RRF 融合）
        try:
            candidates = self._hybrid_search(queries, top_k)
        except Exception as e:
            logger.warning("Hybrid search failed, falling back to BlockStore: %s", e)
            candidates = self._block_store.search(query, top_k=top_k)

        # 3. 重排序（专用 reranker 模型或 LLM 打分）
        if candidates:
            candidates = self._rerank(query, candidates, top_k)

        # 4. Wiki 结构化知识优先
        wiki_results = self._wiki_search(query)
        output.extend(wiki_results)

        # 5. 组装检索+重排结果
        seen_kids = {w.get("knowledge_id") for w in wiki_results}
        for r in candidates:
            kid = (r.get("metadata") or {}).get("page_id",
                  (r.get("metadata") or {}).get("knowledge_id", ""))
            if kid and kid not in seen_kids:
                seen_kids.add(kid)
                item = self._db.get_knowledge(kid) if kid else None
                score = r.get("rerank_score", r.get("rrf_score",
                        r.get("score", r.get("distance", 0))))
                output.append({
                    "source": "knowledge",
                    "block_id": r.get("id", ""),
                    "knowledge_id": kid,
                    "title": item["title"] if item else "未知",
                    "text": r.get("text", ""),
                    "score": score,
                })

        return output

    def _rewrite_query(self, query: str) -> list[str]:
        """查询改写，失败回退 [query]"""
        if not self._config.get("rag.enable_query_rewriting", False):
            return [query]
        try:
            rewriter = QueryRewriter()
            return rewriter.rewrite(query)
        except Exception as e:
            logger.warning("Query rewrite failed: %s", e)
            return [query]

    def _hybrid_search(self, queries: list[str], top_k: int) -> list[dict]:
        """混合检索"""
        searcher = HybridSearcher()
        return searcher.search(queries, top_k=top_k)

    def _rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """重排序，失败保留原序"""
        if not self._config.get("rag.enable_rerank", False):
            return candidates
        try:
            reranker = LLMReranker()
            return reranker.rerank(query, candidates, top_n=top_k)
        except Exception as e:
            logger.warning("Rerank failed: %s", e)
            return candidates

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
