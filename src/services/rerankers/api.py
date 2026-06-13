"""API 重排序器 — 调用专用重排序 API 端点 (SiliconFlow, Cohere 等)"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.utils.config import Config

logger = logging.getLogger(__name__)


class ApiReranker:
    """Reranker using a dedicated rerank API endpoint (e.g., SiliconFlow, Cohere)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        config: "Config | None" = None,
        timeout: float = 20,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._config = config
        self._timeout = timeout

    def rerank(self, query: str, candidates: list[dict], top_n: int = 5) -> list[dict]:
        """Call rerank API and sort candidates by score."""
        if not candidates:
            return []

        import httpx

        # 准备文档文本
        texts = [cand.get("text", "")[:1000] for cand in candidates]

        try:
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": self._model,
                "query": query,
                "documents": texts,
                "top_n": min(len(texts), 10),
            }

            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base_url}/rerank",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                result = resp.json()

            # 解析响应分数
            scores_map: dict[int, float] = {}
            for item in result.get("results", []):
                idx = item.get("index", -1)
                score = item.get("relevance_score", 0.5)
                if 0 <= idx < len(candidates):
                    scores_map[idx] = score

            # 附加分数到候选
            for i, cand in enumerate(candidates):
                cand["rerank_score"] = scores_map.get(i, 0.5)

            # 按分数排序
            candidates.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)

            # 应用最低分数过滤
            min_score = 0.3
            if self._config is not None:
                min_score = self._config.get("rag.rerank.min_score", 0.3)

            filtered = [c for c in candidates if c.get("rerank_score", 0) >= min_score][:top_n]

            # 过滤太严时保留 top_n 避免上下文为空
            if not filtered and candidates:
                filtered = candidates[:top_n]

            return filtered

        except Exception as e:
            logger.warning("API reranker failed: %s, returning original candidates", e)
            return candidates
