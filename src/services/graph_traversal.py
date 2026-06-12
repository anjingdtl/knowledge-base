"""图遍历服务 — 通过图后端执行 BFS 遍历

当配置了 Neo4j 等外部图后端时，遍历操作委托给后端执行，
利用图数据库的原生遍历能力显著提升大规模数据下的性能。
"""
from src.services.db import Database


class GraphTraversalService:
    """图遍历服务

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

    def traverse(
        self,
        start_ids: list[str],
        start_type: str = "knowledge",
        max_depth: int = 2,
        ref_types: list[str] | None = None,
        node_filter=None,
        max_nodes: int = 200,
    ) -> dict:
        """BFS 遍历图谱

        Args:
            start_ids: 起始节点 ID 列表
            start_type: 起始节点类型（knowledge/page/block）
            max_depth: 最大遍历深度
            ref_types: 过滤边类型
            node_filter: 节点过滤器（QueryExecutor 配置）
            max_nodes: 最大节点数

        Returns:
            {"nodes": [...], "edges": [...], "paths": [...], "truncated": bool}
        """
        # 处理节点过滤器
        node_filter_ids = None
        if node_filter is not None:
            from src.services.query_executor import QueryExecutor
            filter_results = QueryExecutor(db=self._db).execute(node_filter)
            node_filter_ids = {r["id"] for r in filter_results}

        # 委托给图后端
        result = self._backend.traverse(
            start_ids=start_ids,
            max_depth=max_depth,
            edge_types=ref_types,
            max_nodes=max_nodes,
            node_filter_ids=node_filter_ids,
        )

        return {
            "nodes": result.nodes,
            "edges": result.edges,
            "paths": result.paths,
            "truncated": result.truncated,
        }
