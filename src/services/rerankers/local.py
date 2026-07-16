"""本地交叉编码器重排序器 — 使用 sentence-transformers 本地模型"""
from __future__ import annotations

import importlib
import importlib.util
import logging
from typing import TYPE_CHECKING

from src.services.deadline import DeadlineTimeout, remaining_deadline
from src.services.provider_runtime import ProviderRequest, run_provider_operation

if TYPE_CHECKING:
    from src.utils.config import Config

logger = logging.getLogger(__name__)


class LocalCrossEncoderReranker:
    """Reranker using a local sentence-transformers cross-encoder model."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        config: "Config | None" = None,
    ):
        self._model_name = model_name
        self._config = config
        self._model = None  # Lazy loaded
        self._available: bool | None = None

    @property
    def is_available(self) -> bool:
        """Check if sentence-transformers is installed without importing it at module level."""
        if self._available is None:
            try:
                self._available = importlib.util.find_spec("sentence_transformers") is not None
            except Exception:
                self._available = False
        return self._available

    def rerank(self, query: str, candidates: list[dict], top_n: int = 5) -> list[dict]:
        """Rerank using local cross-encoder.

        If sentence-transformers not installed, return candidates unchanged with warning.
        The model is loaded inside a terminable worker for each call so a stuck
        native inference cannot occupy the parent process indefinitely.
        """
        if not candidates:
            return []

        if not self.is_available:
            logger.warning("Local reranker unavailable: sentence-transformers not installed")
            return candidates

        try:
            pairs = [[query, c.get("text", "")] for c in candidates]
            timeout = 30.0
            if self._config is not None:
                timeout = float(self._config.get("reranker.timeout", 30) or 30)
            remaining = remaining_deadline()
            if remaining is not None:
                timeout = min(timeout, max(0.01, remaining))
            response = run_provider_operation(
                "reranker_local",
                ProviderRequest(
                    provider_type="local_cross_encoder",
                    base_url="",
                    model=self._model_name,
                    payload={"pairs": pairs},
                    timeout_seconds=timeout,
                    secret_env_key="",
                ),
                isolation_mode="process",
                timeout=timeout,
            )
            if not response.ok or not isinstance(response.data, list):
                raise RuntimeError(
                    response.error_message
                    or response.error_type
                    or "Local reranker returned invalid response"
                )
            scores = response.data

            for c, score in zip(candidates, scores):
                c["rerank_score"] = float(score)

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

        except DeadlineTimeout:
            raise
        except Exception as e:
            logger.warning("Local reranker failed: %s", e)
            return candidates
