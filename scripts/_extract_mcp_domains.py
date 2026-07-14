#!/usr/bin/env python3
"""One-shot extractor: move ingest/admin/wiki tools out of mcp/server.py."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "src" / "mcp" / "server.py"

DOMAIN_FUNCS = {
    "administration": {
        "create",
        "update",
        "delete",
        "restore_knowledge",
        "query_operation_logs",
        "preview_operation",
        "get_operation_log",
        "undo_operation",
        "list_recent_operations",
    },
    "ingest": {
        "reindex_all",
        "list_knowledge",
        "index_path",
        "tags",
        "ingest_file",
        "ingest_url",
        "create_ingest_job",
        "get_job",
        "list_jobs",
        "cancel_job",
        "_validate_ingest_path",
        "_validate_file_path",
        "_do_ingest_file",
        "_do_ingest_url",
    },
    "wiki": {
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
    },
}

HEADER = '''"""{domain} domain MCP tools (WP2 extraction from server.py).

Implementations registered via tool_definition side-effect on import.
"""
from __future__ import annotations

import functools
import json
import logging
import os
from typing import Callable, ParamSpec, TypeVar

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
from src.services.wiki_compiler import try_wiki_compile as _try_wiki_compile
from src.utils.config import Config

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")

'''


def _decorator_span(source: str, node: ast.FunctionDef) -> int:
    """Start offset covering decorators (or function if none)."""
    if node.decorator_list:
        return min(d.lineno for d in node.decorator_list)
    return node.lineno


def extract():
    source = SERVER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)

    # Map name -> (start_line_1based, end_line_1based inclusive)
    spans: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = _decorator_span(source, node)
            end = node.end_lineno or node.lineno
            spans[node.name] = (start, end)

    removed: list[tuple[int, int]] = []
    domain_exports: dict[str, list[str]] = {k: [] for k in DOMAIN_FUNCS}

    for domain, names in DOMAIN_FUNCS.items():
        chunks: list[str] = [HEADER.format(domain=domain)]
        for name in sorted(names, key=lambda n: spans.get(n, (10**9, 0))[0]):
            if name not in spans:
                print(f"WARNING: missing function {name}")
                continue
            start, end = spans[name]
            # convert to 0-based slice
            piece = "".join(lines[start - 1 : end])
            # normalize internal references already matching names
            chunks.append("\n")
            chunks.append(piece)
            if not piece.endswith("\n"):
                chunks.append("\n")
            removed.append((start, end))
            if not name.startswith("_"):
                domain_exports[domain].append(name)

        out_path = ROOT / "src" / "mcp" / "tools" / f"{domain}.py"
        out_path.write_text("".join(chunks), encoding="utf-8")
        print(f"wrote {out_path} ({len(chunks)} chunks, exports={domain_exports[domain]})")

    # rebuild server without removed ranges
    keep = [True] * len(lines)
    for start, end in removed:
        for i in range(start - 1, end):
            keep[i] = False
    new_lines = [ln for i, ln in enumerate(lines) if keep[i]]
    new_text = "".join(new_lines)

    # inject domain imports after FastMCP instance (after mcp = FastMCP(...))
    inject = '''
# ---- Domain tool modules (register ToolDefinitions on import) ----
from src.mcp.tools import administration as _admin_tools  # noqa: F401
from src.mcp.tools import ingest as _ingest_tools  # noqa: F401
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
    # Place after mcp = FastMCP block — find first occurrence of "\nmcp = FastMCP"
    marker = "mcp = FastMCP("
    idx = new_text.find(marker)
    if idx < 0:
        raise SystemExit("cannot find FastMCP construction")
    # find end of call - next blank line after closing paren of FastMCP(
    # simple: after ")\n\n" following mcp =
    end_mcp = new_text.find("\n\n", idx)
    if end_mcp < 0:
        end_mcp = new_text.find("\n", idx)
    insert_at = end_mcp + 2
    new_text = new_text[:insert_at] + inject + new_text[insert_at:]

    SERVER.write_text(new_text, encoding="utf-8")
    print(f"rewrote {SERVER}, lines now ~{new_text.count(chr(10))+1}")


if __name__ == "__main__":
    extract()
