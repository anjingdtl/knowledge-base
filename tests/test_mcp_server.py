"""MCP Server 工具测试"""
import json
import pytest

import src.mcp_server as mcp_mod
from src.mcp_server import (
    search, search_fulltext, read, create, update,
    delete, list_knowledge, tags as get_tags,
    get_knowledge_resource, get_tags_resource, get_stats_resource,
    knowledge_qa_prompt,
)
from src.models.knowledge import KnowledgeItem
from src.services.db import Database


@pytest.fixture
def mcp_env(setup_db, monkeypatch):
    """Mock VectorStore 避免 embedding API 调用"""
    stored_chunks = []

    class MockVS:
        def search(self, query, top_k=5):
            return [
                {"id": "chunk-1", "text": "测试内容", "metadata": {"knowledge_id": ""}, "distance": 0.85}
            ]
        def add_chunks(self, chunks):
            stored_chunks.extend(chunks)
        def delete_by_knowledge(self, kid):
            pass
        def count(self):
            return len(stored_chunks)

    monkeypatch.setattr(mcp_mod, "VectorStore", MockVS)
    return stored_chunks


def _insert_sample(title="测试知识", content="测试内容", tags=None, file_type="txt"):
    item = KnowledgeItem(title=title, content=content, tags=tags or [], file_type=file_type)
    Database.insert_knowledge(item.to_row())
    return item


# ---- Tools ----

class TestSearchFulltext:
    def test_returns_results(self, mcp_env):
        _insert_sample("Python 入门", "Python 是一种编程语言")
        _insert_sample("Java 入门", "Java 是一种编程语言")
        results = search_fulltext("Python")
        assert len(results) >= 1
        assert any("Python" in r["title"] for r in results)


class TestRead:
    def test_existing_item(self, mcp_env):
        item = _insert_sample()
        result = read(item_id=item.id)
        assert result["id"] == item.id
        assert result["title"] == "测试知识"

    def test_nonexistent_raises(self, mcp_env):
        with pytest.raises(ValueError, match="不存在"):
            read(item_id="nonexistent-id")


class TestCreate:
    def test_basic(self, mcp_env):
        result = create(title="新知识", content="新内容", tags=["标签"])
        assert "id" in result
        assert result["title"] == "新知识"
        assert Database.get_knowledge(result["id"]) is not None

    def test_markdown(self, mcp_env):
        result = create(title="MD 文档", content="# 标题\n正文", file_type="md")
        assert result["message"] == "知识创建成功并已完成索引"

    def test_code(self, mcp_env):
        result = create(title="代码片段", content="print('hello')", file_type="code")
        assert result["id"]


class TestUpdate:
    def test_update_title(self, mcp_env):
        item = _insert_sample("原标题")
        update(item_id=item.id, title="新标题")
        updated = Database.get_knowledge(item.id)
        assert updated["title"] == "新标题"

    def test_nonexistent_raises(self, mcp_env):
        with pytest.raises(ValueError, match="不存在"):
            update(item_id="nonexistent", title="x")

    def test_no_fields(self, mcp_env):
        item = _insert_sample()
        result = update(item_id=item.id)
        assert "未提供" in result["message"]


class TestDelete:
    def test_basic(self, mcp_env):
        item = _insert_sample()
        result = delete(item_id=item.id)
        assert "成功" in result["message"]
        assert Database.get_knowledge(item.id) is None

    def test_nonexistent_raises(self, mcp_env):
        with pytest.raises(ValueError, match="不存在"):
            delete(item_id="nonexistent")


class TestList:
    def test_basic(self, mcp_env):
        _insert_sample("知识A")
        _insert_sample("知识B")
        result = list_knowledge()
        assert result["total"] >= 2
        assert len(result["items"]) >= 2

    def test_pagination(self, mcp_env):
        for i in range(5):
            _insert_sample(f"知识{i}")
        result = list_knowledge(limit=2, offset=0)
        assert len(result["items"]) == 2


class TestTags:
    def test_returns_tags(self, mcp_env):
        _insert_sample("A", tags=["Python", "教程"])
        _insert_sample("B", tags=["Python", "进阶"])
        result = get_tags()
        assert "Python" in result
        assert "教程" in result


# ---- Resources ----

class TestResources:
    def test_tags_resource(self, mcp_env):
        _insert_sample(tags=["AI"])
        result = get_tags_resource()
        data = json.loads(result)
        assert "AI" in data["tags"]

    def test_stats_resource(self, mcp_env):
        _insert_sample()
        result = get_stats_resource()
        data = json.loads(result)
        assert data["knowledge_items"] >= 1

    def test_knowledge_resource(self, mcp_env):
        item = _insert_sample()
        result = get_knowledge_resource(item_id=item.id)
        data = json.loads(result)
        assert data["title"] == "测试知识"

    def test_knowledge_resource_not_found(self, mcp_env):
        with pytest.raises(ValueError, match="不存在"):
            get_knowledge_resource(item_id="nonexistent")


# ---- Prompts ----

class TestPrompts:
    def test_kb_qa(self):
        result = knowledge_qa_prompt(question="什么是Python？")
        assert "Python" in result
        assert "知识库" in result
