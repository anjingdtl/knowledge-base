"""Legacy tool alias registration — only active in legacy profile."""
from __future__ import annotations

import functools
import logging

from fastmcp import FastMCP

from src.mcp.tool_registry import ToolDefinition

logger = logging.getLogger(__name__)

# Alias definitions: namespaced_name -> original_name
ALIASES: dict[str, str] = {
    "kb.search": "search",
    "kb.search_fulltext": "search_fulltext",
    "kb.ask": "ask",
    "kb.ask_with_query": "ask_with_query",
    "kb.create": "create",
    "kb.read": "read",
    "kb.update": "update",
    "kb.delete": "delete",
    "kb.restore": "restore_knowledge",
    "kb.reindex": "reindex_all",
    "kb.list": "list_knowledge",
    "kb.tags": "tags",
    "kb.ingest_file": "ingest_file",
    "kb.ingest_url": "ingest_url",
    "kb.preview": "preview_operation",
    "kb.capabilities": "kb_capabilities",
    "kb.route_query": "route_query",
    "kb.execute_query": "execute_query",
    "kb.structured_query": "structured_query",
    "kb.explain_query": "explain_query",
    "kb.get_source_graph": "get_source_graph",
    "kb.get_job": "get_job",
    "kb.list_jobs": "list_jobs",
    "kb.cancel_job": "cancel_job",
    "kb.create_ingest_job": "create_ingest_job",
    "wiki.save": "save_to_wiki",
    "wiki.lint": "wiki_lint",
    "wiki.fix_dead_refs": "fix_dead_references",
    "wiki.submit_review": "wiki_submit_review",
    "wiki.approve": "wiki_approve",
    "wiki.reject": "wiki_reject",
    "wiki.deprecate": "wiki_deprecate",
    "wiki.history": "wiki_workflow_history",
    "wiki.list_versions": "wiki_list_versions",
    "wiki.restore_version": "wiki_restore_version",
    "wiki.delete": "delete_wiki_page",
    "graph.traverse": "graph_traverse",
    "ops.ping": "ping",
    "ops.query_logs": "query_operation_logs",
    "ops.get_log": "get_operation_log",
    "ops.undo": "undo_operation",
    "ops.list_recent": "list_recent_operations",
    "ops.health_check": "kb_health_check",
    "ops.get_trace": "get_trace",
    "ops.create_job": "create_async_job",
    "ops.get_job": "get_async_job",
    "ops.list_jobs": "list_async_jobs",
    "ops.cancel_job": "cancel_async_job",
    "memory.remember": "remember_fact",
    "memory.recall": "recall_facts",
    "memory.update_context": "update_project_context",
    "memory.search_decisions": "search_decisions",
    "memory.summarize_changes": "summarize_recent_changes",
    "memory.extract_tasks": "extract_tasks_from_doc",
    "memory.delete": "delete_memory",
}


def register_aliases(server: FastMCP, definitions: dict[str, ToolDefinition],
                     visible_tool_names: set[str]) -> None:
    """Register legacy aliases for tools that are in visible_tool_names."""
    for alias_name, original_name in ALIASES.items():
        if original_name not in visible_tool_names:
            continue
        original_def = definitions.get(original_name)
        if original_def is None:
            continue

        @functools.wraps(original_def.function)
        def _alias(*args, _fn=original_def.function, **kwargs):
            return _fn(*args, **kwargs)

        try:
            server.tool(
                _alias,
                name=alias_name,
                description=f"[-> {original_name}] {original_def.description[:80]}",
                annotations=original_def.annotations,
            )
        except Exception as exc:
            logger.debug("Alias %s registration failed: %s", alias_name, exc)
