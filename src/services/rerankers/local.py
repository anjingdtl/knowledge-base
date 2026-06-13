"""本地交叉编码器重排序器 — 使用 sentence-transformers 本地模型"""
from __future__ import annotations

import importlib
import importlib.util
import logging
from typing import TYPE_CHECKING

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
        Model is loaded on first call and cached.
        """
        if not candidates:
            return []

        if not self.is_available:
            logger.warning("Local reranker unavailable: sentence-transformers not installed")
            return candidates

        try:
            if self._model is None:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(self._model_name)

            pairs = [[query, c.get("text", "")] for c in candidates]
            scores = self._model.predict(pairs)  # type: ignore[attr-defined]

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

        except Exception as e:
            logger.warning("Local reranker failed: %s", e)
            return candidates
