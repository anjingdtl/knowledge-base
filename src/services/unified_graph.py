"""统一图谱服务 — 从现有存储构建 page/block/tag/entity 统一图结构"""
import json
import logging
from typing import Optional

from src.models.unified_node import UnifiedNode, UnifiedEdge
from src.services.db import Database

logger = logging.getLogger(__name__)


class UnifiedGraphService:
    """从 knowledge_items、blocks、entity_refs、tag_relations 构建统一图负载"""

    def __init__(self, db=None):
        self._db = db or Database

    def build(self, include_blocks: bool = True, include_tags: bool = True,
              page_limit: int = 500, block_limit: int | None = 1000,
              ref_limit: int | None = 10000) -> dict:
        """构建统一图谱，返回 {"nodes": [...], "edges": [...]}"""

        # 防止 entity_refs 在大量数据下加载所有行拖死内存。
        # 历史上 entity_refs 没有 LIMIT，单库数十万行时直接 OOM。
        if ref_limit is not None and ref_limit < 0:
            ref_limit = None
        nodes: list[dict] = []
        edges: list[dict] = []
        node_ids: set[str] = set()

        def _add_node(n: UnifiedNode):
            if n.id not in node_ids:
                node_ids.add(n.id)
                nodes.append(n.to_dict())

        def _add_edge(e: UnifiedEdge):
            edges.append(e.to_dict())

        # 1. 加载页面
        pages = self._db.list_knowledge(limit=page_limit)
        page_ids = [page["id"] for page in pages]

        # 2. 创建页面节点 + 标签节点/边
        tag_set: set[str] = set()
        for page in pages:
            page_id = page["id"]
            _add_node(UnifiedNode(
                id=f"page:{page_id}",
                node_type="page",
                label=page.get("title", ""),
                source_id=page_id,
                properties={
                    "file_type": page.get("file_type", ""),
                    "source_type": page.get("source_type", ""),
                },
            ))

            if include_tags:
                tags = self._load_json_list(page.get("tags", "[]"))
                for tag in tags:
                    tag_set.add(tag)
                    _add_node(UnifiedNode(
                        id=f"tag:{tag}",
                        node_type="tag",
                        label=tag,
                        source_id=tag,
                    ))
                    _add_edge(UnifiedEdge(
                        source=f"page:{page_id}",
                        target=f"tag:{tag}",
                        edge_type="tagged_with",
                    ))

        # 3. 加载 blocks
        if include_blocks and page_ids:
            conn = self._db.get_conn()
            placeholders = ",".join("?" for _ in page_ids)
            params: list = list(page_ids)
            limit_clause = ""
            if block_limit is not None and block_limit >= 0:
                limit_clause = " LIMIT ?"
                params.append(block_limit)
            block_rows = conn.execute(
                f"""SELECT id, parent_id, page_id, content, block_type, properties, order_idx
                    FROM blocks
                    WHERE page_id IN ({placeholders})
                    ORDER BY page_id ASC, order_idx ASC, created_at ASC{limit_clause}""",
                params,
            ).fetchall()

            for row in block_rows:
                block_id = row["id"]
                content = row["content"] or ""
                _add_node(UnifiedNode(
                    id=f"block:{block_id}",
                    node_type="block",
                    label=content[:80],
                    source_id=block_id,
                    properties={
                        "block_type": row["block_type"] or "text",
                        "page_id": row["page_id"] or "",
                        "order_idx": row["order_idx"] or 0,
                    },
                ))

                # page -> block "contains" 边
                page_id = row["page_id"]
                if page_id:
                    _add_edge(UnifiedEdge(
                        source=f"page:{page_id}",
                        target=f"block:{block_id}",
                        edge_type="contains",
                    ))

                # block -> parent block "parent" 边
                parent_id = row["parent_id"]
                if parent_id:
                    _add_edge(UnifiedEdge(
                        source=f"block:{parent_id}",
                        target=f"block:{block_id}",
                        edge_type="parent",
                    ))

        # 4. 加载 entity_refs 并创建边 — 限制最大行数避免 OOM
        conn = self._db.get_conn()
        ref_sql = (
            "SELECT source_type, source_id, target_type, target_id, ref_type "
            "FROM entity_refs"
        )
        ref_params: list = []
        if ref_limit is not None:
            ref_sql += " LIMIT ?"
            ref_params.append(ref_limit)
        ref_rows = conn.execute(ref_sql, ref_params).fetchall()
        ref_truncated = bool(ref_limit is not None and len(ref_rows) >= ref_limit)
        for row in ref_rows:
            src = self._node_id(row["source_type"], row["source_id"])
            tgt = self._node_id(row["target_type"], row["target_id"])
            if src and tgt:
                _add_edge(UnifiedEdge(
                    source=src,
                    target=tgt,
                    edge_type=row["ref_type"] or "mention",
                ))

        # 5. 加载 tag_relations（标签 DAG）— 同样加保护
        if include_tags:
            try:
                tag_sql = "SELECT parent_tag, child_tag FROM tag_relations"
                tag_params: list = []
                if ref_limit is not None:
                    tag_sql += " LIMIT ?"
                    tag_params.append(ref_limit)
                tag_rel_rows = conn.execute(tag_sql, tag_params).fetchall()
                for row in tag_rel_rows:
                    _add_edge(UnifiedEdge(
                        source=f"tag:{row['parent_tag']}",
                        target=f"tag:{row['child_tag']}",
                        edge_type="tag_parent",
                    ))
            except Exception:
                pass

        return {
            "nodes": nodes,
            "edges": edges,
            "ref_truncated": ref_truncated,
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
