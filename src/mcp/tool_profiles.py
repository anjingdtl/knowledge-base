"""Tool profile definitions — which tools belong to which profile."""
from __future__ import annotations

CORE_TOOLS = frozenset({
    "ping", "kb_capabilities", "search", "ask", "read",
    "list_knowledge", "index_path", "get_job", "list_jobs", "reindex_all",
})

EXTENDED_TOOLS = CORE_TOOLS | frozenset({
    "search_fulltext", "tags", "route_query", "execute_query",
    "structured_query", "explain_query", "ask_with_query",
    "get_source_graph", "create_ingest_job", "cancel_job",
})

ADMIN_TOOLS = EXTENDED_TOOLS | frozenset({
    "create", "update", "delete", "restore_knowledge", "ingest_url",
    "preview_operation", "get_operation_log", "undo_operation",
    "list_recent_operations", "query_operation_logs",
})

# Groups that are considered "experimental" (wiki, graph, memory)
EXPERIMENTAL_GROUPS = frozenset({"wiki", "graph", "memory"})

# All known profiles
PROFILES = frozenset({"core", "extended", "admin", "full", "legacy"})
