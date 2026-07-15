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
    start_ids: str,
    max_depth: int = 2,
    start_type: str = "knowledge",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Traverse the knowledge graph starting from given page/block IDs.

    Args:
        start_ids: JSON array of starting node IDs (e.g. '["page-id-1", "page-id-2"]')
        max_depth: Maximum traversal depth
        start_type: Type of start nodes (knowledge or block)
        limit: 节点数上限
        offset: 分页偏移
    """
    from src.services.graph_traversal import GraphTraversalService

    container = _get_container()
    try:
        ids = json.loads(start_ids) if isinstance(start_ids, str) else start_ids
        service = GraphTraversalService(db=container.db, graph_backend=container.graph_backend)
        result = service.traverse(start_ids=ids, start_type=start_type, max_depth=max_depth)
        # 截断节点数
        nodes = result.get("nodes", [])
        edges = result.get("edges", [])
        truncated = len(nodes) > limit
        if truncated:
            nodes = nodes[offset:offset + limit]
        return ok(
            {
                "nodes": nodes,
                "edges": edges,
                "paths": result.get("paths", []),
                "truncated": truncated or result.get("truncated", False),
            },
            limit=limit,
            offset=offset,
            max_depth=max_depth,
        )
    except json.JSONDecodeError:
        return fail(
            ErrorCode.VALIDATION_ERROR,
            "start_ids 必须是 JSON 数组字符串，例如 '[\"knowledge-id\"]'",
        )
    except Exception as exc:
        logger.exception("graph_traverse failed: %s", exc)
        return fail(ErrorCode.QUERY_PARSE_ERROR, str(exc))

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
