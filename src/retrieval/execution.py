"""Re-export SearchExecution as the orchestrator final contract.

Phase-2 must not fork or mutate the Phase-1 SearchExecution field semantics.
"""
from __future__ import annotations

from typing import Any

from src.models.search_execution import SearchExecution

__all__ = ["SearchExecution", "build_execution"]


def build_execution(
    results: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    trace: dict[str, Any] | None = None,
    disclose_claims: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    conflicts: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    fallbacks: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    warnings: list[str] | tuple[str, ...] = (),
) -> SearchExecution:
    """Assemble a SearchExecution from mutable pipeline fragments."""
    return SearchExecution(
        results=tuple(results),
        trace=dict(trace or {}),
        disclose_claims=tuple(disclose_claims),
        conflicts=tuple(conflicts),
        fallbacks=tuple(fallbacks),
        warnings=tuple(warnings),
    )
