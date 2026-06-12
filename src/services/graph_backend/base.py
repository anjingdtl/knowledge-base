"""插件式图数据库后端 — 抽象基类

定义了图谱操作的统一接口，支持 SQLite（默认）、Neo4j 等多种后端。
所有图谱服务（UnifiedGraphService、GraphTraversalService、SourceGraph、
GraphRepository、FileGraphService）均通过此接口访问底层图存储。

设计原则:
    - 读操作优先：图遍历/查询路径必须高效
    - ID 约定：node_id = "{type}:{source_id}"，如 "page:abc", "block:def", "tag:python"
    - edge_type 复用现有关系名：contains, parent, tagged_with, mention, references 等
    - 后端实现可无状态（SQLite 每次从 DB 查）或有状态（Neo4j 维护连接池）
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 标准数据结构
# ---------------------------------------------------------------------------

@dataclass
class GraphNode:
    """图后端统一节点格式"""
    id: str                               # 带前缀的 ID，如 "page:abc"
    node_type: str                        # page | block | tag | entity
    label: str = ""
    source_id: str = ""                   # 原始 ID（不带前缀）
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.node_type,
            "label": self.label,
            "source_id": self.source_id,
            "properties": self.properties,
        }


@dataclass
class GraphEdge:
    """图后端统一边格式"""
    source: str                           # 带前缀的 source node ID
    target: str                           # 带前缀的 target node ID
    edge_type: str = "related"
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.edge_type,
            "properties": self.properties,
        }


@dataclass
class TraversalResult:
    """BFS 遍历结果"""
    nodes: list[dict]
    edges: list[dict]
    paths: list[list[str]]
    truncated: bool = False


@dataclass
class SubgraphResult:
    """子图查询结果"""
    nodes: list[dict]
    edges: list[dict]
    truncated: bool = False


# ---------------------------------------------------------------------------
# ID 工具
# ---------------------------------------------------------------------------

def make_node_id(node_type: str, source_id: str) -> str:
    """构造带前缀的节点 ID"""
    if not source_id:
        return ""
    # 已经带前缀的直接返回
    if ":" in source_id and source_id.split(":", 1)[0] in (
        "page", "block", "tag", "entity", "knowledge",
    ):
        return source_id
    # knowledge 类型统一映射为 page
    if node_type in ("knowledge", "page"):
        return f"page:{source_id}"
    if node_type == "block":
        return f"block:{source_id}"
    if node_type == "tag":
        return f"tag:{source_id}"
    return f"{node_type}:{source_id}"


def parse_node_id(node_id: str) -> tuple[str, str]:
    """解析带前缀的节点 ID，返回 (node_type, source_id)"""
    if ":" in node_id:
        prefix, _, sid = node_id.partition(":")
        # knowledge 和 page 统一为 page
        if prefix == "knowledge":
            prefix = "page"
        return prefix, sid
    return "page", node_id


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------

class GraphBackend(ABC):
    """图后端抽象基类

    子类必须实现以下方法:
    - upsert_node / upsert_edge：写入节点和边
    - get_node：查询单个节点
    - find_neighbors：查找邻居节点
    - traverse：BFS 遍历
    - load_subgraph：加载子图（统一图谱/SourceGraph 使用）
    - delete_node / delete_edges_by_node：删除操作
    - clear：清空图数据
    - health_check：健康检查
    """

    # ---- 名称/标识 ----

    @property
    @abstractmethod
    def name(self) -> str:
        """后端名称，如 'sqlite', 'neo4j', 'memgraph'"""
        ...

    # ---- 写操作 ----

    @abstractmethod
    def upsert_node(self, node: GraphNode) -> None:
        """创建或更新节点"""
        ...

    @abstractmethod
    def upsert_edge(self, edge: GraphEdge) -> None:
        """创建或更新边"""
        ...

    @abstractmethod
    def upsert_nodes_batch(self, nodes: list[GraphNode]) -> None:
        """批量创建或更新节点"""
        ...

    @abstractmethod
    def upsert_edges_batch(self, edges: list[GraphEdge]) -> None:
        """批量创建或更新边"""
        ...

    @abstractmethod
    def delete_node(self, node_id: str) -> None:
        """删除节点及其所有关联边"""
        ...

    @abstractmethod
    def delete_edges_by_node(self, node_id: str) -> None:
        """删除与指定节点关联的所有边（不删除节点本身）"""
        ...

    # ---- 读操作 ----

    @abstractmethod
    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """获取单个节点，不存在返回 None"""
        ...

    @abstractmethod
    def find_neighbors(
        self,
        node_id: str,
        edge_types: list[str] | None = None,
        direction: str = "both",
        limit: int = 1000,
    ) -> list[tuple[GraphNode, GraphEdge]]:
        """查找邻居节点

        Args:
            node_id: 节点 ID
            edge_types: 过滤边类型，None 表示所有类型
            direction: 'out' | 'in' | 'both'
            limit: 最大邻居数

        Returns:
            [(neighbor_node, connecting_edge), ...] 列表
        """
        ...

    @abstractmethod
    def traverse(
        self,
        start_ids: list[str],
        max_depth: int = 2,
        edge_types: list[str] | None = None,
        max_nodes: int = 200,
        node_filter_ids: set[str] | None = None,
    ) -> TraversalResult:
        """BFS 遍历

        Args:
            start_ids: 起始节点 ID 列表
            max_depth: 最大遍历深度
            edge_types: 过滤边类型
            max_nodes: 最大节点数
            node_filter_ids: 如果指定，只保留这些 ID 的节点（depth > 0 时）

        Returns:
            TraversalResult
        """
        ...

    @abstractmethod
    def load_subgraph(
        self,
        node_types: list[str] | None = None,
        edge_types: list[str] | None = None,
        node_limit: int = 500,
        edge_limit: int = 10000,
        block_limit: int | None = None,
    ) -> SubgraphResult:
        """加载子图（用于统一图谱构建、SourceGraph 等）

        Args:
            node_types: 过滤节点类型，None 表示所有类型
            edge_types: 过滤边类型
            node_limit: 最大节点数（页面）
            edge_limit: 最大边数
            block_limit: 最大 block 节点数（None 表示不单独限制）

        Returns:
            SubgraphResult
        """
        ...

    @abstractmethod
    def get_nodes_by_type(
        self,
        node_type: str,
        limit: int = 500,
        offset: int = 0,
    ) -> list[GraphNode]:
        """按类型获取节点列表"""
        ...

    @abstractmethod
    def get_edges_by_type(
        self,
        edge_type: str,
        limit: int = 10000,
        offset: int = 0,
    ) -> list[GraphEdge]:
        """按类型获取边列表"""
        ...

    # ---- 管理操作 ----

    def clear(self) -> None:
        """清空所有图数据（默认空操作，由子类覆盖）"""
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """检查后端是否可用"""
        ...

    def close(self) -> None:
        """关闭连接/释放资源（默认空操作）"""
        pass

    # ---- 统计信息 ----

    def stats(self) -> dict:
        """返回图统计信息（节点数、边数等），默认空实现"""
        return {"backend": self.name, "node_count": -1, "edge_count": -1}
