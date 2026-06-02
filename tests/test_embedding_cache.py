"""EmbeddingCache 集成测试 — 验证缓存接入 embedding 管线"""
import pytest
from unittest.mock import MagicMock, patch
from src.services.embedding import EmbeddingService
from src.core.embedding_cache import EmbeddingCache
from src.services.db import Database


class TestEmbeddingCacheIntegration:
    def test_cache_hit_returns_cached_embedding(self):
        """缓存命中返回缓存值，不调用 API"""
        svc = EmbeddingService()
        cache = EmbeddingCache()

        import hashlib
        text = "缓存测试文本"
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        model = svc._cfg("embedding.model", "")
        cached_vec = [0.5] * 1024
        cache.put(content_hash, model, cached_vec)

        with patch.object(svc, '_get_client') as mock_client:
            result = svc.embed_batch_with_cache([text])
            mock_client.assert_not_called()

        assert len(result) == 1
        assert result[0] == cached_vec

    def test_cache_miss_calls_api_and_caches(self):
        """缓存未命中调用 API 并写入缓存"""
        svc = EmbeddingService()

        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.7] * 1024)]

        with patch.object(svc, '_get_client') as mock_client:
            mock_client.return_value.embeddings.create.return_value = mock_response
            result = svc.embed_batch_with_cache(["未缓存的文本XYZ"])

        assert len(result) == 1
        assert result[0] == pytest.approx([0.7] * 1024, abs=1e-5)

        cache = EmbeddingCache()
        import hashlib
        content_hash = hashlib.sha256("未缓存的文本XYZ".encode()).hexdigest()
        model = svc._cfg("embedding.model", "")
        cached = cache.get(content_hash, model)
        assert cached == pytest.approx([0.7] * 1024, abs=1e-5)

    def test_cache_invalidation_by_model(self):
        """切换模型时缓存失效"""
        cache = EmbeddingCache()
        import hashlib
        text = "模型切换测试"
        content_hash = hashlib.sha256(text.encode()).hexdigest()

        cache.put(content_hash, "model-a", [0.1] * 1024)
        cache.put(content_hash, "model-b", [0.2] * 1024)

        assert cache.get(content_hash, "model-a") == pytest.approx([0.1] * 1024, abs=1e-5)
        assert cache.get(content_hash, "model-b") == pytest.approx([0.2] * 1024, abs=1e-5)

        deleted = cache.invalidate_model("model-a")
        assert deleted == 1
        assert cache.get(content_hash, "model-a") is None
        assert cache.get(content_hash, "model-b") == pytest.approx([0.2] * 1024, abs=1e-5)
