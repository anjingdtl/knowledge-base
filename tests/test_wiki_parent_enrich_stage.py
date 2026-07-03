"""WikiParentEnrichStage 单测 — post-rerank 挂载,仅 enrich wiki 候选。"""
from __future__ import annotations

import asyncio

import pytest

from src.services.rag_pipeline import (
    DEFAULT_PIPELINE_CONFIG,
    StageRegistry,
    WikiParentEnrichStage,
)


class _StubRetriever:
    """记录调用,给 wiki 候选加 parent_content。"""

    def __init__(self):
        self.called = 0

    def enrich(self, candidates, max_length=None):
        self.called += 1
        for c in candidates:
            if str(c.get("id", "")).startswith("wiki:") and c.get("metadata", {}).get("page_type") != "sources":
                c["parent_content"] = "STUB_SOURCE_SUMMARY"
        return candidates


def _ctx(reranked):
    """构造最小 RagContext-like 对象。"""
    class _Ctx:
        def __init__(self):
            self.question = "FTTR 是什么"
            self.reranked_results = reranked
            self.candidates = list(reranked)
            self.metadata = {}
            self.wiki_context = ""
            self.sources = []
            self.conversation_history = []
            self.query_spec_override = None
    return _Ctx()


def _run(stage, ctx, config=None):
    return asyncio.run(stage.execute(ctx, config or {}))


def _enable_wiki_first(monkeypatch):
    """门控:mode=wiki_first + rag.wiki_parent_child.enabled=true。"""
    from src.utils import config as cfg_mod
    real_get = cfg_mod.Config.get

    def fake_get(key, default=None):
        if key == "knowledge_workflow.mode":
            return "wiki_first"
        if key == "rag.wiki_parent_child.enabled":
            return True
        return real_get(key, default)

    monkeypatch.setattr(cfg_mod.Config, "get", staticmethod(fake_get))


def test_stage_registered():
    """StageRegistry 含 wiki_parent_enrich。"""
    assert StageRegistry.get("wiki_parent_enrich") is WikiParentEnrichStage


def test_pipeline_config_has_stage():
    """DEFAULT_PIPELINE_CONFIG 在 rerank 后、generate 前有 wiki_parent_enrich 条目。"""
    names = [e["stage"] for e in DEFAULT_PIPELINE_CONFIG]
    assert "wiki_parent_enrich" in names
    assert names.index("wiki_parent_enrich") > names.index("rerank")
    assert names.index("wiki_parent_enrich") < names.index("generate")


def test_stage_enriches_only_wiki_candidates(monkeypatch):
    """wiki 候选加 parent_content,block 候选不动。"""
    _enable_wiki_first(monkeypatch)
    retriever = _StubRetriever()
    stage = WikiParentEnrichStage(wiki_parent_retriever=retriever)
    wiki_cand = {"id": "wiki:entities:foo", "text": "e",
                 "metadata": {"page_type": "entities", "knowledge_id": "k1"},
                 "match_channels": ["wiki_read"]}
    block_cand = {"id": "page1:block2", "text": "b", "metadata": {"page_id": "page1"}}
    ctx = _ctx([wiki_cand, block_cand])
    _run(stage, ctx)
    assert ctx.reranked_results[0]["parent_content"] == "STUB_SOURCE_SUMMARY"
    assert "parent_content" not in ctx.reranked_results[1]
    assert retriever.called == 1


def test_stage_noop_when_disabled(monkeypatch):
    """enabled=false 时空操作,不调 retriever。"""
    from src.utils import config as cfg_mod
    real_get = cfg_mod.Config.get

    def fake_get(key, default=None):
        if key == "rag.wiki_parent_child.enabled":
            return False
        if key == "knowledge_workflow.mode":
            return "wiki_first"
        return real_get(key, default)

    monkeypatch.setattr(cfg_mod.Config, "get", staticmethod(fake_get))
    retriever = _StubRetriever()
    stage = WikiParentEnrichStage(wiki_parent_retriever=retriever)
    ctx = _ctx([{"id": "wiki:entities:x", "metadata": {"page_type": "entities", "knowledge_id": "k"}}])
    _run(stage, ctx)
    assert retriever.called == 0
    assert "parent_content" not in ctx.reranked_results[0]


def test_stage_noop_in_legacy_mode(monkeypatch):
    """mode=legacy 时空操作(S6)。"""
    from src.utils import config as cfg_mod
    real_get = cfg_mod.Config.get

    def fake_get(key, default=None):
        if key == "knowledge_workflow.mode":
            return "legacy"
        return real_get(key, default)

    monkeypatch.setattr(cfg_mod.Config, "get", staticmethod(fake_get))
    retriever = _StubRetriever()
    stage = WikiParentEnrichStage(wiki_parent_retriever=retriever)
    ctx = _ctx([{"id": "wiki:entities:x", "metadata": {"page_type": "entities", "knowledge_id": "k"}}])
    _run(stage, ctx)
    assert retriever.called == 0


def test_stage_noop_when_empty_results(monkeypatch):
    """reranked_results 为空时空操作。"""
    _enable_wiki_first(monkeypatch)
    retriever = _StubRetriever()
    stage = WikiParentEnrichStage(wiki_parent_retriever=retriever)
    ctx = _ctx([])
    _run(stage, ctx)
    assert retriever.called == 0


def test_stage_fallback_to_container_service(monkeypatch):
    """构造器未注入 retriever 时走 _get_container_service fallback。"""
    _enable_wiki_first(monkeypatch)
    retriever = _StubRetriever()
    import src.services.rag_pipeline as rp
    monkeypatch.setattr(rp, "_get_container_service",
                        lambda name, fb: retriever if name == "wiki_parent_retriever" else fb())
    stage = WikiParentEnrichStage()  # 不注入
    ctx = _ctx([{"id": "wiki:entities:foo",
                 "metadata": {"page_type": "entities", "knowledge_id": "k"}}])
    _run(stage, ctx)
    assert retriever.called == 1
