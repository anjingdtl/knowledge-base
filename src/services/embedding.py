"""Embedding 服务 — 基于 OpenAI 兼容协议，支持任意供应商"""
import time
from src.utils.config import Config


class EmbeddingService:
    def __init__(self, config=None):
        """初始化 Embedding 服务

        Args:
            config: Config 实例（DI 注入），为 None 时回退到全局单例（兼容旧代码）
        """
        self._config = config
        self._client = None

    def _cfg(self, key: str, default=None):
        """读取配置，优先使用注入的 config，回退到全局单例"""
        if self._config is not None:
            return self._config.get(key, default)
        return Config.get(key, default)

    def _get_client(self):
        if self._client is not None:
            return self._client
        from openai import OpenAI
        api_key = self._cfg("embedding.api_key") or self._cfg("llm.api_key", "") or "no-key"
        base_url = self._cfg("embedding.base_url") or self._cfg("llm.base_url") or None
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=30)
        return self._client

    def embed(self, text: str) -> list[float]:
        result = self.embed_batch([text])
        if not result:
            raise RuntimeError("Embedding API returned no results")
        return result[0]

    def embed_batch(self, texts: list[str], batch_size: int = 20) -> list[list[float]]:
        import logging
        from openai import APIError
        client = self._get_client()
        model = self._cfg("embedding.model", "")
        logger = logging.getLogger(__name__)
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            try:
                response = client.embeddings.create(input=batch, model=model)
            except APIError as e:
                logger.error(
                    "Embedding API call failed: model=%s, batch_size=%d, error=%s",
                    model, len(batch), e,
                )
                raise RuntimeError(
                    f"Embedding API call failed (model={model}, batch_size={len(batch)}): {e}"
                ) from e
            except Exception as e:
                logger.error(
                    "Unexpected error during embedding: model=%s, batch_size=%d, error=%s",
                    model, len(batch), e,
                )
                raise RuntimeError(
                    f"Unexpected error during embedding (model={model}, batch_size={len(batch)}): {e}"
                ) from e
            for item in response.data:
                all_embeddings.append(item.embedding)
            if i + batch_size < len(texts):
                time.sleep(0.5)
        return all_embeddings

    def reset_client(self):
        self._client = None
