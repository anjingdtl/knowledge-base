"""SQLite → 图数据库迁移工具

将现有 SQLite 关系表中的图谱数据全量同步到 Neo4j 或其他图后端。
支持全量迁移和增量同步两种模式。

用法:
    # 全量迁移（一次性）
    from src.services.graph_backend.migration import GraphMigration
    migration = GraphMigration(config, db)
    result = migration.migrate_all(target_backend)

    # 增量同步（定期执行）
    migration.sync_incremental(target_backend, since=last_sync_time)
"""
from __future__ import annotations

import json
import logging
import time

from src.services.graph_backend.base import (
    GraphBackend,
    GraphEdge,
    GraphNode,
    make_node_id,
)

logger = logging.getLogger(__name__)

# 每批写入的节点/边数量
DEFAULT_BATCH_SIZE = 500


class GraphMigration:
    """图谱数据迁移服务"""

    def __init__(self, config=None, db=None, progress_callback=None):
        from src.services.db import Database
        self._db = db or Database
        self._config = config
        self._progress = progress_callback

    def _emit_progress(self, message: str, current: int | None = None, total: int | None = None) -> None:
        if not self._progress:
            return
        try:
            if current is not None and total is not None:
                self._progress(message, current, total)
            else:
                self._progress(message)
        except TypeError:
            self._progress(message)

    # ------------------------------------------------------------------
    # 全量迁移
    # ------------------------------------------------------------------

    def migrate_all(
        self,
        target: GraphBackend,
        clear_target: bool = True,
        batch_size: int = DEFAULT_BATCH_SIZE,
        create_indexes: bool = True,
    ) -> dict:
        """将 SQLite 中的所有图谱数据迁移到目标后端

        Args:
            target: 目标图后端（如 Neo4jGraphBackend）
            clear_target: 是否先清空目标后端
            batch_size: 每批写入数量
            create_indexes: 迁移后是否创建索引（Neo4j）

        Returns:
            迁移统计 {"pages": N, "blocks": N, "tags": N, "edges": N, "duration_s": N}
        """
        start_time = time.time()
        stats: dict[str, int] = {
            "pages": 0, "blocks": 0, "tags": 0, "edges": 0,
        }

        if clear_target:
            self._emit_progress("正在清空目标图数据库...")
            target.clear()

        # 1. 迁移页面节点
        self._emit_progress("正在迁移页面节点...")
        stats["pages"] = self._migrate_pages(target, batch_size)

        # 2. 迁移 Block 节点
        self._emit_progress("正在迁移 Block 节点...")
        stats["blocks"] = self._migrate_blocks(target, batch_size)

        # 3. 迁移标签节点
        self._emit_progress("正在迁移标签节点...")
        stats["tags"] = self._migrate_tags(target, batch_size)

        # 4. 迁移边（entity_refs + contains + parent + tagged_with + tag_relations）
        self._emit_progress("正在迁移边...")
        stats["edges"] = self._migrate_edges(target, batch_size)

        # 5. 创建索引
        if create_indexes and hasattr(target, "create_indexes"):
            self._emit_progress("正在创建索引...")
            target.create_indexes()

        duration = time.time() - start_time
        stats["duration_s"] = round(duration, 2)

        self._emit_progress(
            f"迁移完成: {stats['pages']} 页面, {stats['blocks']} 块, "
            f"{stats['tags']} 标签, {stats['edges']} 边, "
            f"耗时 {duration:.1f}s",
        )
        logger.info("Graph migration completed: %s", stats)
        return stats

    # ------------------------------------------------------------------
    # 增量同步
    # ------------------------------------------------------------------

    def sync_incremental(
        self,
        target: GraphBackend,
        since: str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> dict:
        """增量同步：只同步 since 时间之后变更的数据

        Args:
            target: 目标图后端
            since: ISO 时间字符串，同步此时间之后的变更；None 表示全量
            batch_size: 每批写入数量

        Returns:
            同步统计
        """
        if since is None:
            return {"message": "since is None, use migrate_all instead"}

        stats: dict[str, int] = {"pages": 0, "blocks": 0, "edges": 0}
        conn = self._db.get_conn()

        # 1. 同步更新的页面
        rows = conn.execute(
            "SELECT id, title, file_type, tags, source_type, content "
            "FROM knowledge_items WHERE updated_at >= ?",
            (since,),
        ).fetchall()
        if rows:
            nodes = [self._page_to_node(r) for r in rows]
            target.upsert_nodes_batch(nodes)
            stats["pages"] = len(nodes)

            # 同步 tagged_with 边
            tag_edges = []
            for row in rows:
                tags = self._load_json_list(row["tags"] if "tags" in row.keys() else "[]")
                page_nid = f"page:{row['id']}"
                for tag in tags:
                    tag_edges.append(GraphEdge(
                        source=page_nid,
                        target=f"tag:{tag}",
                        edge_type="tagged_with",
                    ))
            if tag_edges:
                target.upsert_edges_batch(tag_edges)

        # 2. 同步更新的 blocks
        block_rows = conn.execute(
            "SELECT id, parent_id, page_id, content, block_type, properties, order_idx "
            "FROM blocks WHERE updated_at >= ?",
            (since,),
        ).fetchall()
        if block_rows:
            block_nodes = [self._block_to_node(r) for r in block_rows]
            target.upsert_nodes_batch(block_nodes)

            block_edges = []
            for row in block_rows:
                block_nid = f"block:{row['id']}"
                if row["page_id"]:
                    block_edges.append(GraphEdge(
                        source=f"page:{row['page_id']}",
                        target=block_nid,
                        edge_type="contains",
                    ))
                if row["parent_id"]:
                    block_edges.append(GraphEdge(
                        source=f"block:{row['parent_id']}",
                        target=block_nid,
                        edge_type="parent",
                    ))
            if block_edges:
                target.upsert_edges_batch(block_edges)
            stats["blocks"] = len(block_nodes)

        # 3. 同步新增/更新的 entity_refs
        ref_rows = conn.execute(
            "SELECT source_type, source_id, target_type, target_id, ref_type "
            "FROM entity_refs WHERE rowid IN ("
            "  SELECT rowid FROM entity_refs ORDER BY rowid DESC LIMIT 10000"
            ")",
        ).fetchall()
        if ref_rows:
            edges = [
                GraphEdge(
                    source=make_node_id(r["source_type"], r["source_id"]),
                    target=make_node_id(r["target_type"], r["target_id"]),
                    edge_type=r["ref_type"] or "mention",
                )
                for r in ref_rows
            ]
            target.upsert_edges_batch(edges)
            stats["edges"] = len(edges)

        logger.info("Incremental sync completed: %s", stats)
        return stats

    # ------------------------------------------------------------------
    # 删除同步
    # ------------------------------------------------------------------

    def sync_delete(self, target: GraphBackend, node_id: str) -> None:
        """同步删除操作到目标后端"""
        target.delete_node(node_id)

    def sync_delete_edges(self, target: GraphBackend, node_id: str) -> None:
        """同步删除与节点关联的边"""
        target.delete_edges_by_node(node_id)

    # ------------------------------------------------------------------
    # 内部迁移方法
    # ------------------------------------------------------------------

    def _migrate_pages(self, target: GraphBackend, batch_size: int) -> int:
        conn = self._db.get_conn()
        total = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        offset = 0
        count = 0
        while offset < total:
            rows = conn.execute(
                "SELECT id, title, file_type, tags, source_type, content "
                "FROM knowledge_items ORDER BY created_at LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            if not rows:
                break
            nodes = [self._page_to_node(r) for r in rows]
            target.upsert_nodes_batch(nodes)
            count += len(nodes)
            offset += batch_size
            self._emit_progress(
                f"页面迁移: {count}/{total}",
                current=count, total=total,
            )
        return count

    def _migrate_blocks(self, target: GraphBackend, batch_size: int) -> int:
        conn = self._db.get_conn()
        total = conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        offset = 0
        count = 0
        while offset < total:
            rows = conn.execute(
                "SELECT id, parent_id, page_id, content, block_type, properties, order_idx "
                "FROM blocks ORDER BY created_at LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            if not rows:
                break
            nodes = [self._block_to_node(r) for r in rows]
            target.upsert_nodes_batch(nodes)
            count += len(nodes)
            offset += batch_size
            self._emit_progress(
                f"Block 迁移: {count}/{total}",
                current=count, total=total,
            )
        return count

    def _migrate_tags(self, target: GraphBackend, batch_size: int) -> int:
        """收集所有标签并创建节点"""
        conn = self._db.get_conn()
        all_tags: set[str] = set()
        offset = 0
        while True:
            rows = conn.execute(
                "SELECT tags FROM knowledge_items LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                tags = self._load_json_list(row["tags"] if "tags" in row.keys() else "[]")
                all_tags.update(tags)
            offset += batch_size

        if not all_tags:
            return 0

        nodes = [
            GraphNode(
                id=f"tag:{tag}", node_type="tag",
                label=tag, source_id=tag,
            )
            for tag in sorted(all_tags)
        ]
        target.upsert_nodes_batch(nodes)
        return len(nodes)

    def _migrate_edges(self, target: GraphBackend, batch_size: int) -> int:
        conn = self._db.get_conn()
        count = 0

        # 1. entity_refs
        total_refs = conn.execute("SELECT COUNT(*) FROM entity_refs").fetchone()[0]
        offset = 0
        while offset < total_refs:
            rows = conn.execute(
                "SELECT source_type, source_id, target_type, target_id, ref_type "
                "FROM entity_refs LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            if not rows:
                break
            edges = [
                GraphEdge(
                    source=make_node_id(r["source_type"], r["source_id"]),
                    target=make_node_id(r["target_type"], r["target_id"]),
                    edge_type=r["ref_type"] or "mention",
                )
                for r in rows
            ]
            target.upsert_edges_batch(edges)
            count += len(edges)
            offset += batch_size
            self._emit_progress(
                f"Entity refs 迁移: {count}/{total_refs}",
                current=count, total=total_refs,
            )

        # 2. contains 边 (page → block)
        total_blocks = conn.execute("SELECT COUNT(*) FROM blocks WHERE page_id IS NOT NULL").fetchone()[0]
        offset = 0
        contains_count = 0
        while offset < total_blocks:
            rows = conn.execute(
                "SELECT id, page_id FROM blocks WHERE page_id IS NOT NULL "
                "ORDER BY created_at LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            if not rows:
                break
            edges = [
                GraphEdge(
                    source=f"page:{r['page_id']}",
                    target=f"block:{r['id']}",
                    edge_type="contains",
                )
                for r in rows if r["page_id"]
            ]
            target.upsert_edges_batch(edges)
            contains_count += len(edges)
            count += len(edges)
            offset += batch_size

        # 3. parent 边 (block → child block)
        offset = 0
        parent_count = 0
        total_parents = conn.execute("SELECT COUNT(*) FROM blocks WHERE parent_id IS NOT NULL").fetchone()[0]
        while offset < total_parents:
            rows = conn.execute(
                "SELECT id, parent_id FROM blocks WHERE parent_id IS NOT NULL "
                "ORDER BY created_at LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            if not rows:
                break
            edges = [
                GraphEdge(
                    source=f"block:{r['parent_id']}",
                    target=f"block:{r['id']}",
                    edge_type="parent",
                )
                for r in rows if r["parent_id"]
            ]
            target.upsert_edges_batch(edges)
            parent_count += len(edges)
            count += len(edges)
            offset += batch_size

        # 4. tagged_with 边 (page → tag)
        offset = 0
        tag_count = 0
        total_pages = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        while offset < total_pages:
            rows = conn.execute(
                "SELECT id, tags FROM knowledge_items LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            if not rows:
                break
            edges = []
            for r in rows:
                tags = self._load_json_list(r["tags"] if "tags" in r.keys() else "[]")
                for tag in tags:
                    edges.append(GraphEdge(
                        source=f"page:{r['id']}",
                        target=f"tag:{tag}",
                        edge_type="tagged_with",
                    ))
            target.upsert_edges_batch(edges)
            tag_count += len(edges)
            count += len(edges)
            offset += batch_size

        # 5. tag_relations (tag DAG)
        try:
            tag_rel_rows = conn.execute("SELECT parent_tag, child_tag FROM tag_relations").fetchall()
            if tag_rel_rows:
                edges = [
                    GraphEdge(
                        source=f"tag:{r['parent_tag']}",
                        target=f"tag:{r['child_tag']}",
                        edge_type="tag_parent",
                    )
                    for r in tag_rel_rows
                ]
                target.upsert_edges_batch(edges)
                count += len(edges)
        except Exception:
            pass

        return count

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _row_get(row, key: str, default=None):
        """安全读取 sqlite3.Row 的字段值"""
        try:
            return row[key]
        except (IndexError, KeyError):
            return default

    @staticmethod
    def _page_to_node(row) -> GraphNode:
        return GraphNode(
            id=f"page:{row['id']}",
            node_type="page",
            label=row["title"] or "" if "title" in row.keys() else "",
            source_id=row["id"],
            properties={
                "file_type": row["file_type"] or "" if "file_type" in row.keys() else "",
                "source_type": row["source_type"] or "" if "source_type" in row.keys() else "",
            },
        )

    @staticmethod
    def _block_to_node(row) -> GraphNode:
        return GraphNode(
            id=f"block:{row['id']}",
            node_type="block",
            label=(row["content"] or "")[:80] if "content" in row.keys() else "",
            source_id=row["id"],
            properties={
                "block_type": row["block_type"] or "text" if "block_type" in row.keys() else "text",
                "page_id": row["page_id"] or "" if "page_id" in row.keys() else "",
                "order_idx": row["order_idx"] or 0 if "order_idx" in row.keys() else 0,
            },
        )

    @classmethod
    def _load_json_list(cls, value) -> list[str]:
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
