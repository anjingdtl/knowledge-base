"""Backward-compatible reranker entry point.

New code should use `from src.services.rerankers import create_reranker`.
This module preserves the old LLMReranker class for backward compatibility.
"""
from __future__ import annotations

import logging

from src.services.rerankers.base import Reranker
from src.services.rerankers.factory import create_reranker

logger = logging.getLogger(__name__)


class LLMReranker:
    """Legacy wrapper — delegates to the provider-based reranker.

    Preserves the original constructor signature (llm, config) so that
    existing code using `LLMReranker(llm=..., config=...)` continues to work.
    """

    def __init__(self, llm=None, config=None):
        self._impl: Reranker = create_reranker(config=config, llm=llm)

    def rerank(self, query: str, candidates: list[dict], top_n: int = 5) -> list[dict]:
        return self._impl.rerank(query, candidates, top_n)


# Keep get_reranker() function for backward compat
_reranker_instance: LLMReranker | None = None


def get_reranker() -> LLMReranker:
    """获取重排序器实例（单例）"""
    global _reranker_instance
    if _reranker_instance is None:
        _reranker_instance = LLMReranker()
    return _reranker_instance
