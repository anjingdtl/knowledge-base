"""Generation adapter — LLM call + evidence-summary fallback."""
from __future__ import annotations

import logging
from typing import Any, Callable

from src.answering.fallbacks import fallback_hybrid_text, fallback_raw_text

logger = logging.getLogger(__name__)


class Generator:
    """LLM generation with evidence-summary fallback (no retrieval)."""

    def __init__(self, llm: Any = None):
        self._llm = llm

    def make_generate_fn(self) -> Callable[[str, str], str] | None:
        if self._llm is None:
            return None

        llm = self._llm

        def generate_fn(question: str, context: str) -> str:
            from src.services.rag_pipeline import build_rag_messages
            from src.utils.llm_text import strip_think

            messages = build_rag_messages(question, context, [])
            if hasattr(llm, "chat_with_usage"):
                content, _usage = llm.chat_with_usage(messages)
                return strip_think(content)
            return strip_think(llm.chat(messages))

        return generate_fn

    def hybrid_fallback(
        self,
        question: str,
        claim_rows: list[dict[str, Any]],
        raw_rows: list[dict[str, Any]],
    ) -> str:
        return fallback_hybrid_text(question, claim_rows, raw_rows)

    def raw_fallback(self, question: str, raw_rows: list[dict[str, Any]]) -> str:
        return fallback_raw_text(question, raw_rows)
