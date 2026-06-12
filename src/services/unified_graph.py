"""统一图谱服务 — 通过图后端构建 page/block/tag/entity 统一图结构

当配置了 Neo4j 等外部图后端时，统一图谱的构建委托给后端执行，
利用图数据库的原生遍历能力显著提升大规模数据下的性能。
未配置时回退到 SQLite 后端（行为与改造前完全一致）。
"""
import json
import logging
from typing import Optional

from src.models.unified_node import UnifiedNode, UnifiedEdge
from src.services.db import Database

logger = logging.getLogger(__name__)


class UnifiedGraphService:
    """从图后端构建统一图谱负载

    参数:
        db: 数据库实例（兼容旧调用方式）
        graph_backend: 图后端实例（可选）；为 None 时自动创建 SQLite 后端
    """

    def __init__(self, db=None, graph_backend=None):
        self._db = db or Database
        self._backend = graph_backend
        if self._backend is None:
            from src.services.graph_backend.sqlite_backend import SQLiteGraphBackend
            self._backend = SQLiteGraphBackend(db=self._db)

    def build(self, include_blocks: bool = True, include_tags: bool = True,
              page_limit: int = 500, block_limit: int | None = 1000,
              ref_limit: int | None = 10000) -> dict:
        """构建统一图谱，返回 {"nodes": [...], "edges": [...]}"""
        # 委托给图后端 — 无论 SQLite 还是 Neo4j 都通过统一接口处理
        node_types = ["page"]
        if include_blocks:
            node_types.append("block")
        if include_tags:
            node_types.append("tag")

        edge_limit = ref_limit if (ref_limit is not None and ref_limit >= 0) else 10000
        result = self._backend.load_subgraph(
            node_types=node_types,
            node_limit=page_limit,
            edge_limit=edge_limit,
            block_limit=block_limit,
        )

        return {
            "nodes": result.nodes,
            "edges": result.edges,
            "ref_truncated": result.truncated,
        }

    def _node_id(self, source_type: str, source_id: str) -> Optional[str]:
        """将 source_type/source_id 转换为带前缀的节点 ID

        映射：knowledge/page -> "page:X", block -> "block:X", tag -> "tag:X"
        其他类型原样返回 "type:X"
        """
        if not source_type or not source_id:
            return None
        if source_type in ("knowledge", "page"):
            return f"page:{source_id}"
        if source_type == "block":
            return f"block:{source_id}"
        if source_type == "tag":
            return f"tag:{source_id}"
        return f"{source_type}:{source_id}"

    @staticmethod
    def _load_json_list(value) -> list[str]:
        """安全解析 JSON 列表"""
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return []
