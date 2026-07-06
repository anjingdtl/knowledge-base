"""统一搜索服务 — MCP 和 API 共用"""
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

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
            obj: object = self._config
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

        # ── 阶段 3.5: 多样性过滤（minhash 去重） ──
        if candidates:
            candidates = self._diversity_filter(candidates, threshold=0.8)

        # ── 阶段 4: 组装结果 ──
        output.extend(wiki_results)

        seen_blocks = set()
        knowledge_doc_counts: dict[str, int] = {}
        max_per_doc = 3  # BUG-5 fix: 同一文档最多返回 3 个 block，避免重复块挤占多样性
        citation_builder = CitationBuilder(self._db)
        for r in candidates:
            bid = r.get("id", "")
            if bid and bid in seen_blocks:
                continue
            if bid:
                seen_blocks.add(bid)

            kid = (r.get("metadata") or {}).get("page_id",
                  (r.get("metadata") or {}).get("knowledge_id", ""))

            # BUG-5: knowledge 级去重 — 同一文档不超过 max_per_doc 条
            if kid:
                doc_count = knowledge_doc_counts.get(kid, 0)
                if doc_count >= max_per_doc:
                    continue
                knowledge_doc_counts[kid] = doc_count + 1

            item = self._db.get_knowledge(kid) if kid else None

            # 分数回退链: rerank_score > rrf_score > vector_score > distance
            # 使用显式 None 检查，0.0 是有效分数
            score = 0.0
            score_key = ""
            for key in ("rerank_score", "rrf_score", "vector_score", "distance"):
                val = r.get(key)
                if val is not None:
                    score = val
                    score_key = key
                    break

            # BUG-8 fix: 更健壮的 title 回退
            title = "未知"
            if item and item.get("title"):
                title = item["title"]
            elif (r.get("metadata") or {}).get("title"):
                title = r["metadata"]["title"]
            elif kid:
                # 尝试从 blocks 表的 page_id 关联 knowledge_items 获取标题
                try:
                    row = self._db.get_conn().execute(
                        "SELECT title FROM knowledge_items WHERE id = ? AND deleted_at IS NULL",
                        (kid,),
                    ).fetchone()
                    if row and row[0]:
                        title = row[0]
                except Exception:
                    pass
                if title == "未知":
                    logger.debug("Title fallback to '未知' for knowledge_id=%s", kid)

            # BUG-2 fix: title boost — 标题包含查询关键词的结果获得分数加成
            # 注意:score 为 distance(cosine 距离,越小越好,∈[0,2])时跳过加成——
            # 直接 ``score + boost_ratio`` 会让低距离(高分)结果变「差」,语义反转。
            # distance 仅在前三者(rerank/rrf/vector_score)全 None 时胜出,低频。
            title_boost = self._cfg("rag.title_boost", 0.15)
            if title_boost > 0 and title != "未知" and score_key != "distance":
                query_lower = query.lower()
                # 检查查询中的核心词是否出现在标题中
                query_chars = set(query_lower) - {' ', '的', '了', '是', '在', '和', '与', '或', '有', '中', '及'}
                title_lower = title.lower()
                overlap = sum(1 for c in query_chars if c in title_lower)
                if overlap > 0 and len(query_chars) > 0:
                    boost_ratio = min(overlap / len(query_chars), 1.0) * title_boost
                    score = min(score + boost_ratio, 1.0)
                    r.setdefault("match_channels", [])
                    if "title_boost" not in r["match_channels"]:
                        r["match_channels"].append("title_boost")

            # 构建 citation
            citation = citation_builder.build(r, item)

            output.append({
                "source": "knowledge",
                "block_id": bid,
                "knowledge_id": kid,
                "title": title,
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
