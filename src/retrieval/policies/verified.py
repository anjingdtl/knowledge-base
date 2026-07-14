"""Verified retrieval policy: Raw + VerifiedProvider → Fusion → SearchExecution."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.models.search_execution import SearchExecution

if TYPE_CHECKING:
    from src.services.search_service import SearchService


class VerifiedPolicy:
    """Verified hybrid path. Wiki failures never block Raw."""

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
        _ = deadline
        return self._svc.execute_verified(
            query,
            top_k=top_k,
            query_spec=query_spec,
        )
