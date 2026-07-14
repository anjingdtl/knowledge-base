"""Evidence-only retrieval policy: RawRetriever → SearchExecution."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from src.models.search_execution import SearchExecution

if TYPE_CHECKING:
    from src.services.search_service import SearchService


class EvidenceOnlyPolicy:
    """Raw evidence path (legacy pipeline semantics, including legacy wiki FTS)."""

    def __init__(self, search_service: "SearchService"):
        self._svc = search_service

    def execute(
        self,
        query: str,
        *,
        top_k: int = 5,
        query_spec: Any = None,
        deadline: float | None = None,
    ) -> SearchExecution:
        # deadline reserved for future stage budget; primary path uses stage timeouts
        _ = deadline
        return self._svc.execute_evidence_only(
            query,
            top_k=top_k,
            query_spec=query_spec,
        )
