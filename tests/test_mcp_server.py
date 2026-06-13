"""MCP Server 工具测试 — envelope-aware 形式（Sprint 1 适配）。"""
from __future__ import annotations

import json

import pytest

from src.mcp_server import (
    create,
    delete,
    get_knowledge_resource,
    get_stats_resource,
    get_tags_resource,
    knowledge_qa_prompt,
    list_knowledge,
    read,
    search_fulltext,
    update,
)
from src.mcp_server import (
    tags as get_tags,
)
from src.models.knowledge import KnowledgeItem
from src.services.db import Database


@pytest.fixture
def mcp_env(setup_db, monkeypatch):
    """Mock BlockStore 和 VectorStore 避免 embedding API 调用"""

    class MockVS:
        def __init__(self, db=None):
            pass
        def search(self, query, top_k=5):
            return [
                {"id": "chunk-1", "text": "测试内容", "metadata": {"knowledge_id": ""}, "distance": 0.85}
            ]
        def add_chunks(self, chunks):
            pass
        def delete_by_knowledge(self, kid):
            pass
        def count(self):
            return 0

    class MockBS:
        def __init__(self, db=None):
            pass
        def search(self, query, top_k=5):
            return [
                {"id": "block-1", "text": "测试内容", "page_id": "", "distance": 0.85}
            ]
        def add_block_embedding(self, block_id, embedding):
            pass
        def delete_by_page(self, page_id):
            pass
        def count(self):
            return 0

    monkeypatch.setattr("src.services.vectorstore.VectorStore", MockVS)
    monkeypatch.setattr("src.services.block_store.BlockStore", MockBS)


def _insert_sample(title="测试知识", content="测试内容", tags=None, file_type="txt"):
    item = KnowledgeItem(title=title, content=content, tags=tags or [], file_type=file_type)
    Database.insert_knowledge(item.to_row())
    return item


# ---- Tools ----

class TestSearchFulltext:
    def test_returns_results(self, mcp_env):
        _insert_sample("Python 入门", "Python 是一种编程语言")
        _insert_sample("Java 入门", "Java 是一种编程语言")
        result = search_fulltext(query="Python")
        assert result["ok"] is True
        data = result["data"]
        assert len(data) >= 1
        assert any("Python" in r["title"] for r in data)


class TestRead:
    def test_existing_item(self, mcp_env):
        item = _insert_sample()
        result = read(item_id=item.id)
        assert result["ok"] is True
        assert result["data"]["id"] == item.id
        assert result["data"]["title"] == "测试知识"

    def test_nonexistent_returns_envelope_fail(self, mcp_env):
        """Phase 0+1 改造：read 不再抛 ValueError，改为 ok=false envelope。"""
        result = read(item_id="nonexistent-id")
        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"
        assert "nonexistent-id" in result["error"]["details"]["item_id"]


class TestCreate:
    def test_basic(self, mcp_env):
        result = create(title="新知识", content="新内容", tags=["标签"])
        assert result["ok"] is True
        assert "id" in result["data"]
        assert result["data"]["title"] == "新知识"
        assert Database.get_knowledge(result["data"]["id"]) is not None
        # Sprint 1：写工具必须返回 operation_id
        assert "operation_id" in result

    def test_markdown(self, mcp_env):
        result = create(title="MD 文档", content="# 标题\n正文", file_type="md")
        assert result["data"]["message"] == "知识创建成功并已完成索引"

    def test_code(self, mcp_env):
        result = create(title="代码片段", content="print('hello')", file_type="code")
        assert result["data"]["id"]


class TestUpdate:
    def test_update_title(self, mcp_env):
        item = _insert_sample("原标题")
        result = update(item_id=item.id, title="新标题")
        assert result["ok"] is True
        updated = Database.get_knowledge(item.id)
        assert updated["title"] == "新标题"
        assert "operation_id" in result

    def test_nonexistent_returns_envelope_fail(self, mcp_env):
        result = update(item_id="nonexistent", title="x")
        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"

    def test_no_fields(self, mcp_env):
        item = _insert_sample()
        result = update(item_id=item.id)
        assert result["ok"] is True
        assert "未提供" in result["data"]["message"]

    def test_dry_run(self, mcp_env):
        item = _insert_sample("原标题")
        result = update(item_id=item.id, title="新标题", dry_run=True)
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert "would_change" in result["data"]
        # 数据库未变
        assert Database.get_knowledge(item.id)["title"] == "原标题"


class TestDelete:
    def test_basic(self, mcp_env):
        item = _insert_sample()
        result = delete(item_id=item.id)
        assert result["ok"] is True
        # Phase 4：软删除 — 消息体现"软删除/可恢复"
        assert "软删除" in result["data"]["message"] or "成功" in result["data"]["message"]
        assert result["data"]["soft_deleted"] is True
        assert "operation_id" in result
        # 二次 read 应返 NOT_FOUND（默认过滤 deleted_at）
        read_result = read(item_id=item.id)
        assert read_result["ok"] is False
        assert read_result["error"]["code"] == "NOT_FOUND"

    def test_nonexistent_returns_envelope_fail(self, mcp_env):
        result = delete(item_id="nonexistent")
        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"

    def test_dry_run(self, mcp_env):
        item = _insert_sample()
        result = delete(item_id=item.id, dry_run=True)
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert "would_delete" in result["data"]["would_change"]
        # 数据仍在
        assert Database.get_knowledge(item.id) is not None
        assert Database.get_knowledge(item.id)["deleted_at"] is None


class TestList:
    def test_basic(self, mcp_env):
        _insert_sample("知识A")
        _insert_sample("知识B")
        result = list_knowledge()
        assert result["ok"] is True
        assert result["meta"]["total"] >= 2
        assert len(result["data"]) >= 2

    def test_pagination(self, mcp_env):
        for i in range(5):
            _insert_sample(f"知识{i}")
        result = list_knowledge(limit=2, offset=0)
        assert len(result["data"]) == 2
        assert result["meta"]["limit"] == 2
        assert result["meta"]["next_offset"] == 2


class TestTags:
    def test_returns_tags(self, mcp_env):
        _insert_sample("A", tags=["Python", "教程"])
        _insert_sample("B", tags=["Python", "进阶"])
        result = get_tags()
        assert result["ok"] is True
        tag_list = result["data"]
        assert "Python" in tag_list
        assert "教程" in tag_list


# ---- Resources ----

class TestResources:
    def test_tags_resource(self, mcp_env):
        _insert_sample(tags=["AI"])
        result = get_tags_resource()
        data = json.loads(result)
        assert data["ok"] is True
        assert "AI" in data["data"]["tags"]

    def test_stats_resource(self, mcp_env):
        _insert_sample()
        result = get_stats_resource()
        data = json.loads(result)
        assert data["ok"] is True
        assert data["data"]["knowledge_items"] >= 1

    def test_knowledge_resource(self, mcp_env):
        item = _insert_sample()
        result = get_knowledge_resource(item_id=item.id)
        data = json.loads(result)
        assert data["ok"] is True
        assert data["data"]["title"] == "测试知识"

    def test_knowledge_resource_not_found(self, mcp_env):
        result = get_knowledge_resource(item_id="nonexistent")
        data = json.loads(result)
        assert data["ok"] is False
        assert data["error"]["code"] == "NOT_FOUND"


# ---- Prompts ----

class TestPrompts:
    def test_kb_qa(self):
        result = knowledge_qa_prompt(question="什么是Python？")
        assert "Python" in result
        assert "知识库" in result
