"""Verified retrieval policy: Raw + VerifiedProvider → Fusion → SearchExecution."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from src.models.search_execution import SearchExecution
from src.retrieval.packaging import SearchRequestState, to_execution

if TYPE_CHECKING:
    from src.retrieval.fusion import VerifiedFusion
    from src.services.search_service import SearchService

logger = logging.getLogger(__name__)


class VerifiedPolicy:
    """Verified hybrid path. Wiki failures never block Raw."""

    def __init__(
        self,
        search_service: "SearchService | None" = None,
        *,
        fusion: "VerifiedFusion | None" = None,
    ):
        self._svc = search_service
        if fusion is not None:
            self._fusion = fusion
        elif search_service is not None:
            self._fusion = search_service._get_verified_fusion()
        else:
            raise TypeError("VerifiedPolicy requires fusion or search_service")

    def execute(
        self,
        query: str,
        *,
        top_k: int = 5,
        query_spec: Any = None,
        deadline: float | None = None,
    ) -> SearchExecution:
        _ = deadline
        if query_spec is not None and self._svc is not None:
            return self._svc.execute_query_spec(query_spec, top_k=top_k)

        t0 = time.monotonic()
        state = SearchRequestState(
            trace={
                "mode": "legacy",
                "query": (query or "")[:200],
                "stages": {},
            },
        )
        output = self._fusion.run(query, top_k=top_k, state=state, t0=t0)
        elapsed = time.monotonic() - t0
        logger.info(
            "Verified hybrid search in %.2fs: %d results for query=%r",
            elapsed,
            len(output),
            (query or "")[:50],
        )
        state.trace["elapsed_ms"] = round(elapsed * 1000, 2)
        return to_execution(output, state)
