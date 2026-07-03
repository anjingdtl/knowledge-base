"""大规模升级回归 Review — 修复回归测试。

锁定本轮 review 修复的真实缺陷,防止退化。每条测试对应计划文档
(docs/superpowers/plans/2026-07-03-knowledge-base-upgrade-regression-review.md)
中的一个 fix 项。
"""
import asyncio

import pytest

from src.services.rag_pipeline import (
    RagContext,
    RAGService,
    VectorSearchStage,
    _RAGResultCache,
)
from src.services.lexical_zh import LexicalZh


# ---------------------------------------------------------------------------
# S1.2 — LRU 缓存深拷贝隔离(防调用方 mutate 嵌套结构污染缓存)
# ---------------------------------------------------------------------------

def test_rag_cache_deepcopy_isolation():
    """get 返回深拷贝:调用方改嵌套结构不得污染缓存。"""
    cache = _RAGResultCache(maxsize=4, ttl=600)
    payload = {"answer": "A", "sources": [{"id": "s1"}], "warnings": []}
    cache.put("q1", payload)

    got = cache.get("q1")
    assert got is not None
    # 调用方 mutate 返回值的嵌套结构
    got["sources"].append({"id": "s2"})
    got["warnings"].append("polluted")
    got["answer"] = "B"

    # 缓存里的内容必须保持原样
    again = cache.get("q1")
    assert again["answer"] == "A"
    assert again["sources"] == [{"id": "s1"}]
    assert again["warnings"] == []


def test_rag_cache_put_then_mutate_original_does_not_pollute():
    """put 后调用方继续 mutate 原 dict 引用也不得污染缓存。"""
    cache = _RAGResultCache(maxsize=4, ttl=600)
    payload = {"answer": "A", "sources": [{"id": "s1"}]}
    cache.put("q1", payload)
    # 保留原引用并 mutate
    payload["sources"].append({"id": "polluted"})
    payload["answer"] = "X"

    got = cache.get("q1")
    assert got["answer"] == "A"
    assert got["sources"] == [{"id": "s1"}]


# ---------------------------------------------------------------------------
# S1.5 — lexical_zh 词边界匹配(防 Latin 子串假阳性污染 FTS 召回)
# ---------------------------------------------------------------------------

def _lexical_with_synonyms(synonyms):
    lx = LexicalZh(config={"rag": {"lexical_zh": {"enabled": True}}})
    lx._synonyms = dict(synonyms)
    return lx


def test_lexical_latin_word_not_matched_as_substring():
    """「AI」不应匹配进「available」(子串假阳性)。"""
    lx = _lexical_with_synonyms({"AI": ["人工智能"]})
    # 旧实现 ``"AI" in "available"`` 为 True → 误注入;修复后必须不扩展
    assert lx.expand_query("available options report") == "available options report"


def test_lexical_latin_word_matched_as_standalone():
    """「AI」作为独立词出现时应扩展同义词。"""
    lx = _lexical_with_synonyms({"AI": ["人工智能"]})
    out = lx.expand_query("AI 是什么")
    assert "人工智能" in out


def test_lexical_latin_word_adjacent_to_cjk_matches():
    """「FTTR是什么」中 FTTR 紧邻 CJK,仍应命中(Latin 词非被 Latin 字母数字包裹)。"""
    lx = _lexical_with_synonyms({"FTTR": ["光纤到房间"]})
    out = lx.expand_query("FTTR是什么")
    assert "光纤到房间" in out


def test_lexical_cjk_word_substring_match():
    """CJK 词保持子串匹配(无词边界)。"""
    lx = _lexical_with_synonyms({"创智杯": ["比赛"]})
    out = lx.expand_query("关于创智杯通知的说明")
    assert "比赛" in out


# ---------------------------------------------------------------------------
# S1.1 — blend_fusion 失败时保留 hybrid 候选(不丢全部候选)
# ---------------------------------------------------------------------------

class _FakeSearcher:
    def __init__(self, results):
        self._results = results

    def search(self, queries, top_k):
        return [dict(r) for r in self._results]


def test_blend_fusion_failure_preserves_hybrid_candidates(monkeypatch):
    """blend_fusion 抖动抛异常时,已算出的 hybrid 候选必须保留,不能被外层 except 清空。"""
    from src.services import agentic_router, blend_fusion as bf_mod

    # 让 agentic 路由直接失败 → 内层 except → 走 hybrid 检索路径(确定性、无 LLM)
    monkeypatch.setattr(
        agentic_router.AgenticRouter, "route",
        lambda self, q: (_ for _ in ()).throw(RuntimeError("no llm in test")),
    )
    # 让 blend_fusion 抛异常(模拟 fusion 抖动)
    def _boom(wiki, hybrid):
        raise RuntimeError("blend fusion boom")
    monkeypatch.setattr(bf_mod, "blend_fusion", _boom)

    hybrid_results = [{
        "id": "b1", "text": "hybrid hit",
        "metadata": {"page_id": "p1"}, "rrf_score": 0.9,
    }]
    stage = VectorSearchStage(db=None, hybrid_search=_FakeSearcher(hybrid_results), llm=None)

    ctx = RagContext(question="测试查询", rewritten_queries=["测试查询"])
    ctx.metadata["scale"] = "blend"
    ctx.metadata["_blend_wiki_candidates"] = [
        {"id": "wiki:src:x", "text": "wiki", "metadata": {"page_id": "w1"}},
    ]

    result = asyncio.run(stage.execute(ctx, {"enabled": True, "top_k": 5}))

    # 关键断言:候选保留(hybrid 命中),未因 fusion 失败被清空
    assert len(result.candidates) >= 1
    assert result.candidates[0]["text"] == "hybrid hit"
    # 警告标记 fusion 失败
    assert any("blend_fusion_failed" in w for w in result.metadata.get("warnings", []))


# ---------------------------------------------------------------------------
# S1.4 — query() 管线异常向上传播(不再盲目 fallback _direct_query 二次调 LLM)
# ---------------------------------------------------------------------------

class _ThrowingPipeline:
    async def execute(self, question, conversation_history=None):
        raise RuntimeError("pipeline stage boom")


def test_query_propagates_pipeline_exception(monkeypatch):
    """管线抛非超时异常时,query() 必须向上传播,不调 _direct_query。"""
    service = RAGService(deps={})
    service._pipeline = _ThrowingPipeline()

    # 若退化回 _direct_query,这里会触发 AssertionError
    monkeypatch.setattr(
        service, "_direct_query",
        lambda *a, **kw: pytest.fail("query() must not fall back to _direct_query"),
    )

    with pytest.raises(RuntimeError, match="pipeline stage boom"):
        service.query("some question")
