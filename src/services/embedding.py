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

    def build_embedding_text(self, block: dict | str | None) -> str:
        """Build text for embedding without mutating stored block content."""
        if not block:
            return ""
        if isinstance(block, str):
            from src.services.db import Database
            block = Database.get_block(block)
        if not block:
            return ""

        content = (block.get("content") or "").strip()
        if not bool(self._cfg("rag.embedding_context.enabled", False)):
            return content

        include_parent = bool(self._cfg("rag.embedding_context.include_parent_chain", True))
        include_links = bool(self._cfg("rag.embedding_context.include_links", True))
        include_siblings = bool(self._cfg("rag.embedding_context.include_siblings", False))
        max_chars = int(self._cfg("rag.embedding_context.max_chars", 1200) or 1200)
        max_depth = int(self._cfg("rag.context_trace_depth", 3) or 3) if include_parent else 0
        sibling_window = int(self._cfg("rag.context_sibling_window", 1) or 1) if include_siblings else 0
        max_links = int(self._cfg("rag.link_expansion.max_links", 3) or 3) if include_links else 0

        from src.services.block_context import BlockContextService
        text = BlockContextService(config=self._config or Config).build_context(
            block["id"],
            max_depth=max_depth,
            sibling_window=sibling_window,
            max_links=max_links,
        ).strip() or content
        if max_chars > 0 and len(text) > max_chars:
            return text[:max_chars]
        return text

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

    def embed_batch_with_cache(self, texts: list[str], batch_size: int = 20) -> list[list[float]]:
        """批量生成 embedding，带 SQLite 缓存"""
        import hashlib
        from src.core.embedding_cache import EmbeddingCache

        cache = EmbeddingCache()
        model = self._cfg("embedding.model", "")

        results = [None] * len(texts)
        to_embed = []

        for i, text in enumerate(texts):
            content_hash = hashlib.sha256(text.encode()).hexdigest()
            cached = cache.get(content_hash, model)
            if cached is not None:
                results[i] = cached
            else:
                to_embed.append((i, text))

        if to_embed:
            texts_to_embed = [t for _, t in to_embed]
            embeddings = self.embed_batch(texts_to_embed, batch_size)
            for (i, text), emb in zip(to_embed, embeddings):
                content_hash = hashlib.sha256(text.encode()).hexdigest()
                cache.put(content_hash, model, emb)
                results[i] = emb

        return results

    def reset_client(self):
        self._client = None
