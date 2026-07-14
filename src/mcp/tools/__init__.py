"""MCP tools by domain (Phase-3).

Domain modules:
  retrieval / ingest / administration / wiki / graph / memory

Tool *implementations* currently load via ``src.mcp.server`` (moved from
``mcp_server.py``). Domain modules below document grouping and re-export
registry names for architecture tests and future extraction PRs.
"""
from __future__ import annotations

# Domain → tool names (must match tool_registry names)
RETRIEVAL_TOOLS = frozenset({
    "ping",
    "kb_capabilities",
    "search",
    "ask",
    "read",
    "list_knowledge",
    "search_fulltext",
})

INGEST_TOOLS = frozenset({
    "index_path",
    "create_ingest_job",
    "get_job",
    "list_jobs",
    "reindex_all",
    "cancel_job",
})

ADMINISTRATION_TOOLS = frozenset({
    "create",
    "update",
    "delete",
    "restore",
    "list_operation_logs",
    "undo_operation",
})

WIKI_TOOLS = frozenset({
    "wiki_search",
    "wiki_get",
    "wiki_list",
    "save_to_wiki",
    "wiki_compile",
})

GRAPH_TOOLS = frozenset({
    "graph_query",
    "graph_neighbors",
})

MEMORY_TOOLS = frozenset({
    "remember_fact",
    "recall_facts",
    "update_project_context",
    "search_decisions",
    "summarize_recent_changes",
    "forget_fact",
})

DOMAIN_GROUPS = {
    "retrieval": RETRIEVAL_TOOLS,
    "ingest": INGEST_TOOLS,
    "administration": ADMINISTRATION_TOOLS,
    "wiki": WIKI_TOOLS,
    "graph": GRAPH_TOOLS,
    "memory": MEMORY_TOOLS,
}
