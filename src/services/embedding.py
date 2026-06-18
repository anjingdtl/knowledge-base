"""Embedding 服务 — 基于 OpenAI 兼容协议，支持任意供应商"""
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
        timeout = float(self._cfg("embedding.timeout", 15) or 15)
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        return self._client

    def embed(self, text: str) -> list[float]:
        result = self.embed_batch([text])
        if not result:
            raise RuntimeError("Embedding API returned no results")
        return result[0]

    def embed_batch(self, texts: list[str], batch_size: int = 20) -> list[list[float]]:
        """批量生成 embedding。

        行为细节：
        - 内部按 ``batch_size`` 切片，**并发**调用 Embedding API（用
          ``ThreadPoolExecutor``），最后按原顺序拼回结果。
        - 并发度由 ``embedding.max_concurrent_batches`` 配置控制（默认 4），
          避免打爆上游限流。
        - 移除了原实现的 ``time.sleep(0.5)`` 串行节流 — 并发模型下不再需要。
        - 任一 batch 失败立即抛 ``RuntimeError``，保留旧版失败语义。
        """
        import logging
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from openai import APIError

        if not texts:
            return []

        client = self._get_client()
        model = self._cfg("embedding.model", "")
        max_concurrent = max(1, int(self._cfg("embedding.max_concurrent_batches", 4) or 4))
        logger = logging.getLogger(__name__)

        # 把 texts 切成定长 batch，保留 batch 在原列表中的索引以便拼回结果
        batches: list[tuple[int, list[str]]] = []
        for i in range(0, len(texts), batch_size):
            batches.append((i, texts[i:i + batch_size]))

        results: list[list[list[float]] | None] = [None] * len(batches)

        def _embed_one(idx: int, batch: list[str]) -> tuple[int, list[list[float]]]:
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
            return idx, [item.embedding for item in response.data]

        # 小批量（单 batch 就能装下）直接同步走，避免线程开销
        if len(batches) == 1:
            _, embs = _embed_one(0, batches[0][1])
            return embs

        with ThreadPoolExecutor(max_workers=min(max_concurrent, len(batches))) as pool:
            futures = [pool.submit(_embed_one, idx, batch) for idx, batch in batches]
            for fut in as_completed(futures):
                idx, embs = fut.result()
                results[idx] = embs

        # 按原 batch 顺序拼成扁平结果（任一 batch 失败会在上面抛出，不会到这里）
        flat: list[list[float]] = []
        for embeddings in results:
            if embeddings is None:
                raise RuntimeError("Embedding batch completed without a result")
            flat.extend(embeddings)
        return flat

    def embed_batch_with_cache(self, texts: list[str], batch_size: int = 20) -> list[list[float]]:
        """批量生成 embedding，带 SQLite 缓存"""
        import hashlib

        from src.core.embedding_cache import EmbeddingCache

        cache = EmbeddingCache()
        model = self._cfg("embedding.model", "")

        results: list[list[float] | None] = [None] * len(texts)
        to_embed: list[tuple[int, str]] = []

        for i, text in enumerate(texts):
            content_hash = hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()
            cached = cache.get(content_hash, model)
            if cached is not None:
                results[i] = cached
            else:
                to_embed.append((i, text))

        if to_embed:
            texts_to_embed = [t for _, t in to_embed]
            embeddings = self.embed_batch(texts_to_embed, batch_size)
            for (i, text), emb in zip(to_embed, embeddings):
                content_hash = hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()
                cache.put(content_hash, model, emb)
                results[i] = emb

        if any(result is None for result in results):
            raise RuntimeError("Embedding cache batch completed without a result")
        return [result for result in results if result is not None]

    def reset_client(self):
        self._client = None
