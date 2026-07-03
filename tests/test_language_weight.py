"""RRF keyword 权重按语种选择（W3 Task 3.3）。

窄目标：验证权重选择路径不抛 + detect_query_language 被纳入。
精确数值验证留给 Task 3.5 集成测试（真实候选 + score_breakdown）。
"""
from unittest.mock import MagicMock, patch

import pytest

from src.services.hybrid_search import HybridSearcher


def _make_searcher(config_dict: dict | None = None) -> HybridSearcher:
    """构造 HybridSearcher，mock 掉所有外部依赖。"""
    cfg = config_dict or {}

    # 用可追踪调用记录的 mock 替代 raw dict
    config_mock = MagicMock()
    config_mock.get = lambda k, d=None: cfg.get(k, d) if isinstance(cfg, dict) else d

    db = MagicMock()
    block_store = MagicMock()

    searcher = HybridSearcher(db=db, block_store=block_store, config=config_mock)

    # mock 掉子方法让 _blend_search 不碰真实 DB / 向量索引
    searcher._keyword_search = MagicMock(return_value=[])
    searcher._vector_search = MagicMock(return_value=([], []))
    return searcher


class TestLanguageAwareWeight:
    """detect_query_language 被调用且权重选择路径不抛异常。"""

    @patch("src.services.hybrid_search.detect_query_language")
    def test_zh_query_selects_zh_weight_key(self, mock_lang):
        """中文 query → _get_config 被请求 rrf_weight_keyword_zh。"""
        mock_lang.return_value = "zh"
        config_mock = MagicMock()
        config_mock.get = MagicMock(side_effect=lambda k, d=None: d)
        searcher = HybridSearcher(
            db=MagicMock(), block_store=MagicMock(), config=config_mock,
        )
        searcher._keyword_search = MagicMock(return_value=[])
        searcher._vector_search = MagicMock(return_value=([], []))

        searcher._blend_search(["知识库是什么"], top_k=5)

        # 验证 _get_config 被调用过 zh 权重键
        get_calls = [c[0][0] for c in config_mock.get.call_args_list]
        assert any("rrf_weight_keyword_zh" in c for c in get_calls), (
            f"Expected rrf_weight_keyword_zh in config calls, got: {get_calls}"
        )
        # 且不被调用旧键 rrf_weight_keyword
        assert not any(c == "rag.rrf_weight_keyword" for c in get_calls), (
            f"Old key rag.rrf_weight_keyword should NOT be used, got: {get_calls}"
        )

    @patch("src.services.hybrid_search.detect_query_language")
    def test_en_query_selects_en_weight_key(self, mock_lang):
        """英文 query → _get_config 被请求 rrf_weight_keyword_en。"""
        mock_lang.return_value = "en"
        config_mock = MagicMock()
        config_mock.get = MagicMock(side_effect=lambda k, d=None: d)
        searcher = HybridSearcher(
            db=MagicMock(), block_store=MagicMock(), config=config_mock,
        )
        searcher._keyword_search = MagicMock(return_value=[])
        searcher._vector_search = MagicMock(return_value=([], []))

        searcher._blend_search(["what is RAG"], top_k=5)

        get_calls = [c[0][0] for c in config_mock.get.call_args_list]
        assert any("rrf_weight_keyword_en" in c for c in get_calls), (
            f"Expected rrf_weight_keyword_en in config calls, got: {get_calls}"
        )
        assert not any(c == "rag.rrf_weight_keyword" for c in get_calls)

    @patch("src.services.hybrid_search.detect_query_language")
    def test_detect_called_with_queries_zero(self, mock_lang):
        """detect_query_language 应被调用且使用 queries[0]。"""
        mock_lang.return_value = "zh"
        config_mock = MagicMock()
        config_mock.get = MagicMock(side_effect=lambda k, d=None: d)
        searcher = HybridSearcher(
            db=MagicMock(), block_store=MagicMock(), config=config_mock,
        )
        searcher._keyword_search = MagicMock(return_value=[])
        searcher._vector_search = MagicMock(return_value=([], []))

        searcher._blend_search(["原始查询"], top_k=5)

        mock_lang.assert_called_once_with("原始查询")


class TestLegacyConfigSafe:
    """无 _zh/_en 配置键时使用默认值，不抛异常。"""

    def test_legacy_config_no_raise_zh(self):
        """config 为空 dict → zh query 不抛。"""
        searcher = _make_searcher({})
        searcher._blend_search(["知识库"], top_k=5)

    def test_legacy_config_no_raise_en(self):
        """config 为空 dict → en query 不抛。"""
        searcher = _make_searcher({})
        searcher._blend_search(["what is RAG"], top_k=5)

    def test_empty_queries_no_raise(self):
        """queries 为空列表 → 不抛。"""
        searcher = _make_searcher({})
        searcher._blend_search([], top_k=5)
