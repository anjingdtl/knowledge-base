"""RetrievalPolicy protocol."""
from __future__ import annotations

from typing import Any, Protocol

from src.models.search_execution import SearchExecution


class RetrievalPolicy(Protocol):
    def execute(
        self,
        query: str,
        *,
        top_k: int = 5,
        query_spec: Any = None,
        deadline: float | None = None,
    ) -> SearchExecution:
        ...
