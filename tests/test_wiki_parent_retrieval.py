"""WikiParentRetriever 单测 — wiki 候选带回 source 页 parent_content (S3)。"""
from __future__ import annotations

from src.services.wiki_parent_retrieval import (
    WikiParentRetriever,
    enrich_wiki_parent_context,
)


class _FakeDb:
    """最小 db mock：get_knowledge_batch / get_knowledge。"""

    def __init__(self, items: dict[str, dict]):
        self._items = items
        self.called_batch: list[list[str]] = []

    def get_knowledge(self, item_id, include_deleted=False):
        return self._items.get(item_id)

    def get_knowledge_batch(self, ids, include_deleted=False):
        self.called_batch.append(list(ids))
        return {k: self._items[k] for k in ids if k in self._items}


def _wiki_cand(page_type: str, kid: str, slug: str = "foo") -> dict:
    return {
        "id": f"wiki:{page_type}:{slug}",
        "text": f"{page_type} body",
        "metadata": {"page_type": page_type, "title": slug, "knowledge_id": kid},
        "match_channels": ["wiki_read"],
    }


def test_entity_candidate_gets_parent_content():
    """S3 核心:wiki entity 候选 parent_content 非空且指向 source 页。"""
    db = _FakeDb({"kid-1": {"id": "kid-1", "title": "源文档",
                            "content": "# 标题\n\n这是 source 页的首段摘要内容。"}})
    cand = _wiki_cand("entities", "kid-1")
    out = WikiParentRetriever(db=db).enrich([cand])
    assert out[0]["parent_content"]  # 非空
    assert "source 页的首段摘要内容" in out[0]["parent_content"]  # 指向 source 页
    assert db.called_batch == [["kid-1"]]  # 证明经 knowledge_id 回查 (A2 方案)


def test_concept_candidate_gets_parent_content():
    db = _FakeDb({"kid-2": {"id": "kid-2", "content": "概念相关 source 全文。"}})
    cand = _wiki_cand("concepts", "kid-2")
    out = WikiParentRetriever(db=db).enrich([cand])
    assert "概念相关 source 全文" in out[0]["parent_content"]


def test_sources_page_skipped():
    """sources 页自身即 source,不加 parent_content。"""
    db = _FakeDb({"kid-3": {"id": "kid-3", "content": "x"}})
    cand = _wiki_cand("sources", "kid-3")
    out = WikiParentRetriever(db=db).enrich([cand])
    assert "parent_content" not in out[0]
    assert db.called_batch == []  # sources 不触发回查


def test_block_candidate_untouched():
    """非 wiki 候选(检索候选 id=page_id:block_id)不被 enrich。"""
    db = _FakeDb({"kid-4": {"id": "kid-4", "content": "x"}})
    block_cand = {"id": "page1:block2", "text": "block", "metadata": {"page_id": "page1"}}
    out = WikiParentRetriever(db=db).enrich([block_cand])
    assert "parent_content" not in out[0]
    assert db.called_batch == []


def test_missing_knowledge_id_no_crash():
    """knowledge_id 缺失时静默跳过,不抛异常。"""
    cand = {"id": "wiki:entities:nope", "text": "x", "metadata": {"page_type": "entities"}}
    out = WikiParentRetriever(db=_FakeDb({})).enrich([cand])
    assert "parent_content" not in out[0]


def test_source_not_in_db_no_crash():
    """knowledge_id 在 db 中不存在时静默跳过。"""
    cand = _wiki_cand("entities", "ghost")
    out = WikiParentRetriever(db=_FakeDb({})).enrich([cand])
    assert "parent_content" not in out[0]


def test_truncation_respects_max_length():
    """parent_content 截断到 max_length。"""
    long_content = "A" * 5000
    db = _FakeDb({"kid-1": {"id": "kid-1", "content": long_content}})
    cand = _wiki_cand("entities", "kid-1")
    out = WikiParentRetriever(db=db).enrich([cand], max_length=100)
    assert len(out[0]["parent_content"]) <= 100


def test_syntheses_uses_source_ids_list():
    """syntheses/comparisons 页优先用 source_ids 列表多查。"""
    db = _FakeDb({
        "kid-a": {"id": "kid-a", "content": "源A摘要。"},
        "kid-b": {"id": "kid-b", "content": "源B摘要。"},
    })
    cand = {
        "id": "wiki:syntheses:s1",
        "text": "综合页",
        "metadata": {"page_type": "syntheses", "source_ids": ["kid-a", "kid-b"]},
    }
    out = WikiParentRetriever(db=db).enrich([cand])
    assert "源A摘要" in out[0]["parent_content"]
    assert "源B摘要" in out[0]["parent_content"]


def test_convenience_function():
    """enrich_wiki_parent_context 便捷函数等价于 retriever.enrich。"""
    db = _FakeDb({"kid-1": {"id": "kid-1", "content": "便利函数 source。"}})
    cand = _wiki_cand("entities", "kid-1")
    out = enrich_wiki_parent_context([cand], db=db)
    assert "便利函数 source" in out[0]["parent_content"]


def test_empty_candidates_returns_empty():
    assert WikiParentRetriever(db=_FakeDb({})).enrich([]) == []
