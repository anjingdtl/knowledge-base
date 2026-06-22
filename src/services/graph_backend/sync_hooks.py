"""SQLite-only 图后端同步钩子兼容层。

图数据现在只保存在 SQLite 中，业务层写入 SQLite 表即完成同步。
这个类保留给旧调用点使用，所有方法都是 no-op。
"""
from __future__ import annotations

from src.services.graph_backend.base import GraphBackend


class GraphSyncHook:
    """兼容旧外部图后端同步接口的 no-op 钩子。"""

    def __init__(self, backend: GraphBackend):
        self._backend = backend
        self._enabled = False

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
        return None

    def on_page_deleted(self, page_id: str) -> None:
        return None

    def on_blocks_synced(self, page_id: str, blocks: list[dict]) -> None:
        return None

    def on_blocks_deleted(self, page_id: str, block_ids: list[str]) -> None:
        return None

    def on_entity_ref_created(
        self,
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
        ref_type: str,
    ) -> None:
        return None

    def on_tag_relation_created(self, parent_tag: str, child_tag: str) -> None:
        return None
