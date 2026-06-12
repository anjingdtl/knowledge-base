"""图后端同步钩子 — 确保关系数据与图后端保持一致

当 graph_backend 配置为 Neo4j 等非 SQLite 后端时，
业务层的写操作（创建/更新/删除页面、块、标签）需要同时同步到图后端。

本模块提供轻量级的同步钩子，供 FileGraphService 和其他服务调用。
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from src.services.graph_backend.base import (
    GraphBackend,
    GraphEdge,
    GraphNode,
    make_node_id,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class GraphSyncHook:
    """图后端同步钩子

    用法:
        hook = GraphSyncHook(graph_backend)
        hook.on_page_synced(page_id, title, tags, file_type, source_type)
        hook.on_page_deleted(page_id)
        hook.on_blocks_synced(page_id, blocks)
    """

    def __init__(self, backend: GraphBackend):
        self._backend = backend
        # SQLite 后端不需要同步（它就是数据源）
        self._enabled = backend.name != "sqlite"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def on_page_synced(
        self,
        page_id: str,
        title: str,
        tags: list[str] | None = None,
        file_type: str = "",
        source_type: str = "",
    ) -> None:
        """页面同步完成后的钩子"""
        if not self._enabled:
            return
        try:
            node = GraphNode(
                id=f"page:{page_id}",
                node_type="page",
                label=title,
                source_id=page_id,
                properties={"file_type": file_type, "source_type": source_type},
            )
            self._backend.upsert_node(node)

            # 同步标签节点和边
            if tags:
                for tag in tags:
                    tag_node = GraphNode(
                        id=f"tag:{tag}", node_type="tag",
                        label=tag, source_id=tag,
                    )
                    self._backend.upsert_node(tag_node)
                    self._backend.upsert_edge(GraphEdge(
                        source=f"page:{page_id}",
                        target=f"tag:{tag}",
                        edge_type="tagged_with",
                    ))
        except Exception as e:
            logger.warning("GraphSyncHook.on_page_synced failed for %s: %s", page_id, e)

    def on_page_deleted(self, page_id: str) -> None:
        """页面删除后的钩子"""
        if not self._enabled:
            return
        try:
            self._backend.delete_node(f"page:{page_id}")
        except Exception as e:
            logger.warning("GraphSyncHook.on_page_deleted failed for %s: %s", page_id, e)

    def on_blocks_synced(self, page_id: str, blocks: list[dict]) -> None:
        """块的缓存重建完成后的钩子

        Args:
            page_id: 所属页面 ID
            blocks: 块列表，每条含 id, parent_id, content, block_type, order_idx
        """
        if not self._enabled:
            return
        try:
            nodes = []
            edges = []
            for block in blocks:
                block_id = block["id"]
                nodes.append(GraphNode(
                    id=f"block:{block_id}",
                    node_type="block",
                    label=(block.get("content", "") or "")[:80],
                    source_id=block_id,
                    properties={
                        "block_type": block.get("block_type", "text"),
                        "page_id": page_id,
                        "order_idx": block.get("order_idx", 0),
                    },
                ))
                # contains 边
                edges.append(GraphEdge(
                    source=f"page:{page_id}",
                    target=f"block:{block_id}",
                    edge_type="contains",
                ))
                # parent 边
                parent_id = block.get("parent_id")
                if parent_id:
                    edges.append(GraphEdge(
                        source=f"block:{parent_id}",
                        target=f"block:{block_id}",
                        edge_type="parent",
                    ))

            self._backend.upsert_nodes_batch(nodes)
            self._backend.upsert_edges_batch(edges)
        except Exception as e:
            logger.warning("GraphSyncHook.on_blocks_synced failed for %s: %s", page_id, e)

    def on_blocks_deleted(self, page_id: str, block_ids: list[str]) -> None:
        """块删除后的钩子"""
        if not self._enabled:
            return
        try:
            for block_id in block_ids:
                self._backend.delete_node(f"block:{block_id}")
        except Exception as e:
            logger.warning("GraphSyncHook.on_blocks_deleted failed for %s: %s", page_id, e)

    def on_entity_ref_created(
        self,
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
        ref_type: str,
    ) -> None:
        """entity_ref 创建后的钩子"""
        if not self._enabled:
            return
        try:
            edge = GraphEdge(
                source=make_node_id(source_type, source_id),
                target=make_node_id(target_type, target_id),
                edge_type=ref_type or "mention",
            )
            self._backend.upsert_edge(edge)
        except Exception as e:
            logger.warning("GraphSyncHook.on_entity_ref_created failed: %s", e)

    def on_tag_relation_created(self, parent_tag: str, child_tag: str) -> None:
        """标签关系创建后的钩子"""
        if not self._enabled:
            return
        try:
            self._backend.upsert_edge(GraphEdge(
                source=f"tag:{parent_tag}",
                target=f"tag:{child_tag}",
                edge_type="tag_parent",
            ))
        except Exception as e:
            logger.warning("GraphSyncHook.on_tag_relation_created failed: %s", e)
