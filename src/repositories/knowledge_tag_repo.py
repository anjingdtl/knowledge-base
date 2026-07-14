"""KnowledgeTagRepository — tag candidate listing and tag updates.

Used by TaggingService / MCP auto_tag so application code does not embed SQL.
"""
from __future__ import annotations

import json
from typing import Any


class KnowledgeTagRepository:
    """Data access for knowledge item tags."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def _conn(self):
        return self._db.get_conn()

    def list_untagged(
        self,
        *,
        limit: int = 50,
        force: bool = False,
    ) -> list[Any]:
        """Return knowledge rows needing tags (or all when force=True)."""
        limit = max(1, min(int(limit), 500))
        if force:
            sql = (
                "SELECT id, title, content, tags FROM knowledge_items "
                "WHERE deleted_at IS NULL LIMIT ?"
            )
        else:
            sql = (
                "SELECT id, title, content, tags FROM knowledge_items "
                "WHERE deleted_at IS NULL AND (tags IS NULL OR tags = '' OR tags = '[]') "
                "LIMIT ?"
            )
        conn = self._conn()
        return list(conn.execute(sql, (limit,)).fetchall())

    def update_tags(self, knowledge_id: str, tags: list[str]) -> None:
        tags_json = json.dumps(tags, ensure_ascii=False)
        conn = self._conn()
        conn.execute(
            "UPDATE knowledge_items SET tags = ? WHERE id = ?",
            (tags_json, knowledge_id),
        )
        conn.commit()
