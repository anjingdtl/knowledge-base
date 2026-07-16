"""Reranker provider 单元测试 — 覆盖 ApiReranker, LocalCrossEncoderReranker, LLMFallbackReranker, DisabledReranker, Factory"""
from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from src.services.deadline import DeadlineTimeout
from src.services.provider_runtime import ProviderResponse
from src.services.reranker import LLMReranker, get_reranker
from src.services.rerankers.api import ApiReranker
from src.services.rerankers.base import Reranker
from src.services.rerankers.factory import DisabledReranker, create_reranker
from src.services.rerankers.llm import LLMFallbackReranker
from src.services.rerankers.local import LocalCrossEncoderReranker


def _make_candidates(n=5):
    """Create n dummy candidates."""
    return [{"id": f"block-{i}", "text": f"这是第 {i} 个候选文本片段"} for i in range(n)]


# ── TestApiReranker ────────────────────────────────────────────────────────────


class TestApiReranker:
    """API reranker with mock HTTP calls."""

    def test_rerank_with_mock_api(self, monkeypatch):
        """API reranker correctly parses mock response and sorts by score."""
        provider_data = {
            "results": [
                {"index": 0, "relevance_score": 0.3},
                {"index": 1, "relevance_score": 0.9},
                {"index": 2, "relevance_score": 0.6},
            ]
        }
        monkeypatch.setattr(
            "src.services.rerankers.api.run_provider_operation",
            lambda *args, **kwargs: ProviderResponse(ok=True, data=provider_data),
        )

        config = Mock()
        config.get.return_value = 0.1  # min_score

        reranker = ApiReranker(
            base_url="https://api.test.com/v1",
            model="test-reranker",
            api_key="fake-key",
            config=config,
        )

        candidates = _make_candidates(3)
        result = reranker.rerank("查询", candidates, top_n=3)

        assert len(result) == 3
        # Should be sorted by score descending: 0.9, 0.6, 0.3
        assert result[0]["rerank_score"] == 0.9
        assert result[1]["rerank_score"] == 0.6
        assert result[2]["rerank_score"] == 0.3

    def test_api_failure_returns_original(self, monkeypatch):
        """API failure returns original candidates with warning."""
        monkeypatch.setattr(
            "src.services.rerankers.api.run_provider_operation",
            lambda *args, **kwargs: ProviderResponse(
                ok=False, error_type="HTTPStatusError", error_message="fail"
            ),
        )

        reranker = ApiReranker(
            base_url="https://api.test.com/v1",
            model="test-reranker",
            api_key="fake-key",
        )

        candidates = _make_candidates(3)
        original = list(candidates)
        result = reranker.rerank("查询", candidates, top_n=3)

        assert len(result) == 3
        # Should return original candidates unchanged
        assert result == original

    def test_timeout_returns_original(self, monkeypatch):
        """A terminable provider timeout remains visible to the caller."""
        def timeout(*args, **kwargs):
            raise DeadlineTimeout(
                "timeout",
                cancelled=True,
                background_work_may_continue=False,
                worker_terminated=True,
                provider_operation="reranker",
            )

        monkeypatch.setattr("src.services.rerankers.api.run_provider_operation", timeout)

        reranker = ApiReranker(
            base_url="https://api.test.com/v1",
            model="test-reranker",
            api_key="fake-key",
        )

        candidates = _make_candidates(3)
        with pytest.raises(DeadlineTimeout):
            reranker.rerank("查询", candidates, top_n=3)

    def test_empty_candidates(self):
        """Empty candidate list returns empty list."""
        reranker = ApiReranker(
            base_url="https://api.test.com/v1",
            model="test-reranker",
            api_key="fake-key",
        )
        assert reranker.rerank("查询", []) == []

    def test_min_score_filter(self, monkeypatch):
        """Candidates below min_score are filtered out."""
        provider_data = {
            "results": [
                {"index": 0, "relevance_score": 0.1},
                {"index": 1, "relevance_score": 0.9},
                {"index": 2, "relevance_score": 0.05},
            ]
        }
        monkeypatch.setattr(
            "src.services.rerankers.api.run_provider_operation",
            lambda *args, **kwargs: ProviderResponse(ok=True, data=provider_data),
        )

        config = Mock()
        config.get.return_value = 0.3  # min_score = 0.3

        reranker = ApiReranker(
            base_url="https://api.test.com/v1",
            model="test-reranker",
            api_key="fake-key",
            config=config,
        )

        candidates = _make_candidates(3)
        result = reranker.rerank("查询", candidates, top_n=3)

        # Only score=0.9 should pass the 0.3 threshold
        assert len(result) == 1
        assert result[0]["rerank_score"] == 0.9


# ── TestLocalReranker ──────────────────────────────────────────────────────────


class TestLocalReranker:
    """Local cross-encoder reranker tests."""

    def test_unavailable_without_package(self):
        """Local reranker reports unavailable when sentence-transformers not installed."""
        reranker = LocalCrossEncoderReranker()

        # Mock importlib to simulate missing package
        with patch("src.services.rerankers.local.importlib") as mock_importlib:
            mock_importlib.util.find_spec.return_value = None
            reranker._available = None  # Reset cache
            assert reranker.is_available is False

    def test_rerank_with_fake_model(self, monkeypatch):
        """Local reranker with mocked CrossEncoder."""
        # Create mock sentence_transformers module
        import types

        mock_st = types.ModuleType("sentence_transformers")

        class MockCrossEncoder:
            def __init__(self, model_name):
                self.model_name = model_name

            def predict(self, pairs):
                # Return descending scores based on text length
                return [0.1 * (i + 1) for i in range(len(pairs))]

        mock_st.CrossEncoder = MockCrossEncoder

        # Mock importlib.util.find_spec to return truthy
        with patch("src.services.rerankers.local.importlib") as mock_importlib:
            mock_importlib.util.find_spec.return_value = True

            # Mock the import inside rerank
            import sys

            monkeypatch.setitem(sys.modules, "sentence_transformers", mock_st)

            config = Mock()
            config.get.return_value = 0.0  # low min_score so all pass

            reranker = LocalCrossEncoderReranker(config=config)
            reranker._available = True  # Force available
            monkeypatch.setattr(
                "src.services.rerankers.local.run_provider_operation",
                lambda *args, **kwargs: ProviderResponse(ok=True, data=[0.1, 0.2, 0.3]),
            )

            candidates = _make_candidates(3)
            result = reranker.rerank("查询", candidates, top_n=3)

            assert len(result) == 3
            # Should be sorted by score descending
            assert result[0]["rerank_score"] > result[1]["rerank_score"]

    def test_empty_candidates(self):
        """Empty candidate list returns empty list."""
        reranker = LocalCrossEncoderReranker()
        assert reranker.rerank("查询", []) == []


# ── TestLLMFallbackReranker ────────────────────────────────────────────────────


class TestLLMFallbackReranker:
    """LLM fallback reranker tests."""

    def test_llm_scoring(self):
        """LLM fallback produces scores from mock response."""
        mock_llm = Mock()
        mock_llm.chat.return_value = "0:0.9\n1:0.5\n2:0.2"

        config = Mock()
        config.get.return_value = 0.0  # low min_score

        reranker = LLMFallbackReranker(llm=mock_llm, config=config)
        candidates = _make_candidates(3)
        result = reranker.rerank("查询", candidates, top_n=3)

        assert len(result) == 3
        # Verify scores were assigned
        scores = sorted([c["rerank_score"] for c in result], reverse=True)
        assert scores[0] == pytest.approx(0.9)
        assert scores[1] == pytest.approx(0.5)
        assert scores[2] == pytest.approx(0.2)

    def test_llm_failure_returns_original(self):
        """LLM failure returns original candidates."""
        mock_llm = Mock()
        mock_llm.chat.side_effect = Exception("LLM unavailable")

        config = Mock()
        config.get.return_value = 0.3

        reranker = LLMFallbackReranker(llm=mock_llm, config=config)
        candidates = _make_candidates(3)
        result = reranker.rerank("查询", candidates, top_n=3)

        # Should return all candidates (with default 0.5 scores from exception handler)
        assert len(result) == 3

    def test_no_llm_returns_default_scores(self):
        """No LLM provided returns candidates with default scores."""
        reranker = LLMFallbackReranker(llm=None, config=Mock())
        reranker._config = Mock()
        reranker._config.get.return_value = 0.0  # low min_score

        candidates = _make_candidates(3)
        result = reranker.rerank("查询", candidates, top_n=3)

        assert len(result) == 3
        # All should have default 0.5 score
        for c in result:
            assert c["rerank_score"] == pytest.approx(0.5)

    def test_parse_scores_various_formats(self):
        """_parse_scores handles various output formats."""
        # Standard format
        assert LLMFallbackReranker._parse_scores("0:0.9\n1:0.5\n2:0.2", 3) == [0.9, 0.5, 0.2]

        # Chinese colon
        assert LLMFallbackReranker._parse_scores("0：0.8\n1：0.6", 2) == [0.8, 0.6]

        # Comma separated
        assert LLMFallbackReranker._parse_scores("0:0.7, 1:0.3", 2) == [0.7, 0.3]

        # Fewer scores than expected -> pad with 0.5
        assert LLMFallbackReranker._parse_scores("0:0.9", 3) == [0.9, 0.5, 0.5]

        # More scores than expected -> truncate
        assert LLMFallbackReranker._parse_scores("0:0.9\n1:0.8\n2:0.7", 2) == [0.9, 0.8]

    def test_empty_candidates(self):
        """Empty candidate list returns empty list."""
        reranker = LLMFallbackReranker(llm=Mock(), config=Mock())
        assert reranker.rerank("查询", []) == []


# ── TestDisabledReranker ───────────────────────────────────────────────────────


class TestDisabledReranker:
    """Disabled reranker tests."""

    def test_returns_unchanged(self):
        """Disabled reranker returns candidates as-is, truncated to top_n."""
        reranker = DisabledReranker()
        candidates = _make_candidates(5)
        result = reranker.rerank("查询", candidates, top_n=3)

        assert len(result) == 3
        # Should be first 3 in original order
        assert result[0]["id"] == "block-0"
        assert result[1]["id"] == "block-1"
        assert result[2]["id"] == "block-2"

    def test_satisfies_protocol(self):
        """DisabledReranker satisfies Reranker protocol."""
        assert isinstance(DisabledReranker(), Reranker)

    def test_empty_candidates(self):
        """Empty candidate list returns empty list."""
        reranker = DisabledReranker()
        assert reranker.rerank("查询", []) == []


# ── TestFactory ────────────────────────────────────────────────────────────────


class TestFactory:
    """Factory function tests."""

    def test_disabled_when_not_enabled(self):
        """Factory returns DisabledReranker when enabled=false."""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "reranker.enabled": False,
        }.get(key, default)

        result = create_reranker(config=config)
        assert isinstance(result, DisabledReranker)

    def test_disabled_when_provider_disabled(self):
        """Factory returns DisabledReranker when provider=disabled."""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "reranker.enabled": True,
            "reranker.provider": "disabled",
        }.get(key, default)

        result = create_reranker(config=config)
        assert isinstance(result, DisabledReranker)

    def test_api_when_model_configured(self):
        """Factory returns ApiReranker when model + base_url + api_key set."""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "reranker.enabled": True,
            "reranker.provider": "api",
            "reranker.model": "test-model",
            "reranker.base_url": "https://api.test.com",
            "reranker.api_key": "test-key",
            "reranker.timeout": 20,
        }.get(key, default)

        result = create_reranker(config=config)
        assert isinstance(result, ApiReranker)

    def test_local_provider(self):
        """Factory returns LocalCrossEncoderReranker for provider=local (when available)."""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "reranker.enabled": True,
            "reranker.provider": "local",
            "reranker.model": "BAAI/bge-reranker-v2-m3",
        }.get(key, default)

        with patch.object(LocalCrossEncoderReranker, "is_available", new_callable=lambda: property(lambda self: True)):
            result = create_reranker(config=config)
            assert isinstance(result, LocalCrossEncoderReranker)

    def test_local_falls_back_when_unavailable(self):
        """Factory falls back when local provider is unavailable."""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "reranker.enabled": True,
            "reranker.provider": "local",
            "reranker.model": "BAAI/bge-reranker-v2-m3",
            "reranker.use_llm_fallback": True,
        }.get(key, default)

        mock_llm = Mock()

        with patch.object(LocalCrossEncoderReranker, "is_available", new_callable=lambda: property(lambda self: False)):
            result = create_reranker(config=config, llm=mock_llm)
            # Should fall back to LLM since local is unavailable
            assert isinstance(result, LLMFallbackReranker)

    def test_llm_fallback(self):
        """Factory returns LLMFallbackReranker when use_llm_fallback=true."""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "reranker.enabled": True,
            "reranker.provider": "",
            "reranker.model": "",
            "reranker.use_llm_fallback": True,
        }.get(key, default)

        mock_llm = Mock()
        result = create_reranker(config=config, llm=mock_llm)
        assert isinstance(result, LLMFallbackReranker)

    def test_default_disabled(self):
        """Factory returns DisabledReranker with empty config."""
        config = Mock()
        config.get.return_value = None
        # Override specific keys
        config.get.side_effect = lambda key, default=None: {
            "reranker.enabled": True,
            "reranker.provider": "",
            "reranker.model": "",
            "reranker.use_llm_fallback": False,
        }.get(key, default)

        result = create_reranker(config=config)
        assert isinstance(result, DisabledReranker)

    def test_auto_detect_api_from_model(self):
        """Factory auto-detects API reranker when model + base_url + api_key present (no explicit provider)."""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "reranker.enabled": True,
            "reranker.provider": "",
            "reranker.model": "BAAI/bge-reranker-v2-m3",
            "reranker.base_url": "https://api.siliconflow.cn/v1",
            "reranker.api_key": "my-key",
            "reranker.timeout": 20,
        }.get(key, default)

        result = create_reranker(config=config)
        assert isinstance(result, ApiReranker)


# ── TestBackwardCompat ─────────────────────────────────────────────────────────


class TestBackwardCompat:
    """Backward compatibility tests for the legacy LLMReranker class."""

    def test_legacy_llm_reranker_works(self):
        """Old LLMReranker class still works via wrapper."""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "reranker.enabled": True,
            "reranker.provider": "",
            "reranker.model": "",
            "reranker.use_llm_fallback": True,
            "rag.rerank.min_score": 0.0,
        }.get(key, default)

        mock_llm = Mock()
        mock_llm.chat.return_value = "0:0.9\n1:0.5\n2:0.2"

        reranker = LLMReranker(llm=mock_llm, config=config)
        candidates = _make_candidates(3)
        result = reranker.rerank("查询", candidates, top_n=3)

        assert len(result) == 3
        assert "rerank_score" in result[0]

    def test_legacy_get_reranker(self):
        """get_reranker() still returns an LLMReranker instance."""
        # Reset the singleton for clean test
        import src.services.reranker as reranker_mod

        reranker_mod._reranker_instance = None

        with patch.object(reranker_mod, "create_reranker", return_value=DisabledReranker()):
            result = get_reranker()
            assert isinstance(result, LLMReranker)

        # Clean up
        reranker_mod._reranker_instance = None

    def test_legacy_disabled_reranker(self):
        """Legacy LLMReranker with disabled config returns candidates as-is."""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "reranker.enabled": False,
        }.get(key, default)

        reranker = LLMReranker(config=config)
        candidates = _make_candidates(5)
        result = reranker.rerank("查询", candidates, top_n=3)

        # Disabled reranker returns first top_n candidates unchanged
        assert len(result) == 3


# ── TestProtocolSatisfaction ───────────────────────────────────────────────────


class TestProtocolSatisfaction:
    """All reranker implementations satisfy the Reranker protocol."""

    def test_api_satisfies_protocol(self):
        assert isinstance(
            ApiReranker(base_url="x", model="y", api_key="z"), Reranker
        )

    def test_local_satisfies_protocol(self):
        assert isinstance(LocalCrossEncoderReranker(), Reranker)

    def test_llm_satisfies_protocol(self):
        assert isinstance(LLMFallbackReranker(), Reranker)

    def test_disabled_satisfies_protocol(self):
        assert isinstance(DisabledReranker(), Reranker)
