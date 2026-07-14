"""Removed legacy primary retrieval path (WP5 / v1.10.0).

Historical dual-path (legacy/shadow orchestrator) was deleted after Unified
default observation. Callers should use SearchService.execute() which always
runs RetrievalOrchestrator unified.
"""
from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

from src.models.search_execution import SearchExecution

if TYPE_CHECKING:
    from src.services.search_service import SearchService


def execute_primary_legacy(
    service: "SearchService",
    query: str,
    top_k: int = 5,
    query_spec: Any = None,
) -> SearchExecution:
    """Deprecated: redirects to unified SearchService.execute()."""
    warnings.warn(
        "execute_primary_legacy is removed in v1.10.0; use SearchService.execute()",
        DeprecationWarning,
        stacklevel=2,
    )
    return service.execute(query, top_k=top_k, query_spec=query_spec)
