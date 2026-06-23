"""Agent Memory + Tool Schema 标准化 测试"""

import pytest

# ---- Fixtures ----

@pytest.fixture
def memory_repo(tmp_path):
    """创建独立的 AgentMemoryRepository（临时 SQLite）"""
    from src.services.db import Database
    db = Database(str(tmp_path / "test_memory.db"))
    from src.repositories.agent_memory_repo import AgentMemoryRepository
    repo = AgentMemoryRepository(db=db)
    repo.ensure_table()
    return repo


@pytest.fixture
def memory_service(memory_repo):
    """创建 AgentMemoryService"""
    from src.services.agent_memory import AgentMemoryService
    return AgentMemoryService(repo=memory_repo, db=memory_repo._db, llm=None)


# ---- Repository Tests ----

class TestAgentMemoryRepository:
    def test_store_and_get(self, memory_repo):
        mid = memory_repo.store("key1", "value1", category="fact")
        assert mid
        entry = memory_repo.get_by_id(mid)
        assert entry is not None
        assert entry["key"] == "key1"
        assert entry["value"] == "value1"
        assert entry["category"] == "fact"

    def test_get_by_key(self, memory_repo):
        memory_repo.store("unique_key", "test value")
        entry = memory_repo.get_by_key("unique_key")
        assert entry is not None
        assert entry["value"] == "test value"

    def test_get_by_key_not_found(self, memory_repo):
        assert memory_repo.get_by_key("nonexistent") is None

    def test_upsert_creates_new(self, memory_repo):
        mid = memory_repo.upsert("new_key", "initial", category="fact")
        assert mid
        entry = memory_repo.get_by_key("new_key")
        assert entry["value"] == "initial"

    def test_upsert_updates_existing(self, memory_repo):
        memory_repo.upsert("dup_key", "v1", category="fact")
        memory_repo.upsert("dup_key", "v2", category="decision")
        entry = memory_repo.get_by_key("dup_key")
        assert entry["value"] == "v2"
        assert entry["category"] == "decision"

    def test_delete(self, memory_repo):
        mid = memory_repo.store("to_delete", "value")
        assert memory_repo.delete(mid) is True
        assert memory_repo.get_by_id(mid) is None

    def test_delete_by_key(self, memory_repo):
        memory_repo.store("del_key", "value")
        assert memory_repo.delete_by_key("del_key") is True
        assert memory_repo.get_by_key("del_key") is None

    def test_list_all(self, memory_repo):
        memory_repo.store("k1", "v1", category="fact")
        memory_repo.store("k2", "v2", category="decision")
        memory_repo.store("k3", "v3", category="fact")
        all_items = memory_repo.list_all()
        assert len(all_items) == 3

    def test_list_by_category(self, memory_repo):
        memory_repo.store("k1", "v1", category="fact")
        memory_repo.store("k2", "v2", category="decision")
        facts = memory_repo.list_all(category="fact")
        assert len(facts) == 1
        assert facts[0]["category"] == "fact"

    def test_count(self, memory_repo):
        memory_repo.store("k1", "v1", category="fact")
        memory_repo.store("k2", "v2", category="decision")
        assert memory_repo.count() == 2
        assert memory_repo.count(category="fact") == 1

    def test_search_fts(self, memory_repo):
        memory_repo.store("python", "Python is a programming language")
        memory_repo.store("java", "Java is also a programming language")
        memory_repo.store("recipe", "How to cook pasta")
        results = memory_repo.search_fts("programming")
        assert len(results) >= 2

    def test_search_fts_with_category(self, memory_repo):
        memory_repo.store("py_fact", "Python fact", category="fact")
        memory_repo.store("py_dec", "Python decision", category="decision")
        results = memory_repo.search_fts("Python", category="fact")
        assert len(results) == 1
        assert results[0]["category"] == "fact"

    def test_search_like_fallback(self, memory_repo):
        memory_repo.store("key1", "machine learning model")
        results = memory_repo.search_like("machine")
        assert len(results) >= 1

    def test_recent_changes(self, memory_repo):
        memory_repo.store("k1", "v1", category="fact")
        memory_repo.store("k2", "v2", category="decision")
        stats = memory_repo.recent_changes(since_hours=1)
        assert stats["total"] >= 2


# ---- Service Tests ----

class TestAgentMemoryService:
    def test_remember_fact_new(self, memory_service):
        result = memory_service.remember_fact("project_name", "ShineHeKnowledge")
        assert result["created"] is True
        assert result["key"] == "project_name"

    def test_remember_fact_update(self, memory_service):
        memory_service.remember_fact("version", "1.0")
        result = memory_service.remember_fact("version", "2.0")
        assert result["created"] is False
        assert result["key"] == "version"

    def test_recall_facts(self, memory_service):
        memory_service.remember_fact("lang", "Python is used", category="fact")
        memory_service.remember_fact("tool", "FastMCP is the framework", category="decision")
        results = memory_service.recall_facts("Python")
        assert len(results) >= 1

    def test_recall_facts_falls_back_when_fts_returns_no_rows(self, memory_service, monkeypatch):
        """第七轮报告 BUG-4：FTS 正常但 0 命中时，也应立即用 LIKE 回读刚写入记忆。"""
        memory_service.remember_fact("agent_id", "企微消息应用 AgentID 为 wx-agent-001")

        repo = memory_service._get_repo()
        monkeypatch.setattr(repo, "search_fts", lambda *args, **kwargs: [])

        results = memory_service.recall_facts("AgentID")

        assert len(results) >= 1
        assert results[0]["key"] == "agent_id"

    def test_update_project_context(self, memory_service):
        result = memory_service.update_project_context("This is a knowledge base project")
        assert result["key"] == "__project_context"
        # 可以回取
        context = memory_service.get_project_context()
        assert context == "This is a knowledge base project"

    def test_search_decisions(self, memory_service):
        memory_service.remember_fact("arch_decision", "Use SQLite for storage", category="decision")
        memory_service.remember_fact("lang_fact", "Python is great", category="fact")
        results = memory_service.search_decisions("SQLite")
        assert len(results) >= 1
        assert results[0]["category"] == "decision"

    def test_summarize_recent_changes(self, memory_service):
        memory_service.remember_fact("test_key", "test value")
        result = memory_service.summarize_recent_changes(since_hours=1)
        assert "memory_changes" in result
        assert "summary" in result

    def test_extract_tasks_heuristic(self, memory_service):
        content = "Some text\nTODO: Fix the bug\n- [ ] Write tests\nFIXME: refactor needed"
        result = memory_service.extract_tasks_from_doc(content)
        assert result["total_found"] >= 2
        assert result["method"] == "heuristic"

    def test_extract_tasks_no_tasks(self, memory_service):
        content = "This is a simple paragraph with no tasks."
        result = memory_service.extract_tasks_from_doc(content)
        assert result["total_found"] == 0


# ---- Tool Metadata Tests ----

class TestToolMetadata:
    def test_metadata_completeness(self):
        """每个已知工具都有元数据"""
        from src.mcp_server import _TOOL_METADATA
        required_keys = {"group", "side_effect", "requires_confirmation", "short_desc"}
        for tool_name, meta in _TOOL_METADATA.items():
            for key in required_keys:
                assert key in meta, f"Tool '{tool_name}' missing '{key}'"

    def test_side_effect_values(self):
        """side_effect 只能是 read/write/destructive"""
        from src.mcp_server import _TOOL_METADATA
        valid = {"read", "write", "destructive"}
        for tool_name, meta in _TOOL_METADATA.items():
            assert meta["side_effect"] in valid, f"Tool '{tool_name}' has invalid side_effect: {meta['side_effect']}"

    def test_groups(self):
        """工具分组覆盖所有命名空间"""
        from src.mcp_server import _TOOL_METADATA
        groups = {meta["group"] for meta in _TOOL_METADATA.values()}
        for expected in {"kb", "wiki", "graph", "ops", "memory"}:
            assert expected in groups, f"Missing tool group: {expected}"

    def test_aliases_reference_existing_tools(self):
        """所有别名都指向存在的元数据"""
        from src.mcp_server import _TOOL_ALIASES, _TOOL_METADATA
        for alias_name, original_name in _TOOL_ALIASES.items():
            assert original_name in _TOOL_METADATA, f"Alias '{alias_name}' references unknown tool '{original_name}'"

    def test_destructive_requires_confirmation(self):
        """破坏性工具必须 require_confirmation=True"""
        from src.mcp_server import _TOOL_METADATA
        for tool_name, meta in _TOOL_METADATA.items():
            if meta["side_effect"] == "destructive":
                assert meta["requires_confirmation"] is True, \
                    f"Destructive tool '{tool_name}' should require confirmation"


# ---- Tool Alias Tests ----

class TestToolAliases:
    def test_alias_mapping_not_empty(self):
        from src.mcp_server import _TOOL_ALIASES
        assert len(_TOOL_ALIASES) > 20  # 应该有大量别名

    def test_kb_group_coverage(self):
        """kb.* 组至少覆盖核心 CRUD"""
        from src.mcp_server import _TOOL_ALIASES
        kb_tools = [k for k in _TOOL_ALIASES if k.startswith("kb.")]
        essential = {"kb.search", "kb.ask", "kb.create", "kb.read", "kb.update", "kb.delete"}
        assert essential.issubset(set(kb_tools)), f"Missing essential kb aliases: {essential - set(kb_tools)}"

    def test_wiki_group_coverage(self):
        from src.mcp_server import _TOOL_ALIASES
        wiki_tools = [k for k in _TOOL_ALIASES if k.startswith("wiki.")]
        assert len(wiki_tools) >= 5  # 至少 5 个 wiki 别名

    def test_memory_group_in_aliases(self):
        """memory.* 别名应在注册时添加（非 _TOOL_ALIASES 全局映射中）"""
        # memory.* 别名是在 MCP 工具注册后动态注册的
        # 这里只验证 metadata 中存在 memory 工具
        from src.mcp_server import _TOOL_METADATA
        memory_tools = [k for k in _TOOL_METADATA if _TOOL_METADATA[k]["group"] == "memory"]
        assert len(memory_tools) >= 6
