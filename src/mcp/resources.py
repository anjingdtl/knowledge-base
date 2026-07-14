"""MCP resource registrations (kb://...)."""
from __future__ import annotations

import json
import logging

from fastmcp import FastMCP

from src.mcp.envelopes import ErrorCode
from src.mcp.tools.support import get_container

logger = logging.getLogger(__name__)


def register_resources(mcp: FastMCP) -> None:
    @mcp.resource("kb://knowledge/{item_id}")
    def get_knowledge_resource(item_id: str) -> str:
        """获取指定知识条目的完整内容。"""
        item = get_container().db.get_knowledge(item_id)
        if not item:
            return json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": ErrorCode.NOT_FOUND,
                        "message": f"知识条目不存在: {item_id}",
                        "details": {"item_id": item_id},
                    },
                },
                ensure_ascii=False,
            )
        return json.dumps({"ok": True, "data": item}, ensure_ascii=False, indent=2)

    @mcp.resource("kb://tags")
    def get_tags_resource() -> str:
        """获取知识库中所有标签。"""
        tags_list = get_container().db.get_all_tags()
        return json.dumps(
            {"ok": True, "data": {"tags": tags_list, "count": len(tags_list)}},
            ensure_ascii=False,
            indent=2,
        )

    @mcp.resource("kb://stats")
    def get_stats_resource() -> str:
        """获取知识库统计信息。"""
        c = get_container()
        try:
            chunk_count = c.block_store.count()
        except Exception:  # noqa: BLE001
            chunk_count = 0
        payload = {
            "ok": True,
            "data": {
                "knowledge_items": c.db.count_knowledge(),
                "vector_chunks": chunk_count,
                "tags": len(c.db.get_all_tags()),
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    register_resources.get_knowledge_resource = get_knowledge_resource  # type: ignore[attr-defined]
    register_resources.get_tags_resource = get_tags_resource  # type: ignore[attr-defined]
    register_resources.get_stats_resource = get_stats_resource  # type: ignore[attr-defined]
