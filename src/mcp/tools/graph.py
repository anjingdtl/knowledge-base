"""graph domain MCP tools (WP2 round-2 extraction from server.py).

Implementations registered via tool_definition side-effect on import.
"""
from __future__ import annotations

import json
import logging
from typing import ParamSpec, TypeVar

from src.mcp.envelopes import (
    ErrorCode,
    fail,
    ok,
)
from src.mcp.tools.retrieval import (
    _search_sources_from_query,
)
from src.mcp.tools.support import (
    define_tool as _define_tool,
)
from src.mcp.tools.support import (
    get_container as _get_container,
)
from src.mcp.tools.support import (
    heartbeat as _heartbeat,
)

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


def _parse_start_ids(start_ids: str | list[str]) -> list[str] | dict:
    """Parse/normalize start_ids. Returns list[str] or fail() dict."""
    raw = start_ids
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return fail(
                ErrorCode.VALIDATION_ERROR,
                "start_ids 必须是 JSON 数组字符串，例如 '[\"knowledge-id\"]'",
            )
    if not isinstance(raw, list):
        return fail(
            ErrorCode.VALIDATION_ERROR,
            "start_ids 必须是非空字符串数组",
            start_ids=start_ids,
        )
    if len(raw) == 0:
        return fail(
            ErrorCode.VALIDATION_ERROR,
            "start_ids 不能为空数组",
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            return fail(
                ErrorCode.VALIDATION_ERROR,
                "start_ids 每一项必须是非空字符串",
                invalid_item=item,
            )
        value = item.strip()
        if not value:
            return fail(
                ErrorCode.VALIDATION_ERROR,
                "start_ids 每一项必须是非空字符串",
            )
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    max_starts = 50
    if len(normalized) > max_starts:
        return fail(
            ErrorCode.VALIDATION_ERROR,
            f"start_ids 数量超过上限 {max_starts}",
            count=len(normalized),
            max_starts=max_starts,
        )
    return normalized


def _validate_graph_params(
    *,
    limit: int,
    offset: int,
    max_depth: int,
    start_type: str,
) -> dict | None:
    """Return fail() envelope if invalid, else None."""
    from src.utils.config import Config

    max_graph_nodes = int(Config.get("rag.max_graph_nodes", 200) or 200)
    max_graph_depth = int(Config.get("rag.max_graph_depth", 3) or 3)

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        return fail(
            ErrorCode.VALIDATION_ERROR,
            f"limit 必须满足 1 <= limit <= {max_graph_nodes}",
            limit=limit,
        )
    if limit > max_graph_nodes:
        return fail(
            ErrorCode.VALIDATION_ERROR,
            f"limit 必须满足 1 <= limit <= {max_graph_nodes}",
            limit=limit,
            max_graph_nodes=max_graph_nodes,
        )
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        return fail(
            ErrorCode.VALIDATION_ERROR,
            "offset 必须 >= 0",
            offset=offset,
        )
    if not isinstance(max_depth, int) or isinstance(max_depth, bool):
        return fail(
            ErrorCode.VALIDATION_ERROR,
            f"max_depth 必须满足 0 <= max_depth <= {max_graph_depth}",
            max_depth=max_depth,
        )
    if max_depth < 0 or max_depth > max_graph_depth:
        return fail(
            ErrorCode.VALIDATION_ERROR,
            f"max_depth 必须满足 0 <= max_depth <= {max_graph_depth}",
            max_depth=max_depth,
            max_graph_depth=max_graph_depth,
        )
    if start_type not in ("knowledge", "block"):
        return fail(
            ErrorCode.VALIDATION_ERROR,
            'start_type 必须是 "knowledge" 或 "block"',
            start_type=start_type,
        )
    return None


@_define_tool(
    name="graph_traverse",
    description="从给定节点遍历知识图谱（多跳、限深度、限节点数）。"
    "start_ids 必须是 JSON 数组字符串，例如 '[\"knowledge-id\"]'；"
    "使用 limit 限制返回节点数。", annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="graph", side_effect="read",
    experimental=True,
)
@_heartbeat
def graph_traverse(
    start_ids: str | list[str],
    max_depth: int = 2,
    start_type: str = "knowledge",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Traverse the knowledge graph starting from given page/block IDs.

    Args:
        start_ids: JSON array of starting node IDs (e.g. '["page-id-1", "page-id-2"]')
            or a list[str] (compatible).
        max_depth: Maximum traversal depth
        start_type: Type of start nodes (knowledge or block)
        limit: 节点数上限
        offset: 分页偏移
    """
    from src.services.graph_pagination import (
        compute_graph_fetch_limit,
        paginate_graph_result,
    )
    from src.services.graph_traversal import GraphTraversalService
    from src.utils.config import Config

    param_error = _validate_graph_params(
        limit=limit, offset=offset, max_depth=max_depth, start_type=start_type
    )
    if param_error is not None:
        return param_error

    parsed = _parse_start_ids(start_ids)
    if isinstance(parsed, dict):
        return parsed
    ids = parsed

    container = _get_container()
    try:
        max_graph_nodes = int(Config.get("rag.max_graph_nodes", 200) or 200)
        fetch_limit = compute_graph_fetch_limit(
            limit=limit, offset=offset, max_graph_nodes=max_graph_nodes
        )
        service = GraphTraversalService(db=container.db, graph_backend=container.graph_backend)
        result = service.traverse(
            start_ids=ids,
            start_type=start_type,
            max_depth=max_depth,
            max_nodes=fetch_limit,
        )
        page = paginate_graph_result(
            result,
            limit=limit,
            offset=offset,
            max_graph_nodes=max_graph_nodes,
        )
        meta = page.pop("meta")
        return ok(
            {
                "nodes": page["nodes"],
                "edges": page["edges"],
                "paths": page["paths"],
                "truncated": page["truncated"],
            },
            limit=meta["limit"],
            offset=meta["offset"],
            next_offset=meta["next_offset"],
            total_estimate=meta["total_estimate"],
            total_estimate_is_exact=meta["total_estimate_is_exact"],
            truncated=page["truncated"],
            hard_limit_reached=meta.get("hard_limit_reached", False),
            max_graph_nodes=meta.get("max_graph_nodes", max_graph_nodes),
            max_depth=max_depth,
        )
    except Exception as exc:
        logger.exception("graph_traverse failed: %s", exc)
        return fail(ErrorCode.INTERNAL_ERROR, str(exc))

@_define_tool(
    name="get_source_graph",
    description="根据 sources、block_ids 或 knowledge_ids 构建 bounded source graph，供 Agent 追溯 RAG 证据链。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def get_source_graph(
    sources: list[dict] | str | None = None,
    block_ids: list[str] | str | None = None,
    knowledge_ids: list[str] | str | None = None,
    max_nodes: int = 50,
    query: str | None = None,
) -> dict:
    """Build a local source graph from RAG sources or explicit IDs."""
    def parse_list(value):
        if value is None:
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else [value]
            except json.JSONDecodeError:
                return [value]
        return value if isinstance(value, list) else [value]

    source_rows = parse_list(sources)
    if any(not isinstance(row, dict) for row in source_rows):
        return fail(ErrorCode.VALIDATION_ERROR, "sources must be a list of objects")

    for block_id in parse_list(block_ids):
        source_rows.append({"block_id": block_id})
    for knowledge_id in parse_list(knowledge_ids):
        source_rows.append({"knowledge_id": knowledge_id})
    if not source_rows and query:
        source_rows.extend(_search_sources_from_query(query, limit=max_nodes))

    if not source_rows:
        return fail(
            ErrorCode.VALIDATION_ERROR,
            "get_source_graph requires sources, block_ids, knowledge_ids, or query",
        )

    from src.services.source_graph import build_source_graph
    graph = build_source_graph(
        source_rows,
        db=_get_container().db,
        max_nodes=max(1, int(max_nodes or 50)),
        graph_backend=_get_container().graph_backend,
    )
    return ok(
        graph,
        node_count=graph.get("node_count", 0),
        edge_count=len(graph.get("edges", [])),
        truncated=graph.get("truncated", False),
        max_nodes=max_nodes,
    )
