"""WikiReadStage 单元测试(第二阶段 Task 1.2, spec S2)。

验证:wiki_read 档零向量(VectorSearchStage 不调 hybrid)、full_search 档不产
候选、stage 注册与管线配置顺序、legacy 门控(stage 层 S6)。
"""
from __future__ import annotations

import pytest

from src.services.rag_pipeline import (
    DEFAULT_PIPELINE_CONFIG,
    RagContext,
    StageRegistry,
    VectorSearchStage,
    WikiReadStage,
)
from src.utils.config import Config


def _enable_wiki_first() -> None:
    Config.set("knowledge_workflow.mode", "wiki_first")
    Config.set("rag.size_aware.enabled", True)


def _legacy_mode() -> None:
    Config.set("knowledge_workflow.mode", "legacy")
    Config.set("rag.size_aware.enabled", False)


class _FakeRouter:
    def __init__(self, scale: str) -> None:
        self._scale = scale

    def route(self, question: str) -> dict:
        return {"scale": self._scale, "reason": f"fake:{self._scale}", "wiki_hits": 1}


class _FakeLocator:
    def __init__(self, candidates: list[dict]) -> None:
        self._cands = candidates
        self.called = False

    def locate(self, query: str, top_n: int = 10):
        self.called = True
        return list(self._cands), len(self._cands)


class _HybridSpy:
    """记录 search 调用次数,验证 wiki_read 档零向量。"""

    def __init__(self) -> None:
        self.search_calls = 0

    def search(self, queries, top_k=10):
        self.search_calls += 1
        return []


@pytest.mark.asyncio
async def test_wiki_read_stage_zero_vector_calls():
    _enable_wiki_first()
    wiki_cand = [{"id": "wiki:sources:fttr", "text": "FTTR 光纤",
                  "metadata": {"title": "FTTR"}, "match_channels": ["wiki_read"]}]
    router = _FakeRouter("wiki_read")
    locator = _FakeLocator(wiki_cand)
    spy = _HybridSpy()

    wiki_stage = WikiReadStage(size_aware_router=router, wiki_page_locator=locator)
    vec_stage = VectorSearchStage(hybrid_search=spy)

    ctx = RagContext(question="FTTR是什么")
    ctx = await wiki_stage.execute(ctx, {"enabled": True})
    assert ctx.metadata["scale"] == "wiki_read"
    assert ctx.candidates, "WikiReadStage 应填充 wiki 候选"

    ctx = await vec_stage.execute(ctx, {"enabled": True, "top_k": 10})
    assert spy.search_calls == 0, "wiki_read 档不应触发向量搜索"
    assert ctx.candidates, "wiki 候选应保留"


@pytest.mark.asyncio
async def test_wiki_read_stage_skipped_for_full_search():
    _enable_wiki_first()
    wiki_cand = [{"id": "wiki:sources:fttr", "text": "FTTR",
                  "metadata": {}, "match_channels": ["wiki_read"]}]
    router = _FakeRouter("full_search")
    locator = _FakeLocator(wiki_cand)
    stage = WikiReadStage(size_aware_router=router, wiki_page_locator=locator)

    ctx = RagContext(question="列出所有FTTR文档")
    ctx = await stage.execute(ctx, {"enabled": True})
    assert ctx.metadata["scale"] == "full_search"
    assert ctx.candidates == [], "full_search 档 WikiReadStage 不应产候选"
    assert not locator.called


def test_wiki_read_stage_registered():
    assert "wiki_read" in StageRegistry.get_all()
    names = [entry["stage"] for entry in DEFAULT_PIPELINE_CONFIG]
    assert "wiki_read" in names
    assert names.index("wiki_read") < names.index("vector_search")


@pytest.mark.asyncio
async def test_wiki_read_stage_legacy_skipped():
    # S6(stage 层):legacy 模式 WikiReadStage 完全空操作
    _legacy_mode()
    router = _FakeRouter("wiki_read")
    locator = _FakeLocator([{"id": "wiki:sources:x", "text": "t",
                             "metadata": {}, "match_channels": ["wiki_read"]}])
    stage = WikiReadStage(size_aware_router=router, wiki_page_locator=locator)
    ctx = RagContext(question="FTTR是什么")
    ctx = await stage.execute(ctx, {"enabled": True})
    assert "scale" not in ctx.metadata
    assert not locator.called
