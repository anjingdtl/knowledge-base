"""MCP tools by domain (WP2 maintainability closure).

Domain modules contain real tool implementations registered via
``tool_definition`` side-effects. ``src.mcp.server`` imports them for
FastMCP registration and re-exports public callables.
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
    "structured_query",
    "explain_query",
    "route_query",
    "execute_query",
    "ask_with_query",
    "get_trace",
    "auto_tag",
    "kb_health_check",
})

INGEST_TOOLS = frozenset({
    "index_path",
    "create_ingest_job",
    "get_job",
    "list_jobs",
    "reindex_all",
    "cancel_job",
    "ingest_file",
    "ingest_url",
    "tags",
})

ADMINISTRATION_TOOLS = frozenset({
    "create",
    "update",
    "delete",
    "restore_knowledge",
    "query_operation_logs",
    "preview_operation",
    "get_operation_log",
    "undo_operation",
    "list_recent_operations",
})

WIKI_TOOLS = frozenset({
    "save_to_wiki",
    "wiki_lint",
    "fix_dead_references",
    "wiki_submit_review",
    "wiki_approve",
    "wiki_reject",
    "wiki_deprecate",
    "wiki_workflow_history",
    "wiki_list_versions",
    "wiki_restore_version",
    "delete_wiki_page",
})

GRAPH_TOOLS = frozenset({
    "graph_query",
    "graph_neighbors",
    "graph_traverse",
    "get_source_graph",
})

OPERATIONS_TOOLS = frozenset({
    "create_async_job",
    "get_async_job",
    "list_async_jobs",
    "cancel_async_job",
})

MEMORY_TOOLS = frozenset({
    "remember_fact",
    "recall_facts",
    "update_project_context",
    "search_decisions",
    "summarize_recent_changes",
    "forget_fact",
    "delete_memory",
    "extract_tasks_from_doc",
})

DOMAIN_GROUPS = {
    "retrieval": RETRIEVAL_TOOLS,
    "ingest": INGEST_TOOLS,
    "administration": ADMINISTRATION_TOOLS,
    "wiki": WIKI_TOOLS,
    "graph": GRAPH_TOOLS,
    "memory": MEMORY_TOOLS,
    "operations": OPERATIONS_TOOLS,
}
