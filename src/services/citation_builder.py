"""CitationBuilder — 构建结构化、可解释的引用信息。

search 和 ask 共用，从检索候选 dict 构建 Citation 对象。
"""
from __future__ import annotations

import logging
from typing import Any

from src.models.citation import Citation, CitationLocation
from src.models.retrieval import (
    build_match_channels,
    build_match_reason,
    compute_final_score,
)

logger = logging.getLogger(__name__)


class CitationBuilder:
    """从检索候选构建结构化引用。

    Args:
        db: Database 实例，用于查询文档标题等元信息。
    """

    def __init__(self, db: Any = None):
        self._db = db

    def _get_db(self):
        if self._db is not None:
            return self._db
        from src.services.db import Database
        return Database

    def build(self, candidate: dict, item: dict | None = None) -> Citation:
        """从一条检索候选构建 Citation。

        Args:
            candidate: 检索候选 dict（来自 hybrid_search / reranker 输出）
            item: 预查询的 knowledge item dict（可选，避免重复查询）
        """
        metadata = candidate.get("metadata", {}) or {}

        # 解析文档标题
        title = "未知"
        if item and item.get("title"):
            title = item["title"]
        elif metadata.get("title"):
            title = metadata["title"]
        else:
            # BUG-8 fix: 从 metadata 中的 knowledge_id/page_id 回查标题
            kid = metadata.get("page_id") or metadata.get("knowledge_id", "")
            if kid:
                try:
                    db = self._get_db()
                    row = db.get_conn().execute(
                        "SELECT title FROM knowledge_items WHERE id = ? AND deleted_at IS NULL",
                        (kid,),
                    ).fetchone()
                    if row and row[0]:
                        title = row[0]
                except Exception:
                    pass

        # 构建定位信息
        location = self._location_from_metadata(metadata)

        # 构建分数细分
        score_breakdown = {
            "vector": candidate.get("vector_score"),
            "keyword": candidate.get("keyword_score"),
            "rrf": candidate.get("rrf_score"),
            "rerank": candidate.get("rerank_score"),
        }

        # 计算最终分数
        final_score = compute_final_score(candidate)

        # 构建匹配渠道
        channels = candidate.get("match_channels") or build_match_channels(candidate)

        # 构建匹配原因
        reranked = candidate.get("rerank_score") is not None
        reason = build_match_reason(candidate, reranked=reranked)

        # 解析 block_id
        block_id = (
            candidate.get("block_id")
            or metadata.get("block_id")
            or candidate.get("id", "")
        )

        # 解析 knowledge_id
        knowledge_id = (
            candidate.get("knowledge_id")
            or metadata.get("page_id")
            or metadata.get("knowledge_id", "")
        )

        return Citation(
            document=title,
            path=metadata.get("source_path", ""),
            knowledge_id=knowledge_id,
            block_id=block_id,
            location=location,
            score=final_score,
            score_breakdown=score_breakdown,
            match_channels=channels,
            reason=reason,
            text=candidate.get("text", ""),
        )

    def build_many(
        self,
        candidates: list[dict],
        max_per_doc: int = 3,
    ) -> list[Citation]:
        """批量构建 Citation，带去重和每文档限制。

        Args:
            candidates: 检索候选列表
            max_per_doc: 每篇文档最多引用条数
        """
        # 按 block_id 去重
        seen: set[str] = set()
        unique: list[dict] = []
        for c in candidates:
            bid = c.get("block_id") or c.get("id", "")
            if bid and bid not in seen:
                seen.add(bid)
                unique.append(c)

        # 按文档限制
        doc_counts: dict[str, int] = {}
        result: list[Citation] = []
        db = self._get_db()

        for c in unique:
            kid = (
                c.get("knowledge_id")
                or (c.get("metadata") or {}).get("page_id")
                or (c.get("metadata") or {}).get("knowledge_id", "")
            )
            count = doc_counts.get(kid, 0)
            if count >= max_per_doc:
                continue

            item = None
            if kid:
                try:
                    item = db.get_knowledge(kid)
                except Exception:
                    pass

            result.append(self.build(c, item))
            doc_counts[kid] = count + 1

        return result

    @staticmethod
    def _location_from_metadata(metadata: dict) -> CitationLocation:
        """从 block metadata 提取定位信息。

        Best-effort: PDF→page, Excel→sheet, PPTX→slide,
        Markdown/DOCX→heading_path+paragraph_index, code→line range。
        缺失字段为 None，不伪造。
        """
        return CitationLocation(
            page=metadata.get("page"),
            sheet=metadata.get("sheet"),
            slide=metadata.get("slide"),
            heading_path=metadata.get("heading_path", []),
            paragraph_index=metadata.get("paragraph_index"),
            line_start=metadata.get("line_start"),
            line_end=metadata.get("line_end"),
        )
