"""ContextBuilder — limited generation context from retrieval rows."""
from __future__ import annotations

from typing import Any

from src.answering.fallbacks import build_generation_context


class ContextBuilder:
    """Build LLM context from claim/raw rows. Does not retrieve or gate."""

    def build(
        self,
        claim_rows: list[dict[str, Any]],
        raw_rows: list[dict[str, Any]],
        *,
        conflicts: list[dict[str, Any]] | None = None,
    ) -> str:
        return build_generation_context(
            claim_rows,
            raw_rows,
            conflicts=list(conflicts or []),
        )
