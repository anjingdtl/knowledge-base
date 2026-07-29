"""API 重排序器 — 调用专用重排序 API 端点 (SiliconFlow, Cohere 等)"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.services.deadline import DeadlineTimeout, remaining_deadline
from src.services.provider_runtime import ProviderRequest, run_provider_operation

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
        self._credential_configured = bool(api_key)
        self._config = config
        self._timeout = timeout

    def rerank(self, query: str, candidates: list[dict], top_n: int = 5) -> list[dict]:
        """Call rerank API and sort candidates by score."""
        if not candidates:
            return []

        # A broad raw pool is valuable for recall, but sending every long
        # passage to a remote cross-encoder turns one ranking request into a
        # multi-second (often timed-out) payload.  Rank a bounded prefix and
        # keep the rest in deterministic raw-score order as a safety net.
        max_candidates = 12
        max_document_chars = 800
        if self._config is not None:
            configured_candidates = self._config.get("reranker.max_candidates", None)
            configured_chars = self._config.get("reranker.max_document_chars", None)
            try:
                if int(configured_candidates) >= 1:
                    max_candidates = int(configured_candidates)
            except (TypeError, ValueError):
                pass
            try:
                if int(configured_chars) >= 100:
                    max_document_chars = int(configured_chars)
            except (TypeError, ValueError):
                pass
        max_candidates = max(1, max_candidates)
        max_document_chars = max(100, max_document_chars)
        ranking_candidates = candidates[:max_candidates]
        remainder = candidates[max_candidates:]
        texts = [str(cand.get("text", ""))[:max_document_chars] for cand in ranking_candidates]

        try:
            payload = {
                "query": query,
                "documents": texts,
                "top_n": min(len(texts), top_n),
            }
            provider_timeout = float(self._timeout)
            remaining = remaining_deadline()
            if remaining is not None:
                provider_timeout = min(provider_timeout, max(0.01, remaining))
            response = run_provider_operation(
                "reranker",
                ProviderRequest(
                    provider_type="reranker_api",
                    base_url=self._base_url,
                    model=self._model,
                    payload=payload,
                    timeout_seconds=provider_timeout,
                    secret_env_key="SHINEHE_RERANKER_API_KEY",
                    credential=self._api_key,
                ),
                isolation_mode="process",
                timeout=provider_timeout,
            )
            if not response.ok or not isinstance(response.data, dict):
                raise RuntimeError(
                    response.error_message
                    or response.error_type
                    or "Reranker provider returned invalid response"
                )
            result = response.data

            # 解析响应分数
            scores_map: dict[int, float] = {}
            for item in result.get("results", []):
                idx = item.get("index", -1)
                score = item.get("relevance_score", 0.5)
                if 0 <= idx < len(candidates):
                    scores_map[idx] = score

            # 附加分数到候选
            for i, cand in enumerate(ranking_candidates):
                cand["rerank_score"] = scores_map.get(i, 0.5)

            # 按分数排序
            ranking_candidates.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)

            # 应用最低分数过滤
            min_score = 0.3
            if self._config is not None:
                min_score = self._config.get("rag.rerank.min_score", 0.3)

            filtered = [c for c in ranking_candidates if c.get("rerank_score", 0) >= min_score][:top_n]

            # 过滤太严时保留 top_n 避免上下文为空
            if not filtered and ranking_candidates:
                filtered = ranking_candidates[:top_n]

            # Keep a deterministic fallback tail for callers that request
            # more results than the rerank output, without assigning invented
            # rerank scores to documents that were not sent to the provider.
            return filtered + remainder[:max(0, top_n - len(filtered))]

        except DeadlineTimeout:
            raise
        except Exception as e:
            logger.warning("API reranker failed: %s, returning original candidates", e)
            return candidates
