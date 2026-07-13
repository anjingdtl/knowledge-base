"""CitationBuilder — 构建结构化、可解释的引用信息。

search 和 ask 共用，从检索候选 dict 构建 Citation 对象。
Phase 4: Claim Citation + Evidence 链 + Conflict Citation（向后兼容字段）。
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

    def build_claim_citation(self, candidate: dict) -> dict[str, Any]:
        """Build Claim + Evidence citation dict (Spec §8.1–§8.2).

        Returns a plain dict (not only Citation) so claim-specific fields
        remain available while embedding a legacy Citation for raw evidence.
        """
        evidence_out: list[dict[str, Any]] = []
        for ev in candidate.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            evidence_out.append({
                "knowledge_id": ev.get("knowledge_id") or "",
                "block_id": ev.get("block_id") or "",
                "path": ev.get("path") or "",
                "location": ev.get("location") or {},
                "excerpt": ev.get("excerpt") or "",
                "evidence_stance": ev.get("stance") or ev.get("evidence_stance") or "supports",
                "stale": bool(ev.get("stale")),
                "ok": ev.get("ok", True),
            })
        primary = evidence_out[0] if evidence_out else {}
        claim_id = candidate.get("claim_id") or candidate.get("candidate_id") or ""
        knowledge_id = candidate.get("knowledge_id") or primary.get("knowledge_id") or ""
        block_id = candidate.get("block_id") or primary.get("block_id") or ""

        legacy = Citation(
            document=candidate.get("title") or f"Claim:{claim_id}",
            path=primary.get("path") or "",
            knowledge_id=knowledge_id,
            block_id=block_id,
            location=CitationLocation(
                page=(primary.get("location") or {}).get("page"),
                sheet=(primary.get("location") or {}).get("sheet"),
                slide=(primary.get("location") or {}).get("slide"),
                heading_path=list((primary.get("location") or {}).get("heading_path") or []),
                paragraph_index=(primary.get("location") or {}).get("paragraph_index"),
                line_start=(primary.get("location") or {}).get("line_start"),
                line_end=(primary.get("location") or {}).get("line_end"),
            ),
            score=float(candidate.get("score") or 0.0),
            score_breakdown=dict(candidate.get("score_breakdown") or {}),
            match_channels=list(candidate.get("match_channels") or ["verified_wiki"]),
            reason="verified_claim",
            text=candidate.get("text") or candidate.get("statement") or "",
        )
        payload = legacy.to_dict()
        payload.update({
            "citation_layer": "claim",
            "claim_id": claim_id,
            "statement": candidate.get("text") or candidate.get("statement") or "",
            "status": candidate.get("status") or "active",
            "revision": candidate.get("revision"),
            "page_id": candidate.get("page_id"),
            "validation": "passed" if candidate.get("eligible", True) else "disclose",
            "evidence": evidence_out,
            "source_layer": "canonical",
            "disclose_only": bool(candidate.get("disclose_only")),
        })
        return payload

    def build_conflict_citations(self, conflict: dict) -> dict[str, Any]:
        """Package both sides of a conflict with evidence (Spec §7.7 / §8.3)."""
        return {
            "citation_layer": "conflict",
            "reason_codes": list(conflict.get("reason_codes") or []),
            "sides": [
                {
                    "claim_id": conflict.get("claim_a_id"),
                    "statement": conflict.get("statement_a"),
                    "status": conflict.get("status_a"),
                    "evidence": list(conflict.get("evidence_a") or []),
                },
                {
                    "claim_id": conflict.get("claim_b_id"),
                    "statement": conflict.get("statement_b"),
                    "status": conflict.get("status_b"),
                    "evidence": list(conflict.get("evidence_b") or []),
                },
            ],
        }

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
