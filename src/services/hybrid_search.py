"""混合检索模块 — Block-First 架构（embedding/keywords/blend）+ 加分融合"""
import hashlib
import logging

from src.utils.config import Config
from src.services.block_store import BlockStore
from src.services.db import Database


class HybridSearcher:
    def search(self, queries: list[str], top_k: int = 5) -> list[dict]:
        mode = Config.get("rag.search_mode", "blend")
        if mode == "embedding":
            return self._vector_search(queries, top_k)
        elif mode == "keywords":
            return self._keyword_search(queries, top_k)
        else:
            return self._blend_search(queries, top_k)

    def _vector_search(self, queries: list[str], top_k: int) -> list[dict]:
        results = []
        seen = set()
        for query in queries:
            try:
                vec_results = BlockStore().search(query, top_k=top_k * 2)
                for r in vec_results:
                    cid = r["id"]
                    if cid not in seen:
                        seen.add(cid)
                        results.append({
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
                fts_results = Database.search_blocks_fts(query, limit=top_k * 2)
                for r in fts_results:
                    cid = r["id"]
                    if cid not in seen:
                        seen.add(cid)
                        results.append({
                            "text": r.get("content", ""),
                            "metadata": {
                                "page_id": r.get("page_id", ""),
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
        w_v = Config.get("rag.hybrid_search.vector_weight", 0.7)
        w_k = Config.get("rag.hybrid_search.keyword_weight", 0.3)

        vec_results = self._vector_search(queries, top_k * 3)
        fts_results = self._keyword_search(queries, top_k * 3)

        merged = {}
        for r in vec_results:
            cid = self._candidate_id(r)
            merged[cid] = {
                "text": r["text"],
                "metadata": r.get("metadata", {}),
                "distance": r.get("distance", 0),
                "vec_score": w_v * max(0, 1 - r.get("distance", 0) / 2),
                "fts_score": 0,
            }
        for r in fts_results:
            cid = self._candidate_id(r)
            raw_rank = r.get("fts_rank", 0)
            normalized = self._normalize_fts_rank(raw_rank)
            if cid in merged:
                merged[cid]["fts_score"] = w_k * min(normalized, 1.0)
                merged[cid]["fts_rank"] = raw_rank
            else:
                merged[cid] = {
                    "text": r["text"],
                    "metadata": r.get("metadata", {}),
                    "distance": r.get("distance", 0),
                    "vec_score": 0,
                    "fts_score": w_k * min(normalized, 1.0),
                    "fts_rank": raw_rank,
                }

        for item in merged.values():
            item["rrf_score"] = item["vec_score"] + item["fts_score"]

        sorted_items = sorted(merged.values(), key=lambda x: x["rrf_score"], reverse=True)
        return self._preserve_keyword_hits(sorted_items, top_k)

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

    def _preserve_keyword_hits(self, sorted_items: list[dict], top_k: int) -> list[dict]:
        limit = top_k * 2
        selected = list(sorted_items[:limit])
        keyword_items = [
            item for item in sorted_items
            if item.get("fts_score", 0) > 0
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
                    if selected[i].get("fts_score", 0) <= 0:
                        replace_idx = i
                        break
                if replace_idx is None:
                    replace_idx = len(selected) - 1
                selected_ids.discard(self._candidate_id(selected[replace_idx]))
                selected[replace_idx] = keyword_item
            selected_ids.add(keyword_id)

        selected.sort(key=lambda x: x["rrf_score"], reverse=True)
        return selected[:limit]
