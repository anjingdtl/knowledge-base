"""文件变更监听器 — 基于 watchdog 的实时目录监控"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from src.services.index_scheduler import IndexScheduler

logger = logging.getLogger(__name__)


def _normalize(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


class FileWatcher:
    """监听目录变更并通过 IndexScheduler 触发增量索引

    依赖 watchdog 可选依赖:
        pip install shinehe-knowledge[watch]
    """

    def __init__(
        self,
        scheduler: IndexScheduler,
        root: Path,
        recursive: bool = True,
    ):
        self._scheduler = scheduler
        self._root = Path(os.path.normcase(os.path.normpath(str(root))))
        self._recursive = recursive
        self._observer = None
        self._running = False

    def start(self) -> None:
        """启动文件监听

        Raises:
            RuntimeError: watchdog 未安装
        """
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            raise RuntimeError(
                "watchdog not installed. Run: pip install shinehe-knowledge[watch]"
            )

        scheduler: IndexScheduler = self._scheduler  # type: ignore[assignment]

        class _Handler(FileSystemEventHandler):
            """将 watchdog 事件标准化并推入 IndexScheduler"""

            def on_created(self, event):
                if not event.is_directory:
                    scheduler.schedule(event.src_path, "created")

            def on_modified(self, event):
                if not event.is_directory:
                    scheduler.schedule(event.src_path, "modified")

            def on_deleted(self, event):
                if not event.is_directory:
                    scheduler.schedule(event.src_path, "deleted")

            def on_moved(self, event):
                if not event.is_directory:
                    # 移动 = 旧路径删除 + 新路径创建
                    scheduler.schedule(event.src_path, "deleted")
                    scheduler.schedule(event.dest_path, "created")

        self._observer = Observer()
        self._observer.schedule(_Handler(), str(self._root), recursive=self._recursive)  # type: ignore[attr-defined]
        self._observer.start()  # type: ignore[attr-defined]
        self._running = True
        logger.info(
            "FileWatcher started on %s (recursive=%s)", self._root, self._recursive
        )

    def stop(self) -> None:
        """优雅停止"""
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            logger.info("FileWatcher stopped")

    @property
    def is_running(self) -> bool:
        return self._running
