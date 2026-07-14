"""SearchExecution packaging helpers for retrieval policies.

No ranking/scoring formulas live here — only structure assembly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.models.search_execution import SearchExecution
from src.retrieval.models import RawRetrievalResult


@dataclass
class SearchRequestState:
    """Per-request mutable state (must not be stored on long-lived services)."""

    trace: dict[str, Any]
    disclose_claims: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    fallbacks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    claim_error: str | None = None

    def record_fallback(
        self,
        reason: str,
        *,
        from_layer: str = "verified_wiki",
        to_layer: str = "raw_retrieval",
    ) -> None:
        self.trace.setdefault("stages", {})["fallback"] = reason
        self.fallbacks.append({
            "from": from_layer,
            "to": to_layer,
            "reason": str(reason),
        })


def to_execution(output: list[dict], state: SearchRequestState) -> SearchExecution:
    conflicts = state.conflicts or list(state.trace.get("conflicts") or [])
    return SearchExecution(
        results=tuple(output),
        trace=state.trace,
        disclose_claims=tuple(state.disclose_claims),
        conflicts=tuple(conflicts),
        fallbacks=tuple(state.fallbacks),
        warnings=tuple(state.warnings),
    )


def build_evidence_only_execution(raw: RawRetrievalResult) -> SearchExecution:
    """Map RawRetriever output into public SearchExecution."""
    state = SearchRequestState(trace=dict(raw.trace or {}))
    if "stages" not in state.trace:
        state.trace["stages"] = {}
    state.warnings.extend(raw.warnings)
    state.fallbacks.extend(raw.fallbacks)
    return to_execution(list(raw.candidates), state)


def merge_raw_into_state(raw: RawRetrievalResult, state: SearchRequestState) -> list[dict]:
    """Merge RawRetriever side-channels into an existing request state."""
    if raw.trace.get("mode"):
        state.trace["mode"] = raw.trace["mode"]
    if "stages" in raw.trace:
        state.trace.setdefault("stages", {}).update(raw.trace["stages"])
    if "elapsed_ms" in raw.trace:
        state.trace["elapsed_ms"] = raw.trace["elapsed_ms"]
    if "result_count" in raw.trace:
        state.trace["result_count"] = raw.trace["result_count"]
    state.warnings.extend(raw.warnings)
    state.fallbacks.extend(raw.fallbacks)
    return list(raw.candidates)
