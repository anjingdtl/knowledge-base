"""SQLite 图后端 — 从现有关系表读取图数据（默认后端）

此实现不引入额外的图表，而是直接从 knowledge_items、blocks、entity_refs、
tag_relations 等现有表中动态构建图视图。行为与改造前的 UnifiedGraphService /
GraphTraversalService 完全一致，确保向后兼容。
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


class SQLiteGraphBackend(GraphBackend):
    """基于 SQLite 的图后端，直接读取现有关系表"""

    def __init__(self, db):
        self._db = db

    @property
    def name(self) -> str:
        return "sqlite"

    # ------------------------------------------------------------------
    # 写操作 — SQLite 后端的写操作委托给现有 DB 方法
    # ------------------------------------------------------------------

    def upsert_node(self, node: GraphNode) -> None:
        """SQLite 后端不独立维护节点表 — 节点数据由 FileGraphService / 业务层管理"""
        # 对于 SQLite 后端，节点是 knowledge_items/blocks 的视图，
        # 不需要独立的 upsert。此处仅做日志记录。
        logger.debug("SQLiteGraphBackend.upsert_node skipped (view-only): %s", node.id)

    def upsert_edge(self, edge: GraphEdge) -> None:
        """将边写入 entity_refs 表"""
        src_type, src_id = parse_node_id(edge.source)
        tgt_type, tgt_id = parse_node_id(edge.target)
        conn = self._db.get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO entity_refs
               (id, source_type, source_id, target_type, target_id, ref_type, weight)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                f"{src_id}->{tgt_id}:{edge.edge_type}",
                self._to_db_type(src_type), src_id,
                self._to_db_type(tgt_type), tgt_id,
                edge.edge_type,
                edge.properties.get("weight", 1.0),
            ),
        )
        conn.commit()

    def upsert_nodes_batch(self, nodes: list[GraphNode]) -> None:
        """批量 upsert — SQLite 后端为视图，无需操作"""
        pass

    def upsert_edges_batch(self, edges: list[GraphEdge]) -> None:
        for edge in edges:
            self.upsert_edge(edge)

    def delete_node(self, node_id: str) -> None:
        node_type, source_id = parse_node_id(node_id)
        if node_type == "page":
            self._db.delete_knowledge(source_id)
        elif node_type == "block":
            conn = self._db.get_conn()
            conn.execute("DELETE FROM blocks WHERE id = ?", (source_id,))
            conn.execute(
                "DELETE FROM entity_refs WHERE (source_type = 'block' AND source_id = ?) "
                "OR (target_type = 'block' AND target_id = ?)",
                (source_id, source_id),
            )
            conn.commit()

    def delete_edges_by_node(self, node_id: str) -> None:
        node_type, source_id = parse_node_id(node_id)
        db_type = self._to_db_type(node_type)
        conn = self._db.get_conn()
        conn.execute(
            "DELETE FROM entity_refs WHERE "
            "(source_type = ? AND source_id = ?) OR (target_type = ? AND target_id = ?)",
            (db_type, source_id, db_type, source_id),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # 读操作
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        node_type, source_id = parse_node_id(node_id)
        if node_type == "page":
            row = self._db.get_conn().execute(
                "SELECT id, title, file_type, tags, source_type FROM knowledge_items WHERE id = ?",
                (source_id,),
            ).fetchone()
            if row:
                return GraphNode(
                    id=node_id,
                    node_type="page",
                    label=row["title"] or "",
                    source_id=source_id,
                    properties={
                        "file_type": row["file_type"] or "",
                        "source_type": row["source_type"] or "",
                        "tags": self._load_json_list(row["tags"] if "tags" in row.keys() else "[]"),
                    },
                )
        elif node_type == "block":
            row = self._db.get_conn().execute(
                "SELECT id, content, page_id, block_type, properties, order_idx FROM blocks WHERE id = ?",
                (source_id,),
            ).fetchone()
            if row:
                return GraphNode(
                    id=node_id,
                    node_type="block",
                    label=(row["content"] or "")[:80],
                    source_id=source_id,
                    properties={
                        "block_type": row["block_type"] or "text",
                        "page_id": row["page_id"] or "",
                        "order_idx": row["order_idx"] or 0,
                    },
                )
        elif node_type == "tag":
            # 标签没有独立表，通过 knowledge_items.tags 查找
            conn = self._db.get_conn()
            row = conn.execute(
                "SELECT id FROM knowledge_items WHERE tags LIKE ? LIMIT 1",
                (f"%{source_id}%",),
            ).fetchone()
            if row:
                return GraphNode(
                    id=node_id, node_type="tag",
                    label=source_id, source_id=source_id,
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
        conn = self._db.get_conn()
        results: list[tuple[GraphNode, GraphEdge]] = []

        rt_clause, rt_params = self._build_ref_type_clause(edge_types)

        # 出边
        if direction in ("out", "both"):
            if node_type in ("page", "knowledge"):
                if self._edge_type_allowed("contains", edge_types):
                    block_rows = conn.execute(
                        """SELECT id, content, page_id, block_type, properties, order_idx
                           FROM blocks
                           WHERE page_id = ?
                           ORDER BY order_idx ASC
                           LIMIT ?""",
                        (source_id, limit),
                    ).fetchall()
                    for row in block_rows:
                        block_id = make_node_id("block", row["id"])
                        edge = GraphEdge(source=node_id, target=block_id, edge_type="contains")
                        neighbor = self.get_node(block_id)
                        if neighbor:
                            results.append((neighbor, edge))

                # page 的出边：通过其 blocks 的 entity_refs
                rows = conn.execute(
                    f"""SELECT er.target_id, er.target_type, er.ref_type
                        FROM entity_refs er
                        WHERE er.source_id IN (
                            SELECT id FROM blocks WHERE page_id = ?
                        ) AND er.source_type = 'block' {rt_clause}
                        UNION
                        SELECT er.target_id, er.target_type, er.ref_type
                        FROM entity_refs er
                        WHERE er.source_id = ? AND er.source_type = 'knowledge' {rt_clause}
                        LIMIT ?""",
                    [source_id] + rt_params + [source_id] + rt_params + [limit],
                ).fetchall()
                for row in rows:
                    tgt_id = make_node_id(row["target_type"], row["target_id"])
                    edge = GraphEdge(source=node_id, target=tgt_id, edge_type=row["ref_type"] or "link")
                    neighbor = self.get_node(tgt_id)
                    if neighbor:
                        results.append((neighbor, edge))

                # 补充：从 knowledge_graph_relations 查询 LLM 发现的语义关系
                try:
                    kg_rows = conn.execute(
                        """SELECT kgr.target_knowledge_id, kgr.relation_type, kgr.description
                           FROM knowledge_graph_relations kgr
                           WHERE kgr.source_knowledge_id = ?
                           LIMIT ?""",
                        (source_id, limit),
                    ).fetchall()
                    for row in kg_rows:
                        tgt_id = make_node_id("page", row["target_knowledge_id"])
                        edge = GraphEdge(
                            source=node_id, target=tgt_id,
                            edge_type=row["relation_type"] or "related",
                            properties={"description": row["description"] or ""},
                        )
                        neighbor = self.get_node(tgt_id)
                        if neighbor:
                            results.append((neighbor, edge))
                except Exception:
                    pass  # knowledge_graph_relations 表可能不存在

            elif node_type == "block":
                if self._edge_type_allowed("parent", edge_types):
                    child_rows = conn.execute(
                        """SELECT id
                           FROM blocks
                           WHERE parent_id = ?
                           ORDER BY order_idx ASC
                           LIMIT ?""",
                        (source_id, limit),
                    ).fetchall()
                    for row in child_rows:
                        child_id = make_node_id("block", row["id"])
                        edge = GraphEdge(source=node_id, target=child_id, edge_type="parent")
                        neighbor = self.get_node(child_id)
                        if neighbor:
                            results.append((neighbor, edge))

                rows = conn.execute(
                    f"""SELECT er.target_id, er.target_type, er.ref_type
                        FROM entity_refs er
                        WHERE er.source_id = ? AND er.source_type = 'block' {rt_clause}
                        LIMIT ?""",
                    [source_id] + rt_params + [limit],
                ).fetchall()
                for row in rows:
                    tgt_id = make_node_id(row["target_type"], row["target_id"])
                    edge = GraphEdge(source=node_id, target=tgt_id, edge_type=row["ref_type"] or "link")
                    neighbor = self.get_node(tgt_id)
                    if neighbor:
                        results.append((neighbor, edge))

        # 入边
        if direction in ("in", "both"):
            if node_type == "block":
                block_row = conn.execute(
                    "SELECT parent_id, page_id FROM blocks WHERE id = ?",
                    (source_id,),
                ).fetchone()
                if block_row:
                    if block_row["page_id"] and self._edge_type_allowed("contains", edge_types):
                        page_id = make_node_id("page", block_row["page_id"])
                        edge = GraphEdge(source=page_id, target=node_id, edge_type="contains")
                        neighbor = self.get_node(page_id)
                        if neighbor:
                            results.append((neighbor, edge))
                    if block_row["parent_id"] and self._edge_type_allowed("parent", edge_types):
                        parent_id = make_node_id("block", block_row["parent_id"])
                        edge = GraphEdge(source=parent_id, target=node_id, edge_type="parent")
                        neighbor = self.get_node(parent_id)
                        if neighbor:
                            results.append((neighbor, edge))

            db_type = self._to_db_type(node_type)
            rows = conn.execute(
                f"""SELECT er.source_id, er.source_type, er.ref_type
                    FROM entity_refs er
                    WHERE er.target_id = ? AND er.target_type = ? {rt_clause}
                    LIMIT ?""",
                [source_id, db_type] + rt_params + [limit],
            ).fetchall()
            for row in rows:
                src_id = make_node_id(row["source_type"], row["source_id"])
                edge = GraphEdge(source=src_id, target=node_id, edge_type=row["ref_type"] or "link")
                neighbor = self.get_node(src_id)
                if neighbor:
                    results.append((neighbor, edge))

            # 补充：从 knowledge_graph_relations 查询反向入边
            if node_type in ("page", "knowledge"):
                try:
                    kg_in_rows = conn.execute(
                        """SELECT kgr.source_knowledge_id, kgr.relation_type, kgr.description
                           FROM knowledge_graph_relations kgr
                           WHERE kgr.target_knowledge_id = ?
                           LIMIT ?""",
                        (source_id, limit),
                    ).fetchall()
                    for row in kg_in_rows:
                        src_nid = make_node_id("page", row["source_knowledge_id"])
                        edge = GraphEdge(
                            source=src_nid, target=node_id,
                            edge_type=row["relation_type"] or "related",
                            properties={"description": row["description"] or ""},
                        )
                        neighbor = self.get_node(src_nid)
                        if neighbor:
                            results.append((neighbor, edge))
                except Exception:
                    pass  # knowledge_graph_relations 表可能不存在

        return results

    def traverse(
        self,
        start_ids: list[str],
        max_depth: int = 2,
        edge_types: list[str] | None = None,
        max_nodes: int = 200,
        node_filter_ids: set[str] | None = None,
    ) -> TraversalResult:
        """BFS 遍历 — 与改造前 GraphTraversalService.traverse 逻辑一致"""
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        paths: list[list[str]] = []
        visited: set[str] = set()
        # 队列元素: (node_id, depth, path, incoming_edge_type)
        queue: deque[tuple[str, int, list[str], str | None]] = deque()

        for sid in start_ids:
            nid = make_node_id("page", sid) if ":" not in sid else sid
            queue.append((nid, 0, [nid], None))

        while queue:
            current_id, depth, path, incoming_edge_type = queue.popleft()
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
                    "source": path[-2],
                    "target": current_id,
                    "type": incoming_edge_type or "link",
                    "depth": depth,
                })
                paths.append(path)

            if depth >= max_depth:
                continue

            neighbors = self.find_neighbors(current_id, edge_types=edge_types)
            for neighbor, _edge in neighbors:
                if neighbor.id not in visited:
                    queue.append((neighbor.id, depth + 1, path + [neighbor.id], _edge.edge_type if _edge else None))

        return TraversalResult(
            nodes=list(nodes.values()),
            edges=edges,
            paths=paths,
            truncated=len(nodes) >= max_nodes,
        )

    def load_subgraph(
        self,
        node_types: list[str] | None = None,
        edge_types: list[str] | None = None,
        node_limit: int = 500,
        edge_limit: int = 10000,
        block_limit: int | None = None,
    ) -> SubgraphResult:
        """加载子图 — 对应改造前 UnifiedGraphService.build"""
        nodes: list[dict] = []
        edges: list[dict] = []
        node_ids: set[str] = set()

        def add_node(n: GraphNode):
            if n.id not in node_ids:
                node_ids.add(n.id)
                nodes.append(n.to_dict())

        def add_edge(e: GraphEdge):
            edges.append(e.to_dict())

        # 1. 加载页面节点
        if node_types is None or "page" in node_types:
            pages = self._db.list_knowledge(limit=node_limit)
            for page in pages:
                page_nid = f"page:{page['id']}"
                add_node(GraphNode(
                    id=page_nid, node_type="page",
                    label=page.get("title", ""),
                    source_id=page["id"],
                    properties={
                        "file_type": page.get("file_type", ""),
                        "source_type": page.get("source_type", ""),
                    },
                ))

                # 标签节点和边
                if node_types is None or "tag" in node_types:
                    tags = self._load_json_list(page.get("tags", "[]"))
                    for tag in tags:
                        tag_nid = f"tag:{tag}"
                        add_node(GraphNode(id=tag_nid, node_type="tag", label=tag, source_id=tag))
                        add_edge(GraphEdge(source=page_nid, target=tag_nid, edge_type="tagged_with"))

        # 2. 加载 blocks
        if node_types is None or "block" in node_types:
            conn = self._db.get_conn()
            page_ids = [
                n["source_id"] for n in nodes
                if isinstance(n, dict) and n.get("type") == "page"
            ]
            if not page_ids:
                # 回退：从 knowledge_items 取
                page_ids = [p["id"] for p in self._db.list_knowledge(limit=node_limit)]

            if page_ids:
                placeholders = ",".join("?" for _ in page_ids)
                params: list = list(page_ids)
                limit_clause = ""
                effective_block_limit = block_limit if block_limit is not None else (node_limit * 2 if node_limit else None)
                if effective_block_limit is not None and effective_block_limit >= 0:
                    limit_clause = " LIMIT ?"
                    params.append(effective_block_limit)

                block_rows = conn.execute(
                    f"""SELECT id, parent_id, page_id, content, block_type, properties, order_idx
                        FROM blocks
                        WHERE page_id IN ({placeholders})
                        ORDER BY page_id ASC, order_idx ASC{limit_clause}""",
                    params,
                ).fetchall()

                for row in block_rows:
                    block_nid = f"block:{row['id']}"
                    add_node(GraphNode(
                        id=block_nid, node_type="block",
                        label=(row["content"] or "")[:80],
                        source_id=row["id"],
                        properties={
                            "block_type": row["block_type"] or "text",
                            "page_id": row["page_id"] or "",
                            "order_idx": row["order_idx"] or 0,
                        },
                    ))
                    if row["page_id"]:
                        add_edge(GraphEdge(
                            source=f"page:{row['page_id']}",
                            target=block_nid,
                            edge_type="contains",
                        ))
                    if row["parent_id"]:
                        add_edge(GraphEdge(
                            source=f"block:{row['parent_id']}",
                            target=block_nid,
                            edge_type="parent",
                        ))

        # 3. 加载 entity_refs
        conn = self._db.get_conn()
        ref_sql = "SELECT source_type, source_id, target_type, target_id, ref_type FROM entity_refs"
        ref_params: list = []
        if edge_types:
            rt_placeholders = ",".join("?" for _ in edge_types)
            ref_sql += f" WHERE ref_type IN ({rt_placeholders})"
            ref_params.extend(edge_types)
        if edge_limit is not None and edge_limit >= 0:
            ref_sql += " LIMIT ?"
            ref_params.append(edge_limit)

        ref_rows = conn.execute(ref_sql, ref_params).fetchall()
        ref_truncated = bool(edge_limit is not None and len(ref_rows) >= edge_limit)

        for row in ref_rows:
            src = make_node_id(row["source_type"], row["source_id"])
            tgt = make_node_id(row["target_type"], row["target_id"])
            if src and tgt:
                add_edge(GraphEdge(source=src, target=tgt, edge_type=row["ref_type"] or "mention"))

        # 4. 加载 tag_relations（标签 DAG）
        if node_types is None or "tag" in node_types:
            try:
                tag_sql = "SELECT parent_tag, child_tag FROM tag_relations"
                tag_params: list = []
                if edge_limit is not None:
                    tag_sql += " LIMIT ?"
                    tag_params.append(edge_limit)
                tag_rows = conn.execute(tag_sql, tag_params).fetchall()
                for row in tag_rows:
                    add_edge(GraphEdge(
                        source=f"tag:{row['parent_tag']}",
                        target=f"tag:{row['child_tag']}",
                        edge_type="tag_parent",
                    ))
            except Exception:
                pass

        # 5. 加载 knowledge_graph_relations（LLM 发现的语义关系）
        try:
            kg_sql = "SELECT source_knowledge_id, target_knowledge_id, relation_type, description FROM knowledge_graph_relations"
            kg_params: list = []
            if edge_limit is not None and edge_limit >= 0:
                kg_sql += " LIMIT ?"
                kg_params.append(edge_limit)
            kg_rows = conn.execute(kg_sql, kg_params).fetchall()
            for row in kg_rows:
                src = f"page:{row['source_knowledge_id']}"
                tgt = f"page:{row['target_knowledge_id']}"
                add_edge(GraphEdge(
                    source=src, target=tgt,
                    edge_type=row["relation_type"] or "related",
                    properties={"description": row["description"] or ""},
                ))
                # 确保两端节点存在
                for nid in (src, tgt):
                    _, kid = parse_node_id(nid)
                    if nid not in node_ids:
                        node = self.get_node(nid)
                        if node:
                            add_node(node)
        except Exception:
            pass  # knowledge_graph_relations 表可能不存在

        return SubgraphResult(
            nodes=nodes,
            edges=edges,
            truncated=ref_truncated,
        )

    def get_nodes_by_type(
        self,
        node_type: str,
        limit: int = 500,
        offset: int = 0,
    ) -> list[GraphNode]:
        conn = self._db.get_conn()
        if node_type == "page":
            rows = conn.execute(
                "SELECT id, title, file_type, tags, source_type FROM knowledge_items "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [
                GraphNode(
                    id=f"page:{r['id']}", node_type="page",
                    label=r["title"] or "", source_id=r["id"],
                    properties={
                        "file_type": r["file_type"] or "",
                        "source_type": r["source_type"] or "",
                    },
                )
                for r in rows
            ]
        elif node_type == "block":
            rows = conn.execute(
                "SELECT id, content, page_id, block_type, order_idx FROM blocks "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [
                GraphNode(
                    id=f"block:{r['id']}", node_type="block",
                    label=(r["content"] or "")[:80], source_id=r["id"],
                    properties={
                        "block_type": r["block_type"] or "text",
                        "page_id": r["page_id"] or "",
                    },
                )
                for r in rows
            ]
        return []

    def get_edges_by_type(
        self,
        edge_type: str,
        limit: int = 10000,
        offset: int = 0,
    ) -> list[GraphEdge]:
        conn = self._db.get_conn()
        rows = conn.execute(
            "SELECT source_type, source_id, target_type, target_id, ref_type "
            "FROM entity_refs WHERE ref_type = ? LIMIT ? OFFSET ?",
            (edge_type, limit, offset),
        ).fetchall()
        return [
            GraphEdge(
                source=make_node_id(r["source_type"], r["source_id"]),
                target=make_node_id(r["target_type"], r["target_id"]),
                edge_type=r["ref_type"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # 管理操作
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        try:
            self._db.get_conn().execute("SELECT 1")
            return True
        except Exception:
            return False

    def stats(self) -> dict:
        conn = self._db.get_conn()
        page_count = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        block_count = conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        ref_count = conn.execute("SELECT COUNT(*) FROM entity_refs").fetchone()[0]
        # 补充 knowledge_graph_relations 统计
        kg_rel_count = 0
        try:
            kg_rel_count = conn.execute("SELECT COUNT(*) FROM knowledge_graph_relations").fetchone()[0]
        except Exception:
            pass
        return {
            "backend": "sqlite",
            "node_count": page_count + block_count,
            "edge_count": ref_count + kg_rel_count,
            "page_count": page_count,
            "block_count": block_count,
            "entity_ref_count": ref_count,
            "kg_relation_count": kg_rel_count,
        }

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _build_ref_type_clause(edge_types: list[str] | None) -> tuple[str, list]:
        if edge_types:
            placeholders = ",".join("?" for _ in edge_types)
            return f"AND ref_type IN ({placeholders})", list(edge_types)
        return "", []

    @staticmethod
    def _edge_type_allowed(edge_type: str, edge_types: list[str] | None) -> bool:
        return not edge_types or edge_type in edge_types

    @staticmethod
    def _to_db_type(node_type: str) -> str:
        """将图后端的 node_type 映射为 entity_refs 中的 source_type/target_type"""
        if node_type == "page":
            return "knowledge"
        return node_type

    @staticmethod
    def _load_json_list(value) -> list[str]:
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

    @staticmethod
    def _node_to_traversal_dict(node: GraphNode) -> dict:
        """将 GraphNode 转换为 GraphTraversalService 风格的 dict"""
        properties = dict(node.properties)
        result = {
            "id": node.source_id,
            "type": node.node_type,
            "label": node.label,
            "properties": properties,
        }
        if node.node_type == "page":
            properties["file_type"] = node.properties.get("file_type", "")
        elif node.node_type == "block":
            result["block_id"] = node.source_id
            properties["block_id"] = node.source_id
            properties["page_id"] = node.properties.get("page_id", "")
        return result
