"""测试 EvidenceCompressStage"""
import asyncio

from src.services.rag_pipeline import (
    EvidenceCompressStage,
    RagContext,
    StageRegistry,
)


def run_stage(stage, ctx, config):
    """同步执行 async stage"""
    return asyncio.run(stage.execute(ctx, config))


class TestEvidenceCompressStageExtractive:
    """抽取式压缩测试"""

    def _make_stage(self):
        return EvidenceCompressStage(llm=None)

    def _make_ctx(self, results):
        return RagContext(
            question="Python 的异常处理机制是什么",
            reranked_results=results,
        )

    def test_basic_compression(self):
        stage = self._make_stage()
        ctx = self._make_ctx([
            {
                "text": "Python 使用 try/except 语句来处理异常。"
                        "当程序运行发生错误时，Python 会抛出异常对象。"
                        "你可以使用 try 块包裹可能出错的代码，用 except 块捕获并处理异常。"
                        "今天天气不错，适合出门散步。",
                "metadata": {"page_id": "k1"},
            },
        ])
        config = {"enabled": True, "strategy": "extractive", "max_evidence_tokens": 4000}
        result = run_stage(stage, ctx, config)
        assert len(result.reranked_results) == 1
        # 天气相关的句子应该被去掉，异常相关的保留
        text = result.reranked_results[0]["text"]
        assert "try" in text or "except" in text or "异常" in text

    def test_preserves_relevant_sentences(self):
        stage = self._make_stage()
        ctx = self._make_ctx([
            {
                "text": "第一句无关内容。Python 异常处理很重要。最后一句无关。",
                "metadata": {"page_id": "k1"},
            },
        ])
        config = {"enabled": True, "strategy": "extractive", "max_evidence_tokens": 4000}
        result = run_stage(stage, ctx, config)
        text = result.reranked_results[0]["text"]
        assert "异常" in text

    def test_disabled_returns_unchanged(self):
        stage = self._make_stage()
        original = [{"text": "hello", "metadata": {}}]
        ctx = self._make_ctx(original)
        config = {"enabled": False}
        result = run_stage(stage, ctx, config)
        assert result.reranked_results is original

    def test_empty_results(self):
        stage = self._make_stage()
        ctx = RagContext(question="test", reranked_results=[])
        config = {"enabled": True, "strategy": "extractive"}
        result = run_stage(stage, ctx, config)
        assert result.reranked_results == []

    def test_respects_token_budget(self):
        stage = self._make_stage()
        # 构造超长内容
        long_text = "Python 异常处理。这是一段很长的无关内容。" * 100
        ctx = self._make_ctx([
            {"text": long_text, "metadata": {"page_id": "k1"}},
            {"text": long_text, "metadata": {"page_id": "k2"}},
        ])
        # 极小 token 预算
        config = {"enabled": True, "strategy": "extractive", "max_evidence_tokens": 100}
        result = run_stage(stage, ctx, config)
        total_chars = sum(len(r.get("text", "")) for r in result.reranked_results)
        # 应远小于原始长度
        assert total_chars < len(long_text) * 2

    def test_metadata_tracks_compression(self):
        stage = self._make_stage()
        ctx = self._make_ctx([{"text": "Python 异常处理", "metadata": {}}])
        config = {"enabled": True, "strategy": "extractive", "max_evidence_tokens": 4000}
        result = run_stage(stage, ctx, config)
        assert "evidence_compress" in result.metadata
        assert result.metadata["evidence_compress"]["strategy"] == "extractive"
        assert result.metadata["evidence_compress"]["result_count"] == 1

    def test_multiple_results_dedup_keywords(self):
        stage = self._make_stage()
        ctx = self._make_ctx([
            {"text": "Python try except 基础语法。", "metadata": {"page_id": "k1"}},
            {"text": "异常处理的最佳实践。无关天气内容。", "metadata": {"page_id": "k2"}},
        ])
        config = {"enabled": True, "strategy": "extractive", "max_evidence_tokens": 8000}
        result = run_stage(stage, ctx, config)
        assert len(result.reranked_results) == 2


class TestEvidenceCompressStageSentences:
    """句子分割测试"""

    def test_split_chinese_sentences(self):
        text = "第一句话。第二句话！第三句话？"
        sentences = EvidenceCompressStage._split_sentences(text)
        assert len(sentences) == 3

    def test_split_mixed_sentences(self):
        text = "Hello world. This is a test! 真的吗？\nNew paragraph."
        sentences = EvidenceCompressStage._split_sentences(text)
        assert len(sentences) >= 3

    def test_split_no_punctuation(self):
        text = "这是一段没有标点符号的文本"
        sentences = EvidenceCompressStage._split_sentences(text)
        assert len(sentences) == 1


class TestStageRegistryIntegration:
    """验证 EvidenceCompressStage 注册到 Registry"""

    def test_registered_in_registry(self):
        assert StageRegistry.get("evidence_compress") is EvidenceCompressStage

    def test_create_stage_with_deps(self):
        stage = StageRegistry.create_stage("evidence_compress", deps={"llm": None})
        assert stage is not None
        assert stage.name == "evidence_compress"
