"""混合检索模块 — Block-First 架构（embedding/keywords/blend）+ RRF 融合"""
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor

from src.utils.config import Config
from src.services.block_store import BlockStore
from src.services.db import Database
from src.services.block_context import enrich_result_with_context


class HybridSearcher:
    def __init__(self, db=None, block_store=None, config=None):
        self._db = db or Database
        self._block_store = block_store or BlockStore()
        self._config = config or Config

    def _get_config(self, key: str, default=None):
        return self._config.get(key, default)

    def search(self, queries: list[str], top_k: int = 5) -> list[dict]:
        mode = self._get_config("rag.search_mode", "blend")
        if mode == "embedding":
            results = self._vector_search(queries, top_k)
        elif mode == "keywords":
            results = self._keyword_search(queries, top_k)
        else:
            results = self._blend_search(queries, top_k)

        # 为每个结果回溯父链上下文
        for r in results:
            enrich_result_with_context(r)

        return results

    def _vector_search(self, queries: list[str], top_k: int) -> list[dict]:
        results = []
        seen = set()
        for query in queries:
            try:
                vec_results = self._block_store.search(query, top_k=top_k * 2)
                for r in vec_results:
                    cid = r["id"]
                    if cid not in seen:
                        seen.add(cid)
                        results.append({
                            "id": cid,
                            "text": r["text"],
                            "metadata": r.get("metadata", {}),
                            "distance": r.get("distance", 0),
                        })
            except Exception as e:
                logging.warning(f"Vector search failed: {e}")
        results.sort(key=lambda x: (1 - x["distance"] / 2, -len(x.get("text", ""))), reverse=True)
        return results[:top_k * 2]

    def _keyword_search(self, queries: list[str], top_k: int) -> list[dict]:
        results = []
        seen = set()
        for query in queries:
            try:
                fts_results = self._db.search_blocks_fts(query, limit=top_k * 2)
                for r in fts_results:
                    cid = r["id"]
                    if cid not in seen:
                        seen.add(cid)
                        results.append({
                            "id": cid,
                            "text": r.get("content", ""),
                            "metadata": {
                                "page_id": r.get("page_id", ""),
                                "block_id": cid,
                                "block_type": r.get("block_type", ""),
                                "properties": r.get("properties", {}),
                            },
                            "distance": 0,
                            "fts_rank": r.get("fts_rank", 0),
                        })
            except Exception as e:
                logging.warning(f"Keyword search failed: {e}")
        return results[:top_k * 2]

    def _blend_search(self, queries: list[str], top_k: int) -> list[dict]:
        # 向量搜索和关键词搜索并行执行，减少总耗时
        with ThreadPoolExecutor(max_workers=2) as pool:
            vec_future = pool.submit(self._vector_search, queries, top_k * 3)
            fts_future = pool.submit(self._keyword_search, queries, top_k * 3)
            vec_results = vec_future.result()
            fts_results = fts_future.result()

        k = 60
        rrf_scores = {}
        result_map = {}

        for rank, item in enumerate(vec_results):
            item_id = self._candidate_id(item)
            rrf_scores[item_id] = rrf_scores.get(item_id, 0) + 1.0 / (k + rank + 1)
            if item_id not in result_map:
                result_map[item_id] = {
                    "id": item.get("id", item_id),
                    "text": item["text"],
                    "metadata": self._metadata_with_block_id(item, item.get("id", item_id)),
                    "distance": item.get("distance", 0),
                }

        for rank, item in enumerate(fts_results):
            item_id = self._candidate_id(item)
            rrf_scores[item_id] = rrf_scores.get(item_id, 0) + 1.0 / (k + rank + 1)
            if item_id not in result_map:
                result_map[item_id] = {
                    "id": item.get("id", item_id),
                    "text": item["text"],
                    "metadata": self._metadata_with_block_id(item, item.get("id", item_id)),
                    "distance": item.get("distance", 0),
                    "fts_rank": item.get("fts_rank", 0),
                }
            else:
                result_map[item_id].setdefault("fts_rank", item.get("fts_rank", 0))

        sorted_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)
        results = []
        for item_id in sorted_ids[:top_k * 2]:
            item = result_map[item_id]
            item["rrf_score"] = rrf_scores[item_id]
            results.append(item)

        return self._preserve_keyword_hits(results, top_k)

    @staticmethod
    def _normalize_fts_rank(raw_rank: float) -> float:
        try:
            rank = float(raw_rank)
        except (TypeError, ValueError):
            return 0
        if rank < 0:
            strength = abs(rank)
            return strength / (strength + 10)
        return min(rank / 10, 1.0)

    @staticmethod
    def _candidate_id(item: dict) -> str:
        page_id = item.get("metadata", {}).get("page_id", "")
        block_id = item.get("id", "")
        if page_id and block_id:
            return page_id + ":" + block_id
        kid = item.get("metadata", {}).get("knowledge_id", "")
        cidx = str(item.get("metadata", {}).get("chunk_index", 0))
        if kid:
            return kid + ":" + cidx
        text = item.get("text", "")
        text_hash = hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()[:12]
        return "text_" + text_hash

    @staticmethod
    def _metadata_with_block_id(item: dict, block_id: str) -> dict:
        metadata = dict(item.get("metadata", {}) or {})
        if block_id and not block_id.startswith("text_"):
            metadata.setdefault("block_id", block_id)
        return metadata

    def _preserve_keyword_hits(self, sorted_items: list[dict], top_k: int) -> list[dict]:
        limit = top_k * 2
        selected = list(sorted_items[:limit])
        keyword_items = [
            item for item in sorted_items
            if item.get("fts_rank", 0) != 0
        ]
        keep_count = min(3, top_k, len(keyword_items))
        if keep_count == 0:
            return selected

        selected_ids = {self._candidate_id(item) for item in selected}
        for keyword_item in keyword_items[:keep_count]:
            keyword_id = self._candidate_id(keyword_item)
            if keyword_id in selected_ids:
                continue
            if len(selected) < limit:
                selected.append(keyword_item)
            else:
                replace_idx = None
                for i in range(len(selected) - 1, -1, -1):
                    if selected[i].get("fts_rank", 0) == 0:
                        replace_idx = i
                        break
                if replace_idx is None:
                    replace_idx = len(selected) - 1
                selected_ids.discard(self._candidate_id(selected[replace_idx]))
                selected[replace_idx] = keyword_item
            selected_ids.add(keyword_id)

        selected.sort(key=lambda x: x["rrf_score"], reverse=True)
        return selected[:limit]
