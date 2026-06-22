"""SQLite 图数据库后端

提供统一的图操作接口，当前唯一实现为 SQLiteGraphBackend。

快速使用:
    from src.services.graph_backend import create_graph_backend
    backend = create_graph_backend(config, db)
    result = backend.traverse(start_ids=["abc"], max_depth=2)
"""
from src.services.graph_backend.base import (
    GraphBackend,
    GraphEdge,
    GraphNode,
    SubgraphResult,
    TraversalResult,
    make_node_id,
    parse_node_id,
)
from src.services.graph_backend.factory import create_graph_backend
from src.services.graph_backend.sqlite_backend import SQLiteGraphBackend

__all__ = [
    "GraphBackend",
    "GraphEdge",
    "GraphNode",
    "SubgraphResult",
    "TraversalResult",
    "make_node_id",
    "parse_node_id",
    "create_graph_backend",
    "SQLiteGraphBackend",
]
