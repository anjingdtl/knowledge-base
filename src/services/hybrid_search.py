"""混合检索模块 — Passage-first 语义检索（SPEC v3）+ 加权 RRF 融合。

图谱 blocks 仅作结构/溯源；当 retrieval_passages 可用时，向量/FTS/融合
统一以 passage 为候选单元。passages 为空时回退 block 路径保持兼容。
"""
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor

from src.models.retrieval import normalize_fts_score, normalize_vector_score
from src.services.block_context import enrich_result_with_context
from src.services.block_store import BlockStore
from src.services.db import Database
from src.utils.chinese_tokenizer import detect_proper_nouns, detect_query_language
from src.utils.config import Config


class HybridSearcher:
    def __init__(self, db=None, block_store=None, config=None, passage_store=None):
        self._db = db or Database
        self._block_store = block_store or BlockStore()
        self._config = config or Config
        self._passage_store = passage_store  # lazy optional
        self._lexical = None  # W3: lazy LexicalZh
        self._passage_mode: bool | None = None

    def _get_config(self, key: str, default=None):
        return self._config.get(key, default)

    def _get_lexical(self):
        if self._lexical is None:
            from src.services.lexical_zh import LexicalZh
            self._lexical = LexicalZh(config=self._config)
        return self._lexical

    def _get_passage_store(self):
        if self._passage_store is not None:
            return self._passage_store
        try:
            from src.services.passage_store import PassageStore
            # Prefer injected db instance; singleton path uses Database.
            if self._db is not None and self._db is not Database:
                self._passage_store = PassageStore(db=self._db)
            else:
                self._passage_store = PassageStore()
            return self._passage_store
        except Exception as e:
            logging.debug("PassageStore unavailable: %s", e)
            return None

    def _use_passages(self) -> bool:
        """Prefer passage index when it has content (SPEC v3)."""
        force = self._get_config("rag.retrieval_unit", None)
        if force == "block":
            return False
        if force == "passage":
            return True
        if self._passage_mode is not None:
            return self._passage_mode
        store = self._get_passage_store()
        if store is None:
            self._passage_mode = False
            return False
        try:
            store.ensure_schema()
            n = store.count()
            self._passage_mode = n > 0
        except Exception:
            self._passage_mode = False
        return bool(self._passage_mode)

    def search(self, queries: list[str], top_k: int = 5) -> list[dict]:
        mode = self._get_config("rag.search_mode", "blend")
        use_passages = self._use_passages()
        if use_passages:
            if mode == "embedding":
                results, _vec_warnings = self._passage_vector_search(queries, top_k)
                if _vec_warnings:
                    for r in results:
                        r.setdefault("warnings", []).extend(_vec_warnings)
            elif mode == "keywords":
                results = self._passage_keyword_search(queries, top_k)
            else:
                results = self._passage_blend_search(queries, top_k)
            for r in results:
                r.setdefault("metadata", {})["retrieval_unit"] = "passage"
            return results

        if mode == "embedding":
            results, _vec_warnings = self._vector_search(queries, top_k)
            if _vec_warnings:
                for r in results:
                    r.setdefault("warnings", []).extend(_vec_warnings)
        elif mode == "keywords":
            results = self._keyword_search(queries, top_k)
        else:
            results = self._blend_search(queries, top_k)

        # 为每个结果回溯父链上下文（小块 → 父块标题链）
        for r in results:
            enrich_result_with_context(r)

        # Parent-Child 检索增强：附加父块完整内容
        if self._get_config("rag.parent_child.enabled", False):
            try:
                from src.services.parent_child_retrieval import enrich_with_parent_context
                results = enrich_with_parent_context(results, db=self._db)
            except Exception as e:
                logging.warning("Parent-child enrichment failed: %s", e)

        return results

    # ------------------------------------------------------------------
    # Passage-level channels (SPEC v3)
    # ------------------------------------------------------------------
    def _passage_vector_search(self, queries: list[str], top_k: int) -> tuple[list[dict], list[str]]:
        store = self._get_passage_store()
        results: list[dict] = []
        seen: set[str] = set()
        warnings: list[str] = []
        if store is None:
            return [], ["passage store unavailable"]
        for query in queries:
            try:
                hits = store.vector_search(query, top_k=top_k * 2)
                for r in hits:
                    cid = r.get("id") or r.get("passage_id")
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)
                    results.append(r)
            except Exception as e:
                msg = f"passage vector channel degraded: {type(e).__name__}: {e}"
                warnings.append(msg[:300])
                logging.warning("Passage vector search failed: %s", e)
        results.sort(
            key=lambda x: (1 - float(x.get("distance") or 0) / 2, -len(x.get("text") or "")),
            reverse=True,
        )
        return results[: top_k * 2], warnings

    def _passage_keyword_search(self, queries: list[str], top_k: int) -> list[dict]:
        store = self._get_passage_store()
        results: list[dict] = []
        seen: set[str] = set()
        if store is None:
            return []
        for query in queries:
            try:
                expanded = self._get_lexical().expand_query(query)
                hits = store.fts_search(expanded, top_k=top_k * 2)
                for r in hits:
                    cid = r.get("id") or r.get("passage_id")
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)
                    results.append(r)
            except Exception as e:
                logging.warning("Passage keyword search failed: %s", e)
        return results[: top_k * 2]

    def _passage_blend_search(self, queries: list[str], top_k: int) -> list[dict]:
        with ThreadPoolExecutor(max_workers=2) as pool:
            vec_future = pool.submit(self._passage_vector_search, queries, top_k * 3)
            fts_future = pool.submit(self._passage_keyword_search, queries, top_k * 3)
            try:
                vec_results, vec_warnings = vec_future.result()
            except Exception as e:
                vec_results, vec_warnings = [], [f"passage vector channel failed: {e}"]
            try:
                fts_results = fts_future.result()
            except Exception as e:
                fts_results = []
                logging.warning("Passage keyword channel failed in blend: %s", e)

        k = self._get_config("rag.rrf_k", 40)
        w_semantic = float(self._get_config("rag.rrf_weight_semantic", 0.4))
        lang = detect_query_language(queries[0] if queries else "")
        if lang == "zh":
            w_keyword = float(self._get_config("rag.rrf_weight_keyword_zh", 0.7))
        else:
            w_keyword = float(self._get_config("rag.rrf_weight_keyword_en", 0.5))
        total_w = w_semantic + w_keyword
        if total_w > 0:
            w_semantic /= total_w
            w_keyword /= total_w

        proper_nouns: list[str] = []
        for q in queries:
            proper_nouns.extend(detect_proper_nouns(q))
        proper_noun_boost = float(self._get_config("rag.proper_noun_boost", 1.5)) if proper_nouns else 1.0

        rrf_scores: dict[str, float] = {}
        rrf_breakdown: dict[str, dict] = {}
        result_map: dict[str, dict] = {}
        vec_ids: set[str] = set()
        fts_ids: set[str] = set()

        for rank, item in enumerate(vec_results):
            item_id = self._candidate_id(item)
            semantic_rrf = w_semantic / (k + rank + 1)
            rrf_scores[item_id] = rrf_scores.get(item_id, 0) + semantic_rrf
            rrf_breakdown.setdefault(item_id, {"semantic_rrf": 0, "keyword_rrf": 0})
            rrf_breakdown[item_id]["semantic_rrf"] += semantic_rrf
            vec_ids.add(item_id)
            if item_id not in result_map:
                result_map[item_id] = {
                    "id": item.get("id", item_id),
                    "text": item.get("text", ""),
                    "metadata": self._metadata_with_block_id(item, item.get("id", item_id)),
                    "distance": item.get("distance", 0),
                    "vector_score": item.get(
                        "vector_score",
                        normalize_vector_score(item.get("distance", 0)),
                    ),
                    "passage_id": item.get("passage_id") or item.get("id"),
                    "knowledge_id": item.get("knowledge_id")
                    or (item.get("metadata") or {}).get("knowledge_id"),
                    "document_family_id": item.get("document_family_id")
                    or (item.get("metadata") or {}).get("document_family_id"),
                    "version_year": item.get("version_year")
                    or (item.get("metadata") or {}).get("version_year"),
                }

        for rank, item in enumerate(fts_results):
            item_id = self._candidate_id(item)
            keyword_rrf = w_keyword * proper_noun_boost / (k + rank + 1)
            rrf_scores[item_id] = rrf_scores.get(item_id, 0) + keyword_rrf
            rrf_breakdown.setdefault(item_id, {"semantic_rrf": 0, "keyword_rrf": 0})
            rrf_breakdown[item_id]["keyword_rrf"] += keyword_rrf
            fts_ids.add(item_id)
            if item_id not in result_map:
                result_map[item_id] = {
                    "id": item.get("id", item_id),
                    "text": item.get("text", ""),
                    "metadata": self._metadata_with_block_id(item, item.get("id", item_id)),
                    "distance": item.get("distance", 0),
                    "fts_rank": item.get("fts_rank", 0),
                    "keyword_score": item.get(
                        "keyword_score",
                        normalize_fts_score(item.get("fts_rank", 0)),
                    ),
                    "passage_id": item.get("passage_id") or item.get("id"),
                    "knowledge_id": item.get("knowledge_id")
                    or (item.get("metadata") or {}).get("knowledge_id"),
                    "document_family_id": item.get("document_family_id")
                    or (item.get("metadata") or {}).get("document_family_id"),
                    "version_year": item.get("version_year")
                    or (item.get("metadata") or {}).get("version_year"),
                }
            else:
                result_map[item_id].setdefault("fts_rank", item.get("fts_rank", 0))
                result_map[item_id].setdefault(
                    "keyword_score",
                    item.get("keyword_score", normalize_fts_score(item.get("fts_rank", 0))),
                )

        sorted_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)
        results = []
        for item_id in sorted_ids[: top_k * 3]:
            item = result_map[item_id]
            item["rrf_score"] = rrf_scores[item_id]
            item["final_score"] = rrf_scores[item_id]
            channels = []
            if item_id in vec_ids:
                channels.append("semantic")
            if item_id in fts_ids:
                channels.append("keyword")
            if proper_nouns and item_id in fts_ids:
                channels.append("proper_noun_boost")
            item["match_channels"] = channels
            item.setdefault("vector_score", None)
            item.setdefault("keyword_score", None)
            bd = rrf_breakdown.get(item_id, {})
            item["score_breakdown"] = {
                "semantic_rrf": round(bd.get("semantic_rrf", 0), 6),
                "keyword_rrf": round(bd.get("keyword_rrf", 0), 6),
                "proper_noun_boost": proper_noun_boost if proper_nouns else 1.0,
                "proper_nouns": proper_nouns,
                "retrieval_unit": "passage",
            }
            results.append(item)

        # Diversity: keep best per (doc, section) then fill remaining slots.
        diversified = self._diversify_passages(results, top_k * 2)
        final = self._preserve_keyword_hits(diversified, top_k)
        if vec_warnings:
            for item in final:
                item.setdefault("warnings", []).extend(vec_warnings)
        return final

    def _diversify_passages(self, items: list[dict], limit: int) -> list[dict]:
        """Same-doc multi-passage diversity: keep best passage per section, then fill."""
        if not items:
            return []
        selected: list[dict] = []
        seen_section: set[tuple[str, str]] = set()
        seen_id: set[str] = set()
        # First pass: best per (knowledge_id, section_path)
        for item in items:
            meta = item.get("metadata") or {}
            kid = str(
                item.get("knowledge_id")
                or meta.get("knowledge_id")
                or meta.get("page_id")
                or ""
            )
            section = str(meta.get("section_path") or "")
            pid = str(item.get("id") or item.get("passage_id") or "")
            key = (kid, section)
            if key in seen_section:
                continue
            seen_section.add(key)
            if pid:
                seen_id.add(pid)
            selected.append(item)
            if len(selected) >= limit:
                return selected
        # Second pass: additional passages from same docs for exceptions/numbers.
        for item in items:
            pid = str(item.get("id") or item.get("passage_id") or "")
            if pid and pid in seen_id:
                continue
            if pid:
                seen_id.add(pid)
            selected.append(item)
            if len(selected) >= limit:
                break
        return selected

    def _vector_search(self, queries: list[str], top_k: int) -> tuple[list[dict], list[str]]:
        """返回 (候选列表, 降级告警列表)。

        向量通道失败时**不抛异常**（避免连累并行执行的 keyword 通道），
        而是把失败原因收集到 warnings，由 _blend_search 合并到候选的
        warnings 字段，让上层和用户能区分"索引空 / API 失败 / 正常未命中"。
        """
        results = []
        seen = set()
        warnings: list[str] = []
        for query in queries:
            try:
                vec_results = self._block_store.search(query, top_k=top_k * 2)
                if not vec_results:
                    logging.debug("Vector search returned 0 results for query=%r", query[:50])
                for r in vec_results:
                    cid = r["id"]
                    if cid not in seen:
                        seen.add(cid)
                        dist = r.get("distance", 0)
                        results.append({
                            "id": cid,
                            "text": r["text"],
                            "metadata": r.get("metadata", {}),
                            "distance": dist,
                            "vector_score": normalize_vector_score(dist),
                            "match_channels": ["semantic"],
                        })
            except Exception as e:
                # 关键：吞掉异常只记录，绝对不能向上抛，否则 _blend_search 的
                # ThreadPoolExecutor 会让 vector 失败连累 keyword 通道（新 P0）。
                msg = f"vector channel degraded: {type(e).__name__}: {e}"
                warnings.append(msg[:300])
                logging.warning("Vector search failed for query=%r: %s", query[:50], e)
        if not results:
            self._log_vector_coverage_diagnosis()
        results.sort(key=lambda x: (1 - x["distance"] / 2, -len(x.get("text", ""))), reverse=True)
        return results[:top_k * 2], warnings

    def _log_vector_coverage_diagnosis(self) -> None:
        """向量通道无结果时记录覆盖率诊断，帮助区分索引空 vs API 调用失败。

        覆盖率检查仅在 results 为空时执行，与 warnings 是否为空无关——
        这样 API 调用失败（results 为空、warnings 非空）时也能输出索引
        覆盖率，便于定位是"索引没建"还是"凭据/网络问题"。
        """
        try:
            # Prefer active knowledge coverage (excludes orphan blocks from deleted items).
            row = self._db.get_conn().execute(
                """
                SELECT COUNT(*) AS total_blocks,
                       SUM(CASE WHEN v.rowid IS NOT NULL THEN 1 ELSE 0 END) AS covered_blocks
                FROM blocks AS b
                JOIN knowledge_items AS k ON k.id = b.page_id
                LEFT JOIN vec_blocks AS v ON v.rowid = b.rowid
                WHERE k.deleted_at IS NULL
                """
            ).fetchone()
            block_count = int(row[0] or 0) if not hasattr(row, "keys") else int(row["total_blocks"] or 0)
            vec_count = int(row[1] or 0) if not hasattr(row, "keys") else int(row["covered_blocks"] or 0)
            if block_count and vec_count == 0:
                logging.warning(
                    "Vector index is EMPTY (%d active blocks, 0 embeddings). "
                    "Semantic search unavailable; FTS fallback only. "
                    "Run reindex_all to rebuild vector index.",
                    block_count,
                )
            elif block_count > 0 and vec_count / block_count < 0.5:
                logging.warning(
                    "Vector index coverage very low: %d/%d active blocks (%.1f%%). "
                    "Semantic search degraded. Run reindex_all to rebuild.",
                    vec_count, block_count, vec_count / block_count * 100,
                )
        except Exception as diag_exc:
            logging.debug("Vector coverage diagnosis failed: %s", diag_exc)

    def _keyword_search(self, queries: list[str], top_k: int) -> list[dict]:
        results = []
        seen = set()
        for query in queries:
            try:
                expanded = self._get_lexical().expand_query(query)  # W3: 同义词扩展
                fts_results = self._db.search_blocks_fts(expanded, limit=top_k * 2)
                for r in fts_results:
                    cid = r["id"]
                    if cid not in seen:
                        seen.add(cid)
                        fts_rank = r.get("fts_rank", 0)
                        properties = r.get("properties", {})
                        metadata = dict(properties)
                        metadata.update({
                            "page_id": r.get("page_id", ""),
                            "block_id": cid,
                            "block_type": r.get("block_type", ""),
                            "properties": properties,
                        })
                        results.append({
                            "id": cid,
                            "text": r.get("content", ""),
                            "metadata": metadata,
                            "distance": 0,
                            "fts_rank": fts_rank,
                            "keyword_score": normalize_fts_score(fts_rank),
                            "match_channels": ["keyword"],
                        })
            except Exception as e:
                logging.warning(f"Keyword search failed: {e}")
        return results[:top_k * 2]

    def _blend_search(self, queries: list[str], top_k: int) -> list[dict]:
        # 向量搜索和关键词搜索并行执行，减少总耗时
        with ThreadPoolExecutor(max_workers=2) as pool:
            vec_future = pool.submit(self._vector_search, queries, top_k * 3)
            fts_future = pool.submit(self._keyword_search, queries, top_k * 3)
            # 关键：捕获 .result() 可能抛出的异常，确保一个通道失败不连累另一个
            try:
                vec_results, vec_warnings = vec_future.result()
            except Exception as e:
                vec_results, vec_warnings = [], [f"vector channel failed: {e}"]
                logging.warning(f"Vector search channel failed in blend: {e}")
            try:
                fts_results = fts_future.result()
            except Exception as e:
                fts_results = []
                logging.warning(f"Keyword search channel failed in blend: {e}")

        # Phase 2: 可配置 RRF 参数
        k = self._get_config("rag.rrf_k", 40)
        w_semantic = float(self._get_config("rag.rrf_weight_semantic", 0.4))
        # W3 Task 3.3: 按查询语种选 keyword 权重（queries[0] 为原始 query）
        lang = detect_query_language(queries[0] if queries else "")
        if lang == "zh":
            w_keyword = float(self._get_config("rag.rrf_weight_keyword_zh", 0.7))
        else:
            w_keyword = float(self._get_config("rag.rrf_weight_keyword_en", 0.5))
        # 归一化权重，防止用户设置不当导致分数膨胀
        total_w = w_semantic + w_keyword
        if total_w > 0:
            w_semantic /= total_w
            w_keyword /= total_w
        else:
            # 两权重都设 0 时归一化被跳过,rrf_score 全为 0,排序退化为原序。
            # 原实现静默,这里记 warning 便于诊断(防用户误配后召回质量莫名下降)。
            logging.warning(
                "rag.rrf_weight_semantic + rrf_weight_keyword 均为 0,RRF 分数将全部 "
                "为 0,排序退化为原序——请检查权重配置"
            )

        # Phase 2: 专有名词检测 → keyword 通道加权
        proper_nouns = []
        for q in queries:
            proper_nouns.extend(detect_proper_nouns(q))
        proper_noun_boost = float(self._get_config("rag.proper_noun_boost", 1.5)) if proper_nouns else 1.0
        if proper_nouns:
            logging.debug(f"Proper nouns detected in query: {proper_nouns}, keyword boost={proper_noun_boost}")

        rrf_scores: dict[str, float] = {}
        rrf_breakdown: dict[str, dict] = {}  # Phase 2: score decomposition
        result_map: dict[str, dict] = {}
        # 跟踪每个 item 来自哪个通道
        vec_ids = set()
        fts_ids = set()

        for rank, item in enumerate(vec_results):
            item_id = self._candidate_id(item)
            semantic_rrf = w_semantic / (k + rank + 1)
            rrf_scores[item_id] = rrf_scores.get(item_id, 0) + semantic_rrf
            rrf_breakdown.setdefault(item_id, {"semantic_rrf": 0, "keyword_rrf": 0})
            rrf_breakdown[item_id]["semantic_rrf"] += semantic_rrf
            vec_ids.add(item_id)
            if item_id not in result_map:
                result_map[item_id] = {
                    "id": item.get("id", item_id),
                    "text": item["text"],
                    "metadata": self._metadata_with_block_id(item, item.get("id", item_id)),
                    "distance": item.get("distance", 0),
                    "vector_score": item.get("vector_score", normalize_vector_score(item.get("distance", 0))),
                }

        for rank, item in enumerate(fts_results):
            item_id = self._candidate_id(item)
            keyword_rrf = w_keyword * proper_noun_boost / (k + rank + 1)
            rrf_scores[item_id] = rrf_scores.get(item_id, 0) + keyword_rrf
            rrf_breakdown.setdefault(item_id, {"semantic_rrf": 0, "keyword_rrf": 0})
            rrf_breakdown[item_id]["keyword_rrf"] += keyword_rrf
            fts_ids.add(item_id)
            if item_id not in result_map:
                result_map[item_id] = {
                    "id": item.get("id", item_id),
                    "text": item["text"],
                    "metadata": self._metadata_with_block_id(item, item.get("id", item_id)),
                    "distance": item.get("distance", 0),
                    "fts_rank": item.get("fts_rank", 0),
                    "keyword_score": item.get("keyword_score", normalize_fts_score(item.get("fts_rank", 0))),
                }
            else:
                result_map[item_id].setdefault("fts_rank", item.get("fts_rank", 0))
                result_map[item_id].setdefault("keyword_score",
                    item.get("keyword_score", normalize_fts_score(item.get("fts_rank", 0))))

        sorted_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)
        results = []
        for item_id in sorted_ids[:top_k * 2]:
            item = result_map[item_id]
            item["rrf_score"] = rrf_scores[item_id]
            item["final_score"] = rrf_scores[item_id]  # blend 阶段 final_score = rrf_score

            # 构建 match_channels
            channels = []
            if item_id in vec_ids:
                channels.append("semantic")
            if item_id in fts_ids:
                channels.append("keyword")
            if proper_nouns and item_id in fts_ids:
                channels.append("proper_noun_boost")
            item["match_channels"] = channels

            # 确保 vector_score 和 keyword_score 都有值
            item.setdefault("vector_score", None)
            item.setdefault("keyword_score", None)

            # Phase 2: score_breakdown (debug)
            bd = rrf_breakdown.get(item_id, {})
            item["score_breakdown"] = {
                "semantic_rrf": round(bd.get("semantic_rrf", 0), 6),
                "keyword_rrf": round(bd.get("keyword_rrf", 0), 6),
                "proper_noun_boost": proper_noun_boost if proper_nouns else 1.0,
                "proper_nouns": proper_nouns,
            }

            results.append(item)

        final = self._preserve_keyword_hits(results, top_k)
        # 向量通道降级时，把诊断信息合并到所有候选的 warnings 字段，
        # 让上层 CitationBuilder / search_service 能透传给用户。用
        # setdefault+extend 而非覆盖，避免冲掉 reranker/parent-child 写入的既有 warnings。
        if vec_warnings:
            for item in final:
                item.setdefault("warnings", []).extend(vec_warnings)
        return final

    @staticmethod
    def _candidate_id(item: dict) -> str:
        page_id = item.get("metadata", {}).get("page_id", "")
        block_id = item.get("id", "")
        if page_id and block_id:
            return f"{page_id}:{block_id}"
        kid = item.get("metadata", {}).get("knowledge_id", "")
        cidx = str(item.get("metadata", {}).get("chunk_index", 0))
        if kid:
            return f"{kid}:{cidx}"
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
