"""Legacy primary retrieval path — rollback and shadow baseline only.

Do not use for new product features. Prefer RetrievalOrchestrator unified path.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from src.models.search_execution import SearchExecution
from src.retrieval.packaging import SearchRequestState, to_execution

if TYPE_CHECKING:
    from src.services.search_service import SearchService

logger = logging.getLogger(__name__)


def execute_primary_legacy(
    service: "SearchService",
    query: str,
    top_k: int = 5,
    query_spec: Any = None,
) -> SearchExecution:
    """Original execute body for legacy/shadow primary and emergency rollback."""
    t0 = time.monotonic()
    state = SearchRequestState(
        trace={
            "mode": "legacy",
            "query": (query or "")[:200],
            "stages": {},
        },
    )

    if query_spec is not None:
        spec_exec = service.execute_query_spec(query_spec, top_k=top_k)
        if spec_exec.results:
            return spec_exec

    if service._should_use_verified_hybrid():
        output = service._search_verified_hybrid(
            query, top_k=top_k, t0=t0, state=state,
        )
        elapsed = time.monotonic() - t0
        logger.info(
            "Verified hybrid search in %.2fs: %d results for query=%r",
            elapsed,
            len(output),
            (query or "")[:50],
        )
        state.trace["elapsed_ms"] = round(elapsed * 1000, 2)
        return to_execution(output, state)

    output = service._search_legacy_pipeline(
        query, top_k=top_k, t0=t0, state=state,
    )
    return to_execution(output, state)
