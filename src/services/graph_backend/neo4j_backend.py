"""Neo4j 图后端 — 通过 neo4j Python 驱动实现高效图遍历

适用场景:
    - 知识库文档量超过 10 万篇
    - 需要多跳遍历、最短路径等复杂图查询
    - 需要实时图谱可视化的大规模数据

依赖:
    pip install neo4j

连接配置 (config.yaml):
    graph_backend:
      provider: neo4j
      uri: bolt://localhost:7687
      user: neo4j
      password: your_password
      database: neo4j          # 可选，默认 neo4j
      max_connection_pool_size: 50
"""
from __future__ import annotations

import json
import logging
from collections import deque
from typing import Optional

from src.services.graph_backend.base import (
    GraphBackend,
    GraphEdge,
    GraphNode,
    SubgraphResult,
    TraversalResult,
    make_node_id,
    parse_node_id,
)

logger = logging.getLogger(__name__)

# 尝试导入 neo4j 驱动 — 仅在 provider=neo4j 时需要
try:
    from neo4j import GraphDatabase as Neo4jDriver

    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False


def _check_neo4j_available():
    if not NEO4J_AVAILABLE:
        raise ImportError(
            "Neo4j Python driver is not installed. "
            "Install it with: pip install neo4j"
        )


class Neo4jGraphBackend(GraphBackend):
    """基于 Neo4j 的图后端

    数据模型:
        节点 Label: Page, Block, Tag
        节点属性: source_id (原始 ID), label (显示文本), + 业务属性
        关系类型: CONTAINS, PARENT, TAGGED_WITH, MENTION, REFERENCES,
                  RELATED, LINK, TAG_PARENT 等
    """

    # Neo4j Label 映射
    _LABEL_MAP = {
        "page": "Page",
        "block": "Block",
        "tag": "Tag",
        "entity": "Entity",
    }

    # 边类型映射（Python → Neo4j）
    _EDGE_TYPE_MAP = {
        "contains": "CONTAINS",
        "parent": "PARENT",
        "tagged_with": "TAGGED_WITH",
        "mention": "MENTION",
        "references": "REFERENCES",
        "related": "RELATED",
        "link": "LINK",
        "tag_parent": "TAG_PARENT",
        "prerequisite": "PREREQUISITE",
        "contradicts": "CONTRADICTS",
        "part_of": "PART_OF",
    }

    # 反向边类型映射（Neo4j → Python）
    _EDGE_TYPE_REVERSE = {v: k for k, v in _EDGE_TYPE_MAP.items()}

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "",
        database: str = "neo4j",
        max_connection_pool_size: int = 50,
        connection_timeout: float = 2.0,
    ):
        _check_neo4j_available()
        self._uri = uri
        self._user = user
        self._password = password
        self._database = database
        self._driver = Neo4jDriver.driver(
            uri,
            auth=(user, password),
            max_connection_pool_size=max_connection_pool_size,
            connection_timeout=connection_timeout,
            connection_acquisition_timeout=connection_timeout,
        )
        logger.info("Neo4j backend connected: %s (db=%s)", uri, database)

    @property
    def name(self) -> str:
        return "neo4j"

    # ------------------------------------------------------------------
    # 写操作
    # ------------------------------------------------------------------

    def upsert_node(self, node: GraphNode) -> None:
        label = self._LABEL_MAP.get(node.node_type, "Node")
        with self._driver.session(database=self._database) as session:
            session.run(
                f"""
                MERGE (n:{label} {{source_id: $source_id}})
                SET n.node_type = $node_type,
                    n.label = $label,
                    n.properties = $properties
                """,
                source_id=node.source_id or node.id,
                node_type=node.node_type,
                label=node.label,
                properties=json.dumps(node.properties, ensure_ascii=False),
            )

    def upsert_edge(self, edge: GraphEdge) -> None:
        src_type, src_id = parse_node_id(edge.source)
        tgt_type, tgt_id = parse_node_id(edge.target)
        src_label = self._LABEL_MAP.get(src_type, "Node")
        tgt_label = self._LABEL_MAP.get(tgt_type, "Node")
        rel_type = self._EDGE_TYPE_MAP.get(edge.edge_type, edge.edge_type.upper())

        with self._driver.session(database=self._database) as session:
            session.run(
                f"""
                MATCH (a:{src_label} {{source_id: $src_id}})
                MATCH (b:{tgt_label} {{source_id: $tgt_id}})
                MERGE (a)-[r:{rel_type}]->(b)
                SET r.properties = $properties
                """,
                src_id=src_id,
                tgt_id=tgt_id,
                properties=json.dumps(edge.properties, ensure_ascii=False),
            )

    def upsert_nodes_batch(self, nodes: list[GraphNode]) -> None:
        """批量 MERGE 节点 — 使用 UNWIND 优化性能"""
        if not nodes:
            return

        # 按 node_type 分组
        by_type: dict[str, list[dict]] = {}
        for node in nodes:
            by_type.setdefault(node.node_type, []).append({
                "source_id": node.source_id or node.id,
                "node_type": node.node_type,
                "label": node.label,
                "properties": json.dumps(node.properties, ensure_ascii=False),
            })

        with self._driver.session(database=self._database) as session:
            for node_type, items in by_type.items():
                label = self._LABEL_MAP.get(node_type, "Node")
                session.run(
                    f"""
                    UNWIND $items AS item
                    MERGE (n:{label} {{source_id: item.source_id}})
                    SET n.node_type = item.node_type,
                        n.label = item.label,
                        n.properties = item.properties
                    """,
                    items=items,
                )

    def upsert_edges_batch(self, edges: list[GraphEdge]) -> None:
        """批量 MERGE 边 — 使用 UNWIND 优化性能"""
        if not edges:
            return

        # 按 (src_type, tgt_type, edge_type) 分组
        grouped: dict[tuple[str, str, str], list[dict]] = {}
        for edge in edges:
            src_type, src_id = parse_node_id(edge.source)
            tgt_type, tgt_id = parse_node_id(edge.target)
            rel_type = self._EDGE_TYPE_MAP.get(edge.edge_type, edge.edge_type.upper())
            key = (src_type, tgt_type, rel_type)
            grouped.setdefault(key, []).append({
                "src_id": src_id,
                "tgt_id": tgt_id,
                "properties": json.dumps(edge.properties, ensure_ascii=False),
            })

        with self._driver.session(database=self._database) as session:
            for (src_type, tgt_type, rel_type), items in grouped.items():
                src_label = self._LABEL_MAP.get(src_type, "Node")
                tgt_label = self._LABEL_MAP.get(tgt_type, "Node")
                session.run(
                    f"""
                    UNWIND $items AS item
                    MATCH (a:{src_label} {{source_id: item.src_id}})
                    MATCH (b:{tgt_label} {{source_id: item.tgt_id}})
                    MERGE (a)-[r:{rel_type}]->(b)
                    SET r.properties = item.properties
                    """,
                    items=items,
                )

    def delete_node(self, node_id: str) -> None:
        node_type, source_id = parse_node_id(node_id)
        label = self._LABEL_MAP.get(node_type, "Node")
        with self._driver.session(database=self._database) as session:
            # DETACH DELETE 会同时删除所有关联边
            session.run(
                f"MATCH (n:{label} {{source_id: $source_id}}) DETACH DELETE n",
                source_id=source_id,
            )

    def delete_edges_by_node(self, node_id: str) -> None:
        node_type, source_id = parse_node_id(node_id)
        label = self._LABEL_MAP.get(node_type, "Node")
        with self._driver.session(database=self._database) as session:
            session.run(
                f"""
                MATCH (n:{label} {{source_id: $source_id}})-[r]-()
                DELETE r
                """,
                source_id=source_id,
            )

    # ------------------------------------------------------------------
    # 读操作
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        node_type, source_id = parse_node_id(node_id)
        label = self._LABEL_MAP.get(node_type, "Node")
        with self._driver.session(database=self._database) as session:
            result = session.run(
                f"MATCH (n:{label} {{source_id: $source_id}}) RETURN n",
                source_id=source_id,
            )
            record = result.single()
            if record:
                n = record["n"]
                props = self._parse_properties(n.get("properties", "{}"))
                return GraphNode(
                    id=node_id,
                    node_type=node_type,
                    label=n.get("label", ""),
                    source_id=source_id,
                    properties=props,
                )
        return None

    def find_neighbors(
        self,
        node_id: str,
        edge_types: list[str] | None = None,
        direction: str = "both",
        limit: int = 1000,
    ) -> list[tuple[GraphNode, GraphEdge]]:
        node_type, source_id = parse_node_id(node_id)
        label = self._LABEL_MAP.get(node_type, "Node")

        # 构建关系类型过滤
        rel_filter = ""
        if edge_types:
            neo4j_types = [self._EDGE_TYPE_MAP.get(t, t.upper()) for t in edge_types]
            rel_filter = ":" + "|:".join(neo4j_types)

        # 构建方向
        if direction == "out":
            pattern = f"(a:{label} {{source_id: $source_id}})-[r{rel_filter}]->(b)"
        elif direction == "in":
            pattern = f"(a:{label} {{source_id: $source_id}})<-[r{rel_filter}]-(b)"
        else:  # both
            pattern = f"(a:{label} {{source_id: $source_id}})-[r{rel_filter}]-(b)"

        query = f"""
        MATCH {pattern}
        RETURN b.source_id AS src_id,
               labels(b)[0] AS label,
               b.label AS display_label,
               b.node_type AS node_type,
               b.properties AS properties,
               type(r) AS rel_type,
               startNode(r) = a AS is_outgoing
        LIMIT $limit
        """

        results: list[tuple[GraphNode, GraphEdge]] = []
        with self._driver.session(database=self._database) as session:
            for record in session.run(query, source_id=source_id, limit=limit):
                tgt_type = self._reverse_label(record["label"]) or record.get("node_type", "page")
                tgt_id = record["src_id"]
                neighbor_nid = make_node_id(tgt_type, tgt_id)
                props = self._parse_properties(record.get("properties", "{}"))

                neighbor = GraphNode(
                    id=neighbor_nid,
                    node_type=tgt_type,
                    label=record.get("display_label", ""),
                    source_id=tgt_id,
                    properties=props,
                )

                neo4j_rel = record["rel_type"]
                edge_type = self._EDGE_TYPE_REVERSE.get(neo4j_rel, neo4j_rel.lower())
                if record["is_outgoing"]:
                    edge = GraphEdge(source=node_id, target=neighbor_nid, edge_type=edge_type)
                else:
                    edge = GraphEdge(source=neighbor_nid, target=node_id, edge_type=edge_type)

                results.append((neighbor, edge))

        return results

    def traverse(
        self,
        start_ids: list[str],
        max_depth: int = 2,
        edge_types: list[str] | None = None,
        max_nodes: int = 200,
        node_filter_ids: set[str] | None = None,
    ) -> TraversalResult:
        """BFS 遍历 — 利用 Neo4j 的可变长度路径"""
        # 构建起始节点 ID
        start_nids = []
        for sid in start_ids:
            nid = make_node_id("page", sid) if ":" not in sid else sid
            start_nids.append(nid)

        # 使用 Cypher 可变长度路径进行高效遍历
        rel_filter = ""
        if edge_types:
            neo4j_types = [self._EDGE_TYPE_MAP.get(t, t.upper()) for t in edge_types]
            rel_filter = ":" + "|:".join(neo4j_types)

        # 收集所有起始节点的 source_id
        start_source_ids = []
        start_labels = []
        for nid in start_nids:
            ntype, nsid = parse_node_id(nid)
            start_source_ids.append(nsid)
            start_labels.append(self._LABEL_MAP.get(ntype, "Node"))

        # 对每种起始 Label 执行遍历
        all_nodes: dict[str, dict] = {}
        all_edges: list[dict] = []
        all_paths: list[list[str]] = []

        with self._driver.session(database=self._database) as session:
            for label, src_id in zip(start_labels, start_source_ids):
                query = f"""
                MATCH path = (start:{label} {{source_id: $src_id}})-[r{rel_filter}*1..{max_depth}]-(end)
                WITH start, end, path, length(path) AS depth
                LIMIT $max_nodes
                RETURN
                    end.source_id AS end_src_id,
                    labels(end)[0] AS end_label,
                    end.label AS end_display_label,
                    end.node_type AS end_node_type,
                    end.properties AS end_properties,
                    start.source_id AS start_src_id,
                    labels(start)[0] AS start_label,
                    depth,
                    [rel IN relationships(path) | type(rel)] AS rel_types,
                    [node IN nodes(path) | node.source_id] AS path_ids,
                    [node IN nodes(path) | labels(node)[0]] AS path_labels
                """
                try:
                    result = session.run(
                        query, src_id=src_id, max_nodes=max_nodes,
                    )
                except Exception as e:
                    logger.warning("Neo4j traverse query failed: %s, falling back to BFS", e)
                    return self._traverse_bfs_fallback(
                        start_nids, max_depth, edge_types, max_nodes, node_filter_ids,
                    )

                for record in result:
                    end_type = self._reverse_label(record["end_label"]) or record.get("end_node_type", "page")
                    end_nid = make_node_id(end_type, record["end_src_id"])

                    if node_filter_ids is not None and end_nid not in node_filter_ids:
                        continue

                    if len(all_nodes) >= max_nodes:
                        break

                    # 添加终点节点
                    props = self._parse_properties(record.get("end_properties", "{}"))
                    all_nodes[end_nid] = {
                        "id": record["end_src_id"],
                        "type": end_type,
                        "label": record.get("end_display_label", ""),
                        "properties": props,
                    }
                    if end_type == "block":
                        all_nodes[end_nid]["block_id"] = record["end_src_id"]
                        all_nodes[end_nid]["properties"]["block_id"] = record["end_src_id"]

                    # 添加起始节点（确保存在）
                    start_type = self._reverse_label(record["start_label"]) or "page"
                    start_nid = make_node_id(start_type, record["start_src_id"])
                    if start_nid not in all_nodes:
                        start_node = self.get_node(start_nid)
                        if start_node:
                            all_nodes[start_nid] = self._node_to_traversal_dict(start_node)

                    record["depth"]
                    rel_types = record["rel_types"] or []
                    path_ids = record["path_ids"] or []
                    path_labels = record["path_labels"] or []

                    # 构建边
                    for i in range(len(path_ids) - 1):
                        p_src_type = self._reverse_label(path_labels[i]) or "page"
                        p_tgt_type = self._reverse_label(path_labels[i + 1]) or "page"
                        p_src = make_node_id(p_src_type, path_ids[i])
                        p_tgt = make_node_id(p_tgt_type, path_ids[i + 1])
                        rel_t = rel_types[i] if i < len(rel_types) else "LINK"
                        edge_type = self._EDGE_TYPE_REVERSE.get(rel_t, rel_t.lower())

                        all_edges.append({
                            "source": p_src,
                            "target": p_tgt,
                            "type": edge_type,
                            "depth": i + 1,
                        })

                    # 构建路径
                    full_path = [
                        make_node_id(
                            self._reverse_label(path_labels[j]) or "page",
                            path_ids[j],
                        )
                        for j in range(len(path_ids))
                    ]
                    all_paths.append(full_path)

        return TraversalResult(
            nodes=list(all_nodes.values()),
            edges=all_edges,
            paths=all_paths,
            truncated=len(all_nodes) >= max_nodes,
        )

    def load_subgraph(
        self,
        node_types: list[str] | None = None,
        edge_types: list[str] | None = None,
        node_limit: int = 500,
        edge_limit: int = 10000,
        block_limit: int | None = None,
    ) -> SubgraphResult:
        """加载子图 — 使用 Cypher 高效查询"""
        nodes: list[dict] = []
        edges: list[dict] = []
        node_ids: set[str] = set()

        with self._driver.session(database=self._database) as session:
            # 1. 加载节点
            labels_to_query = []
            if node_types is None:
                labels_to_query = list(self._LABEL_MAP.values())
            else:
                for nt in node_types:
                    if nt in self._LABEL_MAP:
                        labels_to_query.append(self._LABEL_MAP[nt])

            for label in labels_to_query:
                # Block 节点使用 block_limit（如果指定），其他使用 node_limit
                effective_limit = node_limit
                if label == "Block" and block_limit is not None:
                    effective_limit = block_limit
                result = session.run(
                    f"MATCH (n:{label}) RETURN n.source_id AS src_id, n.label AS label, "
                    f"n.node_type AS node_type, n.properties AS properties LIMIT $limit",
                    limit=effective_limit,
                )
                for record in result:
                    node_type = self._reverse_label(label) or record.get("node_type", "page")
                    nid = make_node_id(node_type, record["src_id"])
                    if nid not in node_ids:
                        node_ids.add(nid)
                        props = self._parse_properties(record.get("properties", "{}"))
                        nodes.append({
                            "id": nid,
                            "type": node_type,
                            "label": record.get("label", ""),
                            "source_id": record["src_id"],
                            "properties": props,
                        })

            # 2. 加载边
            rel_filter = ""
            if edge_types:
                neo4j_types = [self._EDGE_TYPE_MAP.get(t, t.upper()) for t in edge_types]
                rel_filter = ":" + "|:".join(neo4j_types)

            result = session.run(
                f"""
                MATCH (a)-[r{rel_filter}]->(b)
                RETURN a.source_id AS src_id, labels(a)[0] AS src_label,
                       b.source_id AS tgt_id, labels(b)[0] AS tgt_label,
                       type(r) AS rel_type
                LIMIT $limit
                """,
                limit=edge_limit,
            )
            edge_count = 0
            for record in result:
                src_type = self._reverse_label(record["src_label"]) or "page"
                tgt_type = self._reverse_label(record["tgt_label"]) or "page"
                src_nid = make_node_id(src_type, record["src_id"])
                tgt_nid = make_node_id(tgt_type, record["tgt_id"])
                rel_type = record["rel_type"]
                edge_type = self._EDGE_TYPE_REVERSE.get(rel_type, rel_type.lower())
                edges.append({
                    "source": src_nid,
                    "target": tgt_nid,
                    "type": edge_type,
                })
                edge_count += 1

        return SubgraphResult(
            nodes=nodes,
            edges=edges,
            truncated=edge_count >= edge_limit,
        )

    def get_nodes_by_type(
        self,
        node_type: str,
        limit: int = 500,
        offset: int = 0,
    ) -> list[GraphNode]:
        label = self._LABEL_MAP.get(node_type, "Node")
        with self._driver.session(database=self._database) as session:
            result = session.run(
                f"MATCH (n:{label}) RETURN n.source_id AS src_id, n.label AS label, "
                f"n.properties AS properties SKIP $offset LIMIT $limit",
                offset=offset, limit=limit,
            )
            return [
                GraphNode(
                    id=make_node_id(node_type, r["src_id"]),
                    node_type=node_type,
                    label=r.get("label", ""),
                    source_id=r["src_id"],
                    properties=self._parse_properties(r.get("properties", "{}")),
                )
                for r in result
            ]

    def get_edges_by_type(
        self,
        edge_type: str,
        limit: int = 10000,
        offset: int = 0,
    ) -> list[GraphEdge]:
        rel_type = self._EDGE_TYPE_MAP.get(edge_type, edge_type.upper())
        with self._driver.session(database=self._database) as session:
            result = session.run(
                f"""
                MATCH (a)-[r:{rel_type}]->(b)
                RETURN a.source_id AS src_id, labels(a)[0] AS src_label,
                       b.source_id AS tgt_id, labels(b)[0] AS tgt_label
                SKIP $offset LIMIT $limit
                """,
                offset=offset, limit=limit,
            )
            return [
                GraphEdge(
                    source=make_node_id(self._reverse_label(r["src_label"]) or "page", r["src_id"]),
                    target=make_node_id(self._reverse_label(r["tgt_label"]) or "page", r["tgt_id"]),
                    edge_type=edge_type,
                )
                for r in result
            ]

    # ------------------------------------------------------------------
    # 管理操作
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """清空所有图数据"""
        with self._driver.session(database=self._database) as session:
            # 分批删除避免内存溢出
            session.run(
                """
                CALL { MATCH (n) WITH n LIMIT 10000 DETACH DELETE n }
                IN TRANSACTIONS
                """
            )
        logger.info("Neo4j graph cleared")

    def health_check(self) -> bool:
        try:
            with self._driver.session(database=self._database) as session:
                session.run("RETURN 1")
            return True
        except Exception as e:
            logger.warning("Neo4j health check failed: %s", e)
            return False

    def close(self) -> None:
        try:
            self._driver.close()
            logger.info("Neo4j backend closed")
        except Exception:
            pass

    def stats(self) -> dict:
        with self._driver.session(database=self._database) as session:
            page_count = session.run("MATCH (n:Page) RETURN count(n) AS c").single()["c"]
            block_count = session.run("MATCH (n:Block) RETURN count(n) AS c").single()["c"]
            tag_count = session.run("MATCH (n:Tag) RETURN count(n) AS c").single()["c"]
            edge_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        return {
            "backend": "neo4j",
            "node_count": page_count + block_count + tag_count,
            "edge_count": edge_count,
            "page_count": page_count,
            "block_count": block_count,
            "tag_count": tag_count,
        }

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _reverse_label(self, neo4j_label: str) -> Optional[str]:
        """Neo4j Label → 内部 node_type"""
        for k, v in self._LABEL_MAP.items():
            if v == neo4j_label:
                return k
        return None

    @staticmethod
    def _parse_properties(raw: str | dict) -> dict:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    @staticmethod
    def _node_to_traversal_dict(node: GraphNode) -> dict:
        properties = dict(node.properties)
        result = {
            "id": node.source_id,
            "type": node.node_type,
            "label": node.label,
            "properties": properties,
        }
        if node.node_type == "block":
            result["block_id"] = node.source_id
            properties["block_id"] = node.source_id
        return result

    def _traverse_bfs_fallback(
        self,
        start_nids: list[str],
        max_depth: int,
        edge_types: list[str] | None,
        max_nodes: int,
        node_filter_ids: set[str] | None,
    ) -> TraversalResult:
        """当 Cypher 可变长度路径查询失败时的 BFS 回退方案"""
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        paths: list[list[str]] = []
        visited: set[str] = set()
        queue: deque[tuple[str, int, list[str]]] = deque()

        for nid in start_nids:
            queue.append((nid, 0, [nid]))

        while queue:
            current_id, depth, path = queue.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)
            if len(nodes) >= max_nodes:
                break
            if node_filter_ids is not None and current_id not in node_filter_ids and depth > 0:
                continue

            node = self.get_node(current_id)
            if node:
                nodes[current_id] = self._node_to_traversal_dict(node)

            if depth > 0:
                edges.append({
                    "source": path[-2], "target": current_id,
                    "type": "link", "depth": depth,
                })
                paths.append(path)

            if depth >= max_depth:
                continue

            neighbors = self.find_neighbors(current_id, edge_types=edge_types)
            for neighbor, _edge in neighbors:
                if neighbor.id not in visited:
                    queue.append((neighbor.id, depth + 1, path + [neighbor.id]))

        return TraversalResult(
            nodes=list(nodes.values()),
            edges=edges,
            paths=paths,
            truncated=len(nodes) >= max_nodes,
        )

    # ------------------------------------------------------------------
    # 索引管理（迁移后调用）
    # ------------------------------------------------------------------

    def create_indexes(self) -> None:
        """创建 Neo4j 索引以加速查询"""
        with self._driver.session(database=self._database) as session:
            for label in self._LABEL_MAP.values():
                try:
                    session.run(
                        f"CREATE INDEX IF NOT EXISTS FOR (n:{label}) ON (n.source_id)"
                    )
                except Exception as e:
                    logger.warning("Failed to create index for %s: %s", label, e)
        logger.info("Neo4j indexes created")
