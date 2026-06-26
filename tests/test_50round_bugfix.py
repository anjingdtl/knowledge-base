"""50轮 MCP 测试报告 BUG 修复回归测试

覆盖:
- Bug-1: auto_tag LLM 调用方式修复（messages list 而非字符串）
- Bug-1: EmbeddingRouter 标签不足时的 title embedding 兜底
- Bug-2: ask 工具总超时控制 + 超时返回部分结果
- 改进项3: PostProcessStage block_contexts 截断
"""
from unittest.mock import MagicMock, patch

from src.services.rag_pipeline import PostProcessStage, RagContext
from src.services.route_engine import EmbeddingRouter

# ── Bug-1: auto_tag LLM 调用方式修复 ──


class TestAutoTagLLMCallFix:
    """验证 auto_tag 工具构造标准 messages list 调用 LLM。

    50轮测试报告 Bug-1 根因: 旧实现 llm.chat(prompt) 把字符串当 messages 传，
    类型不符导致 auto_tag 必然失败，标签覆盖率停滞 3.7%。
    """

    def test_auto_tag_passes_messages_list_not_string(self):
        """auto_tag 应构造 [{"role":"user","content":...}] 而非裸字符串。"""
        import src.mcp_server as mcp_mod
        from src.services.db import Database

        # 准备 mock container + db + llm
        # 注意：mock_db 用 spec=Database 严格限制属性 —— 任何对 Database 不存在
        # 属性的访问（如早期 bug 中的 db.conn）都会立即抛 AttributeError，避免
        # MagicMock 自动生成假属性掩盖真实缺陷（C1 回归防护）。
        mock_llm = MagicMock()
        mock_llm.chat_with_usage.return_value = ('["管理办法"]', {})
        mock_db = MagicMock(spec=Database)
        mock_db._shutdown = False
        mock_db._instance = mock_db
        row = MagicMock()
        row.__getitem__ = lambda self, key: {
            "id": "k1", "title": "采购管理办法", "content": "采购流程...", "tags": "",
        }[key]
        row.keys.return_value = ["id", "title", "content", "tags"]
        # 修复后 auto_tag 用 get_conn()（而非已删除的 db.conn）；配置 mock 连接
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [row]
        mock_db.get_conn.return_value.__enter__ = lambda self: mock_conn
        mock_db.get_conn.return_value.__exit__ = lambda *a: False

        mock_container = MagicMock()
        mock_container.llm = mock_llm
        mock_container.db = mock_db

        original = mcp_mod._get_container
        original_check = mcp_mod._check_write_policy
        mcp_mod._get_container = lambda: mock_container
        mcp_mod._check_write_policy = lambda *a, **kw: None
        # patch Database._instance
        from src.services import db as db_mod
        original_instance = db_mod.Database._instance
        db_mod.Database._instance = mock_db
        try:
            result = mcp_mod.auto_tag(limit=1)
        finally:
            mcp_mod._get_container = original
            mcp_mod._check_write_policy = original_check
            db_mod.Database._instance = original_instance

        # 验证 LLM 被调用时传入的是 messages list，而非字符串
        assert mock_llm.chat_with_usage.called
        call_args = mock_llm.chat_with_usage.call_args
        first_arg = call_args.args[0] if call_args.args else call_args[0][0]
        assert isinstance(first_arg, list), \
            "auto_tag 必须传 messages list 给 llm.chat_with_usage，而非裸字符串"
        assert first_arg[0]["role"] == "user"
        assert "采购管理办法" in first_arg[0]["content"]
        assert result["ok"] is True


# ── Bug-1: EmbeddingRouter title embedding 兜底 ──


class TestEmbeddingRouterTitleFallback:
    """验证标签覆盖率不足时，L2 用标题 embedding 兜底路由。"""

    def test_title_fallback_when_tag_miss(self):
        """tag 匹配落空时，title embedding 命中高相似度标题则路由为 title contains。"""
        router = EmbeddingRouter(db=MagicMock(), similarity_threshold=0.60,
                                 title_similarity_threshold=0.70)

        # mock embedding service
        emb_service = MagicMock()
        # query embedding
        emb_service.embed.return_value = [0.9, 0.1, 0.0]
        # tag embeddings 为空（标签覆盖率 3.7% 场景）
        # title embeddings: 一个高相似度标题
        emb_service.embed_batch.return_value = [
            [0.95, 0.05, 0.0],  # 高相似度
            [0.1, 0.9, 0.0],   # 低相似度
        ]

        # mock db: 无标签，但有两个标题
        router._db.get_all_tags.return_value = []
        title_rows = [MagicMock(), MagicMock()]
        title_rows[0].__getitem__ = lambda self, k: "供应商管理办法"
        title_rows[1].__getitem__ = lambda self, k: "无关文档"
        router._db.get_conn.return_value.execute.return_value.fetchall.return_value = title_rows

        # 清空缓存
        EmbeddingRouter._TAG_EMB_CACHE = None
        EmbeddingRouter._TITLE_EMB_CACHE = None

        # EmbeddingService 在 route() 内部 from src.services.embedding import，
        # 故 patch 源模块而非 route_engine 模块。
        with patch("src.services.embedding.EmbeddingService", return_value=emb_service):
            result = router.route("供应商管理办法准入评估")

        assert result is not None
        assert result["mode"] == "structured"
        assert "title-fallback" in result["explanation"]
        spec = result["query_spec"]
        assert spec.filter_condition.type == "title"
        assert spec.filter_condition.op == "contains"

    def test_returns_none_when_both_tag_and_title_miss(self):
        """tag 和 title 都不命中时返回 None，交给 L3。"""
        router = EmbeddingRouter(db=MagicMock(), similarity_threshold=0.60,
                                 title_similarity_threshold=0.70)

        emb_service = MagicMock()
        emb_service.embed.return_value = [0.1, 0.9, 0.0]
        emb_service.embed_batch.return_value = [
            [0.9, 0.1, 0.0],  # 与 query 不相似
        ]

        router._db.get_all_tags.return_value = []
        title_rows = [MagicMock()]
        title_rows[0].__getitem__ = lambda self, k: "无关文档"
        router._db.get_conn.return_value.execute.return_value.fetchall.return_value = title_rows

        EmbeddingRouter._TAG_EMB_CACHE = None
        EmbeddingRouter._TITLE_EMB_CACHE = None

        with patch("src.services.embedding.EmbeddingService", return_value=emb_service):
            result = router.route("完全无关的查询")

        assert result is None


# ── Bug-2: ask 工具总超时控制 ──


class TestAskTimeoutControl:
    """验证 ask 工具超时返回部分结果而非触发 MCP -32001。"""

    def test_do_ask_returns_partial_result_on_timeout(self):
        """rag_pipeline.query 超时抛 TimeoutError 时，_do_ask 返回部分结果。"""
        import concurrent.futures

        import src.mcp_server as mcp_mod

        mock_container = MagicMock()
        mock_container.rag_pipeline.query.side_effect = concurrent.futures.TimeoutError()
        original = mcp_mod._get_container
        mcp_mod._get_container = lambda: mock_container
        try:
            result = mcp_mod._do_ask("供应商管理办法准入评估")
        finally:
            mcp_mod._get_container = original

        assert result["answer"] == ""
        assert result["sources"] == []
        assert result["route"]["mode"] == "timeout"
        assert any("timed out" in w for w in result["warnings"])


# ── 改进项3: PostProcessStage block_contexts 截断 ──


class TestPostProcessBlockContextTruncation:
    """验证大文档 block_contexts 被截断，避免 MCP payload >300KB。"""

    def test_block_context_truncated_when_exceeds_limit(self):
        stage = PostProcessStage()
        ctx = RagContext(question="test")
        ctx.answer = "short answer"
        ctx.sources = []
        long_block_ctx = "x" * 5000
        ctx.metadata["block_contexts"] = {"b1": long_block_ctx, "b2": "short"}

        config = {"enabled": True, "dedup": True, "block_context_max_length": 2000}
        result_ctx = asyncio_run(stage.execute(ctx, config))

        # 2000 字符 + "...(block_context 已截断)" 标注
        truncated = result_ctx.metadata["block_contexts"]["b1"]
        assert truncated.startswith("x" * 2000)
        assert "已截断" in truncated
        assert len(truncated) <= 2000 + 50  # 截断标注不超过 50 字符
        assert result_ctx.metadata["block_contexts"]["b2"] == "short"
        assert any("block_contexts_truncated" in w for w in result_ctx.metadata["warnings"])

    def test_block_context_not_truncated_when_under_limit(self):
        stage = PostProcessStage()
        ctx = RagContext(question="test")
        ctx.answer = "short answer"
        ctx.sources = []
        ctx.metadata["block_contexts"] = {"b1": "short context"}

        config = {"enabled": True, "dedup": True, "block_context_max_length": 2000}
        result_ctx = asyncio_run(stage.execute(ctx, config))

        assert result_ctx.metadata["block_contexts"]["b1"] == "short context"
        assert not any("block_contexts_truncated" in w for w in result_ctx.metadata.get("warnings", []))


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)
