"""ReadUseCase — application entry for document/block reads (Phase 2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReadRequest:
    knowledge_id: str | None = None
    block_id: str | None = None
    passage_id: str | None = None


class ReadUseCase:
    def __init__(self, knowledge_repository: Any = None, container: Any = None):
        self._repo = knowledge_repository
        self._container = container

    def execute(self, request: ReadRequest) -> dict[str, Any]:
        kid = request.knowledge_id
        if not kid and self._container is not None and request.block_id:
            # Best-effort resolve via db when only block_id is given.
            db = getattr(self._container, "db", None)
            if db is not None and hasattr(db, "get_block"):
                block = db.get_block(request.block_id) or {}
                kid = block.get("page_id") or block.get("knowledge_id")
        if self._repo is not None and kid and hasattr(self._repo, "get"):
            item = self._repo.get(kid)
            return {"knowledge_id": kid, "item": item}
        if self._container is not None and kid:
            db = getattr(self._container, "db", None)
            if db is not None and hasattr(db, "get_knowledge"):
                return {"knowledge_id": kid, "item": db.get_knowledge(kid)}
        return {"knowledge_id": kid, "item": None}
