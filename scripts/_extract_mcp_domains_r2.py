#!/usr/bin/env python3
"""Round-2 extractor: graph + memory + remaining retrieval out of server.py."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "src" / "mcp" / "server.py"

DOMAIN_FUNCS = {
    "retrieval": {
        # public tools
        "ping",
        "search",
        "search_fulltext",
        "ask",
        "read",
        "structured_query",
        "explain_query",
        "route_query",
        "execute_query",
        "ask_with_query",
        "kb_capabilities",
        "get_trace",
        "auto_tag",
        "kb_health_check",
        # helpers used only by retrieval tools
        "_should_use_verified_ask",
        "_do_ask",
        "_resolve_read_target",
        "_load_json_dict",
        "_resolve_query_alias",
        "_natural_language_query_dsl",
        "_looks_like_json",
        "_parse_query_dsl_or_natural_language",
        "_search_sources_from_query",
        "_list_blocks_for_page",
        "_embedding_context_config",
        "_runtime_diagnostics",
        "_kb_capabilities_verified_fields",
    },
    "graph": {
        "graph_traverse",
        "get_source_graph",
    },
    "memory": {
        "remember_fact",
        "recall_facts",
        "update_project_context",
        "search_decisions",
        "summarize_recent_changes",
        "extract_tasks_from_doc",
        "delete_memory",
    },
    "operations": {
        "create_async_job",
        "get_async_job",
        "list_async_jobs",
        "cancel_async_job",
    },
}

HEADER = '''"""{domain} domain MCP tools (WP2 round-2 extraction from server.py).

Implementations registered via tool_definition side-effect on import.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, ParamSpec, TypeVar

from src.mcp.envelopes import (
    ErrorCode,
    attach_operation_id,
    dry_run_preview,
    fail,
    ok,
)
from src.mcp.tools.support import (
    check_write_policy as _check_write_policy,
    content_preview as _content_preview,
    define_tool as _define_tool,
    get_container as _get_container,
    heartbeat as _heartbeat,
    op_log as _op_log,
)
from src.services.file_parser import parse_file, parse_url
from src.services.wiki_compiler import try_wiki_compile as _try_wiki_compile
from src.utils.config import Config
from src.version import VERSION

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")

'''


def _decorator_start(node: ast.FunctionDef) -> int:
    if node.decorator_list:
        return min(d.lineno for d in node.decorator_list)
    return node.lineno


def extract() -> None:
    source = SERVER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)

    spans: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans[node.name] = (_decorator_start(node), node.end_lineno or node.lineno)

    removed: list[tuple[int, int]] = []
    exports: dict[str, list[str]] = {k: [] for k in DOMAIN_FUNCS}

    for domain, names in DOMAIN_FUNCS.items():
        chunks = [HEADER.format(domain=domain)]
        for name in sorted(names, key=lambda n: spans.get(n, (10**9,))[0]):
            if name not in spans:
                print(f"WARNING missing {name}")
                continue
            start, end = spans[name]
            piece = "".join(lines[start - 1 : end])
            chunks.append("\n")
            chunks.append(piece)
            if not piece.endswith("\n"):
                chunks.append("\n")
            removed.append((start, end))
            if not name.startswith("_"):
                exports[domain].append(name)

        out = ROOT / "src" / "mcp" / "tools" / f"{domain}.py"
        out.write_text("".join(chunks), encoding="utf-8")
        print(f"wrote {out.name}: exports={exports[domain]}")

    keep = [True] * len(lines)
    for start, end in removed:
        for i in range(start - 1, end):
            keep[i] = False
    new_lines = [ln for i, ln in enumerate(lines) if keep[i]]
    new_text = "".join(new_lines)

    # Extend domain import block
    old_block_start = new_text.find("# ---- Domain tool modules")
    if old_block_start < 0:
        raise SystemExit("domain import block not found")
    # replace from domain block through end of last re-export section
    # Find "from src.mcp.tools.wiki import" block end
    wiki_import = new_text.find("from src.mcp.tools.wiki import")
    if wiki_import < 0:
        raise SystemExit("wiki re-export not found")
    # end of wiki import parenthesis
    end_paren = new_text.find(")\n", wiki_import)
    if end_paren < 0:
        raise SystemExit("wiki import close not found")
    end_block = end_paren + 2

    inject = '''# ---- Domain tool modules (register ToolDefinitions on import) ----
from src.mcp.tools import administration as _admin_tools  # noqa: F401
from src.mcp.tools import graph as _graph_tools  # noqa: F401
from src.mcp.tools import ingest as _ingest_tools  # noqa: F401
from src.mcp.tools import memory as _memory_tools  # noqa: F401
from src.mcp.tools import operations as _operations_tools  # noqa: F401
from src.mcp.tools import retrieval as _retrieval_tools  # noqa: F401
from src.mcp.tools import wiki as _wiki_tools  # noqa: F401

# Re-export public tool callables for tests/import compatibility
from src.mcp.tools.administration import (  # noqa: E402
    create,
    delete,
    get_operation_log,
    list_recent_operations,
    preview_operation,
    query_operation_logs,
    restore_knowledge,
    undo_operation,
    update,
)
from src.mcp.tools.graph import (  # noqa: E402
    get_source_graph,
    graph_traverse,
)
from src.mcp.tools.ingest import (  # noqa: E402
    cancel_job,
    create_ingest_job,
    get_job,
    index_path,
    ingest_file,
    ingest_url,
    list_jobs,
    list_knowledge,
    reindex_all,
    tags,
)
from src.mcp.tools.memory import (  # noqa: E402
    delete_memory,
    extract_tasks_from_doc,
    recall_facts,
    remember_fact,
    search_decisions,
    summarize_recent_changes,
    update_project_context,
)
from src.mcp.tools.operations import (  # noqa: E402
    cancel_async_job,
    create_async_job,
    get_async_job,
    list_async_jobs,
)
from src.mcp.tools.retrieval import (  # noqa: E402
    ask,
    ask_with_query,
    auto_tag,
    execute_query,
    explain_query,
    get_trace,
    kb_capabilities,
    kb_health_check,
    ping,
    read,
    route_query,
    search,
    search_fulltext,
    structured_query,
)
from src.mcp.tools.wiki import (  # noqa: E402
    delete_wiki_page,
    fix_dead_references,
    save_to_wiki,
    wiki_approve,
    wiki_deprecate,
    wiki_lint,
    wiki_list_versions,
    wiki_reject,
    wiki_restore_version,
    wiki_submit_review,
    wiki_workflow_history,
)

'''
    new_text = new_text[:old_block_start] + inject + new_text[end_block:]
    SERVER.write_text(new_text, encoding="utf-8")
    print(f"server lines now ~{new_text.count(chr(10)) + 1}")


if __name__ == "__main__":
    extract()
