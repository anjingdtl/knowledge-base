"""RebuildScheduler — per-kid debounce 合并 source 变更事件(Phase 5)。

合并范式对齐 IndexScheduler,但语义是知识失效传播(非原始索引):
  - update + update → update
  - update + delete → delete(delete 主导,删 source 后无须再 update)
  - delete + update → delete
  - distinct knowledge_id 不合并
flush() 对每个 pending kid 调 rebuild_service.rebuild(knowledge_id=, event=)。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RebuildBatchResult:
    processed: int = 0
    failed: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class RebuildScheduler:
    def __init__(self, rebuild_service: Any, debounce_ms: int = 500) -> None:
        self._svc = rebuild_service
        self._debounce_ms = debounce_ms
        self._pending: dict[str, str] = {}  # knowledge_id -> event
        self._lock = threading.Lock()

    def schedule(self, knowledge_id: str, event_type: str) -> None:
        """添加事件。同 knowledge_id 在窗口内合并。"""
        if event_type not in ("update", "delete"):
            return
        with self._lock:
            prev = self._pending.get(knowledge_id)
            self._pending[knowledge_id] = self._merge(prev, event_type)

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
        result = RebuildBatchResult()
        for kid in sorted(events):
            try:
                self._svc.rebuild(knowledge_id=kid, event=events[kid])
                result.processed += 1
            except Exception as exc:  # noqa: BLE001 - 单 kid 失败不阻断其余
                result.failed.append({"knowledge_id": kid, "error": str(exc)})
        return result
