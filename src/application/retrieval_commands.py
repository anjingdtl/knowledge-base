"""Retrieval application commands for MCP/API adapters."""
from __future__ import annotations

import time
from typing import Any


class RetrievalCommands:
    """Read-side knowledge retrieval without MCP envelope concerns."""

    def __init__(self, container: Any):
        self._c = container

    def ping(self, *, version: str) -> dict[str, Any]:
        return {
            "status": "alive",
            "timestamp": time.time(),
            "version": version,
            "uptime_hint": "ok",
        }

    def semantic_search(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        return list(self._c.search_service.search(query, top_k=top_k) or [])

    def fulltext_search(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        db = self._c.db
        output: list[dict[str, Any]] = []
        wiki_results = db.search_wiki_fts(query, limit=3)
        for wr in wiki_results:
            summary = wr.get("concept_summary", "")
            content_preview = (wr.get("content", "") or "")[:300]
            output.append({
                "source": "wiki",
                "title": wr["title"],
                "summary": summary,
                "text": f"[Wiki] {wr['title']}: {summary}\n{content_preview}",
                "fts_rank": wr.get("fts_rank", 0),
            })

        seen_block_ids: set[str] = set()
        seen_knowledge_ids: set[str] = set()
        block_results = db.search_blocks_fts(query, limit=max(limit + offset, limit))
        for block in block_results[offset:offset + limit]:
            block_id = block.get("id", "")
            knowledge_id = block.get("page_id", "")
            seen_block_ids.add(block_id)
            if knowledge_id:
                seen_knowledge_ids.add(knowledge_id)
            item = db.get_knowledge(knowledge_id) if knowledge_id else None
            output.append({
                "source": "knowledge",
                "match_channel": "block_fts",
                "match_channels": ["block_fts"],
                "block_id": block_id,
                "knowledge_id": knowledge_id,
                "title": item.get("title", "") if item else "",
                "text": block.get("content", ""),
                "block_type": block.get("block_type", ""),
                "properties": block.get("properties", {}),
                "fts_rank": block.get("fts_rank", 0),
            })

        # fill with item-level FTS if still short
        remaining = max(0, limit - sum(1 for r in output if r.get("source") == "knowledge"))
        if remaining > 0:
            rows = db.search_knowledge(query, limit=remaining + offset, offset=0)
            for row in rows:
                kid = row.get("id", "")
                if kid in seen_knowledge_ids:
                    continue
                seen_knowledge_ids.add(kid)
                output.append({
                    "source": "knowledge",
                    "match_channel": "knowledge_fts",
                    "match_channels": ["knowledge_fts"],
                    "block_id": "",
                    "knowledge_id": kid,
                    "title": row.get("title", ""),
                    "text": row.get("content", ""),
                    "fts_rank": row.get("fts_rank", 0),
                })
                remaining -= 1
                if remaining <= 0:
                    break
        return output

    def ask_verified(self, question: str, *, top_k: int = 5) -> dict[str, Any]:
        from src.answering.service import AnswerService

        svc = AnswerService(
            self._c.search_service,
            llm=self._c.llm,
            config=self._c.config,
        )
        return dict(svc.ask(question, top_k=top_k))
