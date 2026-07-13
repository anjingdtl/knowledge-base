"""RebuildScheduler — per-kid debounce 合并 source 变更事件(Phase 5)。

合并范式对齐 IndexScheduler,但语义是知识失效传播(非原始索引):
  - update + update → update
  - update + delete → delete(delete 主导,删 source 后无须再 update)
  - delete + update → delete
  - distinct knowledge_id 不合并

schedule() 仅入队并重置 debounce 定时器；定时器到期后自动 flush()。
debounce_ms=0 表示尽快异步 flush（同线程连续 schedule 仍可合并）。
flush() 也可被 CLI/测试显式调用。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RebuildBatchResult:
    processed: int = 0
    failed: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class RebuildScheduler:
    def __init__(self, rebuild_service: Any, debounce_ms: int = 500) -> None:
        self._svc = rebuild_service
        self._debounce_ms = max(0, int(debounce_ms))
        self._pending: dict[str, str] = {}  # knowledge_id -> event
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._shutdown = False

    def schedule(self, knowledge_id: str, event_type: str) -> None:
        """添加事件。同 knowledge_id 在窗口内合并；窗口结束后自动 flush。"""
        if self._shutdown:
            return
        if event_type not in ("update", "delete"):
            return
        with self._lock:
            prev = self._pending.get(knowledge_id)
            self._pending[knowledge_id] = self._merge(prev, event_type)
            self._arm_timer_unlocked()

    def _arm_timer_unlocked(self) -> None:
        """重置 debounce 定时器（调用方须持锁）。"""
        self._cancel_timer_unlocked()
        delay = self._debounce_ms / 1000.0
        timer = threading.Timer(delay, self._on_timer)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _cancel_timer_unlocked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_timer(self) -> None:
        try:
            self.flush()
        except Exception:
            logger.exception("RebuildScheduler auto-flush failed")

    @staticmethod
    def _merge(prev: str | None, new: str) -> str:
        if prev is None:
            return new
        # delete 主导:任一端 delete → delete(删 source 后 update 无意义)
        if "delete" in (prev, new):
            return "delete"
        return new  # update + update → update

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def flush(self) -> RebuildBatchResult:
        """处理所有待处理事件(字典序确定性)。单个失败不阻断其余。"""
        with self._lock:
            events = dict(self._pending)
            self._pending.clear()
            self._cancel_timer_unlocked()
        return self._process(events)

    def _process(self, events: dict[str, str]) -> RebuildBatchResult:
        result = RebuildBatchResult()
        if not events:
            return result
        for kid in sorted(events):
            try:
                self._svc.rebuild(knowledge_id=kid, event=events[kid])
                result.processed += 1
            except Exception as exc:  # noqa: BLE001 - 单 kid 失败不阻断其余
                result.failed.append({"knowledge_id": kid, "error": str(exc)})
                logger.warning(
                    "RebuildScheduler: rebuild failed for %s: %s", kid, exc,
                )
        logger.info(
            "RebuildScheduler flush: processed=%d failed=%d",
            result.processed, len(result.failed),
        )
        return result

    def shutdown(self) -> None:
        """停止接受新事件并 flush 剩余。"""
        self._shutdown = True
        self.flush()
