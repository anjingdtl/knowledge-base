"""Candidate pool policy — explicit separation of public top_k and internal fetch_k.

ADR ``docs/architecture/adr-search-ask-contract-v2.md`` §5. The public result
count is always bounded by ``public_top_k`` (the caller's ``top_k``); internal
over-fetch, rerank output and final packaging are derived from one policy
object so the main path and every fallback share identical pool semantics.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Internal pool multiplier / floor. Keeping a wider pre-rerank pool prevents a
# single sparse FTS/vector channel from collapsing recall before fusion and
# rerank can compare evidence (see RawRetriever.retrieve).
_FETCH_MULTIPLIER = 4
_FETCH_FLOOR = 20
_DEFAULT_MAX_PER_DOCUMENT = 3


@dataclass(frozen=True)
class CandidatePoolPolicy:
    """Frozen candidate pool sizing for one retrieval request."""

    public_top_k: int
    fetch_k: int
    rerank_top_k: int
    final_top_k: int
    max_per_document: int = _DEFAULT_MAX_PER_DOCUMENT

    def __post_init__(self) -> None:
        if self.public_top_k < 1:
            raise ValueError("public_top_k must be >= 1")
        if self.fetch_k < self.public_top_k:
            raise ValueError("fetch_k must be >= public_top_k")
        if self.rerank_top_k < 1 or self.final_top_k < 1:
            raise ValueError("rerank_top_k/final_top_k must be >= 1")

    @classmethod
    def from_request(
        cls,
        top_k: int,
        *,
        config: Any = None,
    ) -> "CandidatePoolPolicy":
        """Derive the policy from the public request.

        ``fetch_k = max(public_top_k * 4, 20)`` by default; config key
        ``rag.retrieval.fetch_k_floor`` may raise the floor but never shrink
        the pool below the multiplier result.
        """
        public_top_k = max(1, int(top_k))
        fetch_k = max(public_top_k * _FETCH_MULTIPLIER, _FETCH_FLOOR)
        floor_override = _read_floor(config)
        if floor_override is not None:
            fetch_k = max(fetch_k, floor_override)
        return cls(
            public_top_k=public_top_k,
            fetch_k=fetch_k,
            rerank_top_k=public_top_k,
            final_top_k=public_top_k,
        )

    def to_trace(self) -> dict[str, int]:
        """Deterministic trace fragment (no timestamps)."""
        return asdict(self)


def _read_floor(config: Any) -> int | None:
    if config is None:
        return None
    try:
        if isinstance(config, dict):
            raw = (config.get("rag") or {}).get("retrieval", {}).get("fetch_k_floor")
        else:
            getter = getattr(config, "get", None)
            if not callable(getter):
                return None
            raw = getter("rag.retrieval.fetch_k_floor", None)
        if raw is None:
            return None
        value = int(raw)
        return value if value > 0 else None
    except (TypeError, ValueError, AttributeError):
        return None
