"""Retrieval-layer internal result contracts (Phase-2).

These are internal orchestration types. The public return type of search remains
``SearchExecution`` (see ``src.models.search_execution``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RawRetrievalResult:
    """Evidence / raw retrieval channel output."""

    candidates: tuple[dict[str, Any], ...]
    trace: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    fallbacks: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class VerifiedServingResult:
    """Gate-protected Wiki claim serving output.

    ``claim_pairs`` keeps (Claim, ServingDecision) for fusion without re-query.
    Serializable side-channels live in eligible/disclose claim dict rows.
    """

    eligible_claims: tuple[dict[str, Any], ...]
    disclose_claims: tuple[dict[str, Any], ...]
    conflicts: tuple[dict[str, Any], ...] = ()
    trace: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    fallback_reason: str | None = None
    claim_pairs: tuple[Any, ...] = ()
