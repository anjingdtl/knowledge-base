"""Embedding 服务 — 基于 OpenAI 兼容协议，支持任意供应商

Phase 3: 三级缓存架构 (L1 进程内 → L2 SQLite → L3 API)
"""
import hashlib
import logging
import threading
from collections import OrderedDict
from typing import Literal

from src.services.deadline import DeadlineTimeout, remaining_deadline
from src.services.provider_runtime import ProviderRequest, run_provider_operation
from src.utils.config import Config

logger = logging.getLogger(__name__)

# 标记是否已就 "Embedding API Key 缺失" 告警过一次，避免重复刷屏
_embedding_key_missing_warned = False


# ── Phase 3: L1 进程内 Embedding 缓存 ──

class _L1EmbeddingCache:
    """L1 进程内 embedding 缓存 (hash → vector)，线程安全，LRU 淘汰。"""

    def __init__(self, maxsize: int = 2048):
        self._maxsize = max(maxsize, 1)
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> list[float] | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                self._cache.move_to_end(key)
                self._hits += 1
                return entry
            self._misses += 1
            return None

    def put(self, key: str, vector: list[float]) -> None:
        with self._lock:
            self._cache[key] = vector
            self._cache.move_to_end(key)
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)


# 模块级 L1 缓存单例
_l1_cache = _L1EmbeddingCache()


class EmbeddingService:
    ISOLATION_MODE: Literal["process"] = "process"

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
        api_key = self._cfg("embedding.api_key") or self._cfg("llm.api_key", "")
        if not api_key:
            # 静默兜底为 "no-key" 会让 embedding 调用拿到模糊的 401，向量通道
            # 异常被 hybrid_search 吞掉后表现为 score_breakdown.vector 始终为 null。
            # 这里一次性告警，指明配置路径。
            global _embedding_key_missing_warned
            if not _embedding_key_missing_warned:
                logger.warning(
                    "Embedding API Key 未配置（embedding.api_key 与 llm.api_key "
                    "均为空），向量索引与语义搜索将不可用。请通过以下任一方式配置："
                    "1) GUI 设置；2) 环境变量 SHINEHE_EMBEDDING_API_KEY（或复用 "
                    "SHINEHE_LLM_API_KEY）；3) keyring。Windows Service 需在服务"
                    "账户下配置或注入系统环境变量。"
                )
                _embedding_key_missing_warned = True
            api_key = "no-key"
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

        if not texts:
            return []

        model = self._cfg("embedding.model", "")
        max_concurrent = max(1, int(self._cfg("embedding.max_concurrent_batches", 4) or 4))
        logger = logging.getLogger(__name__)

        # 把 texts 切成定长 batch
        # batch_idx 是 batch 在 batches 列表中的位置（0,1,2,...），
        # text_start 是该 batch 在原 texts 列表中的起始索引，供拼回结果时使用。
        batches: list[tuple[int, int, list[str]]] = []
        for batch_idx, start in enumerate(range(0, len(texts), batch_size)):
            batches.append((batch_idx, start, texts[start:start + batch_size]))

        results: list[list[list[float]] | None] = [None] * len(batches)

        def _embed_one(batch_idx: int, batch: list[str]) -> tuple[int, list[list[float]]]:
            try:
                provider_timeout = float(self._cfg("embedding.timeout", 15) or 15)
                remaining = remaining_deadline()
                if remaining is not None:
                    provider_timeout = min(provider_timeout, max(0.01, remaining))
                request = ProviderRequest(
                    provider_type="openai_compatible_embedding",
                    base_url=str(
                        self._cfg("embedding.base_url")
                        or self._cfg("llm.base_url")
                        or ""
                    ),
                    model=str(model or ""),
                    payload={"input": batch},
                    timeout_seconds=provider_timeout,
                    secret_env_key="SHINEHE_EMBEDDING_API_KEY",
                )
                response = run_provider_operation(
                    "embedding",
                    request,
                    isolation_mode=self.ISOLATION_MODE,
                    timeout=provider_timeout,
                )
                if not response.ok or not isinstance(response.data, list):
                    raise RuntimeError(
                        response.error_message
                        or response.error_type
                        or "Embedding provider returned invalid response"
                    )
                embeddings = [list(item) for item in response.data]
            except DeadlineTimeout:
                raise
            except Exception as e:
                logger.error(
                    "Unexpected error during embedding: model=%s, batch_size=%d, error=%s",
                    model, len(batch), e,
                )
                raise RuntimeError(
                    f"Unexpected error during embedding (model={model}, batch_size={len(batch)}): {e}"
                ) from e
            return batch_idx, embeddings

        # 小批量（单 batch 就能装下）直接同步走，避免线程开销
        if len(batches) == 1:
            _, embs = _embed_one(0, batches[0][2])
            return embs

        with ThreadPoolExecutor(max_workers=min(max_concurrent, len(batches))) as pool:
            futures = [pool.submit(_embed_one, batch_idx, batch) for batch_idx, _, batch in batches]
            for fut in as_completed(futures):
                batch_idx, embs = fut.result()
                results[batch_idx] = embs

        # 按原 batch 顺序拼成扁平结果（任一 batch 失败会在上面抛出，不会到这里）
        flat: list[list[float]] = []
        for embeddings in results:
            if embeddings is None:
                raise RuntimeError("Embedding batch completed without a result")
            flat.extend(embeddings)
        return flat

    def embed_batch_with_cache(self, texts: list[str], batch_size: int = 20) -> list[list[float]]:
        """批量生成 embedding，三级缓存链: L1(进程内) → L2(SQLite) → L3(API)"""

        l2_enabled = bool(self._cfg("rag.cache.l2_enabled", True))
        l1_max = int(self._cfg("rag.cache.l1_embedding_max", 2048) or 2048)
        l2_ttl = int(self._cfg("rag.cache.l2_ttl_hours", 168) or 168)

        # Resize L1 cache if config changed
        if l1_max != _l1_cache._maxsize:
            _l1_cache._maxsize = max(l1_max, 1)

        model = self._cfg("embedding.model", "")

        results: list[list[float] | None] = [None] * len(texts)
        to_embed: list[tuple[int, str, str]] = []

        # Pass 1: L1 cache lookup
        for i, text in enumerate(texts):
            content_hash = hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()
            cache_key = f"{content_hash}:{model}"
            cached = _l1_cache.get(cache_key)
            if cached is not None:
                results[i] = cached
            else:
                to_embed.append((i, text, content_hash))

        # Pass 2: L2 SQLite cache lookup (for L1 misses)
        l2_cache = None
        if l2_enabled and to_embed:
            try:
                from src.core.embedding_cache import EmbeddingCache
                l2_cache = EmbeddingCache(ttl_hours=l2_ttl)
            except Exception as e:
                logger.debug("L2 embedding cache unavailable: %s", e)

        still_to_embed: list[tuple[int, str, str]] = []
        if l2_cache is not None:
            for i, text, content_hash in to_embed:
                try:
                    cached = l2_cache.get(content_hash, model)
                    if cached is not None:
                        # L2 hit: promote to L1
                        cache_key = f"{content_hash}:{model}"
                        _l1_cache.put(cache_key, cached)
                        results[i] = cached
                    else:
                        still_to_embed.append((i, text, content_hash))
                except Exception:
                    # L2 read failure: treat as miss, continue to API
                    still_to_embed.append((i, text, content_hash))
        else:
            still_to_embed = to_embed

        # Pass 3: API call (for L1 + L2 misses)
        if still_to_embed:
            texts_to_embed = [t for _, t, _ in still_to_embed]
            try:
                embeddings = self.embed_batch(texts_to_embed, batch_size)
            except Exception:
                # API call failed: don't cache, propagate error
                raise

            for (i, text, content_hash), emb in zip(still_to_embed, embeddings):
                cache_key = f"{content_hash}:{model}"
                # Write to L1
                _l1_cache.put(cache_key, emb)
                # Write to L2
                if l2_cache is not None:
                    try:
                        l2_cache.put(content_hash, model, emb)
                    except Exception:
                        logger.debug("L2 embedding cache write failed (non-fatal)")
                results[i] = emb

        if any(result is None for result in results):
            raise RuntimeError("Embedding cache batch completed without a result")
        return [result for result in results if result is not None]

    def reset_client(self):
        """重置 OpenAI client。同步清空 embedding 两级缓存——切换 api_key/base_url
        可能换了向量供应商，若保留旧缓存会返回跨向量空间的脏向量（即使 model 名相同，
        新旧供应商的向量空间不兼容，余弦相似度会系统性失真）。
        """
        self._client = None
        try:
            _l1_cache.clear()
        except Exception:
            pass
        try:
            model = self._cfg("embedding.model", "")
            if model:
                from src.core.embedding_cache import EmbeddingCache
                EmbeddingCache().invalidate_model(model)
        except Exception as e:
            logger.debug("L2 embedding cache invalidation skipped: %s", e)
