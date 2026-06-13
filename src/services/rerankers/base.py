"""重排序器协议定义 — 所有实现必须满足此接口"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Reranker(Protocol):
    """Reranker protocol — all implementations must satisfy this."""

    def rerank(self, query: str, candidates: list[dict], top_n: int = 5) -> list[dict]:
        """Rerank candidates by relevance to query.

        Args:
            query: 用户查询文本
            candidates: 候选结果列表，每个元素为 dict，至少包含 'text' 字段
            top_n: 返回前 N 个结果

        Returns:
            按相关性排序的候选列表，每个元素附带 'rerank_score' 字段。
            失败时不得抛出异常 — 应记录警告并返回原始候选列表。
        """
        ...
