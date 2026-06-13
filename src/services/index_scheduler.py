"""索引调度器 — 合并文件变更事件并批量触发索引"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from src.models.indexing import IndexResult

logger = logging.getLogger(__name__)


def _normalize(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


class IndexScheduler:
    """合并文件变更事件并在 debounce 窗口后批量触发索引

    同一路径在 debounce 窗口内的多个事件会被合并:
      - delete + create → modify
      - 多次 modify → 单次 modify
      - create + delete → 忽略（净效果为零）
    """

    def __init__(self, path_indexer, debounce_ms: int = 500):
        self._indexer = path_indexer
        self._debounce_ms = debounce_ms
        self._pending: dict[str, str] = {}  # normalized_path -> event_type
        self._lock = threading.Lock()
        self._shutdown = False
        self._processing = False

    def schedule(self, path: str, event_type: str) -> None:
        """添加事件。同一路径在 debounce 窗口内合并。

        event_type: 'created' | 'modified' | 'deleted'
        """
        if self._shutdown:
            return

        norm = _normalize(path)
        with self._lock:
            existing = self._pending.get(norm)
            if existing is None:
                self._pending[norm] = event_type
            else:
                self._pending[norm] = self._merge_events(existing, event_type)

    def _merge_events(self, prev: str, new: str) -> str:
        """合并两个事件"""
        # delete + create → modify
        if prev == "deleted" and new == "created":
            return "modified"
        # create + delete → 忽略（移除）
        if prev == "created" and new == "deleted":
            return "__drop__"
        # 多次 modify → 单次 modify
        # create + modify → create (still new)
        # 其他情况 → 以最新事件为准
        return new

    def flush(self) -> IndexResult:
        """处理所有待处理事件"""
        with self._lock:
            events = dict(self._pending)
            self._pending.clear()

        # 移除 __drop__ 标记
        events = {k: v for k, v in events.items() if v != "__drop__"}

        if not events:
            return IndexResult()

        self._processing = True
        result = IndexResult()

        try:
            # 按类型分组处理
            created_paths = [p for p, e in events.items() if e == "created"]
            modified_paths = [p for p, e in events.items() if e == "modified"]
            deleted_paths = [p for p, e in events.items() if e == "deleted"]

            # 处理 created 和 modified（都需要重新索引）
            for p in created_paths + modified_paths:
                try:
                    r = self._indexer.index_path(Path(p), recursive=False, force=True)
                    result.created += r.created
                    result.updated += r.updated
                    result.failed.extend(r.failed)
                except Exception as e:
                    logger.warning("IndexScheduler: failed to index %s: %s", p, e)
                    result.failed.append({"path": p, "error": str(e)})

            # 处理 deleted
            for p in deleted_paths:
                try:
                    deleted = self._indexer.delete_path(Path(p))
                    result.deleted += deleted.deleted
                    result.skipped += deleted.skipped
                    result.failed.extend(deleted.failed)
                except Exception as e:
                    logger.warning("IndexScheduler: failed to delete %s: %s", p, e)
                    result.failed.append({"path": p, "error": str(e)})
        finally:
            self._processing = False

        logger.info(
            "IndexScheduler flush: created=%d updated=%d deleted=%d failed=%d",
            result.created, result.updated, result.deleted, len(result.failed),
        )
        return result

    def shutdown(self) -> None:
        """停止接受新事件，等待当前处理完成"""
        self._shutdown = True
        # 等待当前处理完成
        while self._processing:
            pass

    @property
    def pending_count(self) -> int:
        """待处理事件数"""
        with self._lock:
            return len([v for v in self._pending.values() if v != "__drop__"])
