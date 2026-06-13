"""重排序器工厂 — 根据配置创建合适的重排序器实现"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.services.rerankers.api import ApiReranker
from src.services.rerankers.base import Reranker
from src.services.rerankers.llm import LLMFallbackReranker
from src.services.rerankers.local import LocalCrossEncoderReranker

if TYPE_CHECKING:
    from src.services.llm import LLMService
    from src.utils.config import Config

logger = logging.getLogger(__name__)


class DisabledReranker:
    """No-op reranker that returns candidates unchanged."""

    def rerank(self, query: str, candidates: list[dict], top_n: int = 5) -> list[dict]:
        return candidates[:top_n]


def create_reranker(
    config: "Config | None" = None,
    llm: "LLMService | None" = None,
) -> Reranker:
    """Factory: create reranker based on config.

    Priority:
        1. reranker.enabled=false or provider=disabled -> DisabledReranker
        2. reranker.provider=local -> LocalCrossEncoderReranker (if available)
        3. reranker.provider=api + model configured -> ApiReranker
        4. reranker.provider=llm or use_llm_fallback=true -> LLMFallbackReranker
        5. reranker.model configured (auto-detect api) -> ApiReranker
        6. Default -> DisabledReranker
    """
    if config is None:
        from src.utils.config import Config as _Config

        config = _Config  # type: ignore[assignment]

    assert config is not None

    enabled = config.get("reranker.enabled", True)
    if not enabled:
        logger.info("Reranker disabled by config (reranker.enabled=false)")
        return DisabledReranker()

    provider = config.get("reranker.provider", "")

    # Explicit disabled
    if provider == "disabled":
        logger.info("Reranker disabled by config (provider=disabled)")
        return DisabledReranker()

    # Local cross-encoder
    if provider == "local":
        model_name = config.get("reranker.model", "BAAI/bge-reranker-v2-m3")
        reranker = LocalCrossEncoderReranker(model_name=model_name, config=config)
        if reranker.is_available:
            logger.info("Using local cross-encoder reranker: %s", model_name)
            return reranker
        else:
            logger.warning(
                "Local reranker requested but sentence-transformers not installed, "
                "falling back to next available provider"
            )
            # Fall through to next available provider

    # API reranker
    if provider == "api" or (provider == "" and config.get("reranker.model", "")):
        model = config.get("reranker.model", "")
        base_url = config.get("reranker.base_url", "")
        api_key = config.get("reranker.api_key", "")

        # Fallback: 复用 embedding 配置
        if not base_url:
            base_url = config.get("embedding.base_url", "")
        if not api_key:
            api_key = config.get("embedding.api_key", "")

        if model and base_url and api_key:
            timeout = config.get("reranker.timeout", 20)
            logger.info("Using API reranker: model=%s, base_url=%s", model, base_url)
            return ApiReranker(
                base_url=base_url,
                model=model,
                api_key=api_key,
                config=config,
                timeout=float(timeout),
            )

        if provider == "api":
            logger.warning(
                "API reranker requested but model/base_url/api_key not fully configured"
            )

    # LLM fallback
    use_llm_fallback = config.get("reranker.use_llm_fallback", True)
    if provider == "llm" or use_llm_fallback:
        if llm is not None:
            logger.info("Using LLM fallback reranker")
            return LLMFallbackReranker(llm=llm, config=config)
        else:
            logger.warning("LLM fallback requested but no LLM service provided")

    # Default: disabled
    logger.info("No reranker configured, using DisabledReranker")
    return DisabledReranker()
