"""WikiParentEnrichStage 单测 — post-rerank 挂载,仅 enrich wiki 候选。"""
from __future__ import annotations

import asyncio

from src.services.blend_fusion import blend_fusion
from src.services.rag_pipeline import (
    DEFAULT_PIPELINE_CONFIG,
    StageRegistry,
    WikiParentEnrichStage,
)
from src.services.wiki_parent_retrieval import WikiParentRetriever


class _FakeDb:
    """Minimal db mock for WikiParentRetriever (mirrors test_wiki_parent_retrieval.py)."""

    def __init__(self, items: dict):
        self._items = items

    def get_knowledge(self, item_id, include_deleted=False):
        return self._items.get(item_id)

    def get_knowledge_batch(self, ids, include_deleted=False):
        return {k: self._items[k] for k in ids if k in self._items}


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


# ── Task 2.3: blend 档共存契约测试 ──────────────────────────────────────────


def test_blend_preserves_wiki_parent_content():
    """blend 融合后 wiki 候选的 parent_content 保留(S3 在 blend 档仍成立)。"""
    db = _FakeDb({"kid-1": {"id": "kid-1", "content": "wiki source 摘要"}})
    # 先 enrich wiki 候选(模拟 post-rerank 已挂 parent_content)
    wiki_cands = WikiParentRetriever(db=db).enrich([
        {"id": "wiki:entities:foo", "text": "wiki 命中",
         "metadata": {"page_type": "entities", "knowledge_id": "kid-1"},
         "match_channels": ["wiki_read"]}])
    # block 检索候选也带自己的 parent_content(block parent-child 已挂)
    search_cands = [
        {"id": "page1:block1", "text": "block 命中", "parent_content": "block 父块",
         "metadata": {"page_id": "page1"}, "match_channels": ["vector", "keyword"]}]
    merged = blend_fusion(wiki_cands, search_cands)
    wiki_merged = [c for c in merged if str(c["id"]).startswith("wiki:")][0]
    block_merged = [c for c in merged if not str(c["id"]).startswith("wiki:")][0]
    assert "wiki source 摘要" in wiki_merged["parent_content"]  # wiki parent 不丢
    assert block_merged["parent_content"] == "block 父块"  # block parent 不被覆盖


def test_blend_id_systems_do_not_collide():
    """wiki 候选(wiki:type:slug)与检索候选(page_id:block_id)id 体系不同,不互覆盖。"""
    wiki = [{"id": "wiki:entities:foo", "text": "w",
             "metadata": {"page_type": "entities", "knowledge_id": "k"},
             "match_channels": ["wiki_read"]}]
    search = [{"id": "wiki:entities:foo", "text": "s",  # 故意同 id(极端情况)
               "metadata": {}, "match_channels": ["vector"]}]
    merged = blend_fusion(wiki, search)
    # 同 id 累加 RRF 分(并集 match_channels),不丢任一路
    assert len(merged) == 1
    assert set(merged[0]["match_channels"]) == {"wiki_read", "vector"}
