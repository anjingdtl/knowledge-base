"""Retrieval domain MCP tools — real implementations (WP2).

Tool registration metadata still uses server-side ``_define_tool`` wrappers
during migration; business logic lives here / in ``src.application``.
"""
from __future__ import annotations

from typing import Any

from src.application.retrieval_commands import RetrievalCommands
from src.mcp.tools import RETRIEVAL_TOOLS
from src.version import VERSION

DOMAIN = "retrieval"
TOOL_NAMES = RETRIEVAL_TOOLS


def get_commands(container: Any) -> RetrievalCommands:
    return RetrievalCommands(container)


def ping_payload() -> dict[str, Any]:
    return {
        "status": "alive",
        "timestamp": __import__("time").time(),
        "version": VERSION,
        "uptime_hint": "ok",
    }


def semantic_search(container: Any, query: str, *, top_k: int = 5) -> list[dict]:
    return get_commands(container).semantic_search(query, top_k=top_k)


def fulltext_search(
    container: Any, query: str, *, limit: int = 20, offset: int = 0,
) -> list[dict]:
    return get_commands(container).fulltext_search(
        query, limit=limit, offset=offset,
    )


def ask_verified(container: Any, question: str, *, top_k: int = 5) -> dict[str, Any]:
    return get_commands(container).ask_verified(question, top_k=top_k)
