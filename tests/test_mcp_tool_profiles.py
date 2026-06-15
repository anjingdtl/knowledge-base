"""MCP 工具配置档契约测试 — 验证 tool_profile 机制。

本测试文件在 M0 阶段创建，预期在 profile registry 实现前部分失败。
M1/M3 完成后所有测试应通过。

覆盖：
- core profile 恰好 10 个工具
- core 不包含 wiki/graph/memory/CRUD 工具
- legacy profile 与冻结 snapshot 一致
- 配置兼容规则（缺省 core / 无 profile 走 legacy / 别名开关 / 实验工具开关）
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import src.mcp_server as mcp_mod

LEGACY_SNAPSHOT = Path(__file__).parent / "snapshots" / "mcp_tools_legacy.json"

CORE_TOOLS = frozenset({
    "ping",
    "kb_capabilities",
    "search",
    "ask",
    "read",
    "list_knowledge",
    "index_path",
    "get_job",
    "list_jobs",
    "reindex_all",
})

# 这些工具不应出现在 core profile 中
FORBIDDEN_IN_CORE = frozenset({
    # wiki
    "save_to_wiki", "wiki_lint", "wiki_submit_review", "wiki_approve",
    "wiki_reject", "wiki_deprecate", "wiki_workflow_history",
    "wiki_list_versions", "wiki_restore_version", "fix_dead_references",
    # graph
    "graph_traverse",
    # memory
    "remember_fact", "recall_facts", "update_project_context",
    "search_decisions", "summarize_recent_changes", "extract_tasks_from_doc",
    # CRUD
    "create", "delete", "update",
    # 别名
    "kb.search", "kb.ask", "wiki.save", "graph.traverse",
    "ops.ping", "memory.remember",
})


def _registered_tool_names() -> set[str]:
    """抽取 FastMCP 当前注册的工具名集合。"""
    async def _names():
        return {tool.name for tool in await mcp_mod.mcp.list_tools()}
    return asyncio.run(_names())


# ---- Core Profile 工具数量与成员 ----

class TestCoreProfileTools:
    """core profile 下恰好 10 个工具，且不包含高级/实验工具。"""

    def test_core_tool_count_is_ten(self):
        """core profile 必须恰好注册 10 个工具。"""
        # 在 profile registry 实现前，此测试预期失败
        try:
            from src.mcp.tool_registry import select_tools
            definitions = select_tools("core", experimental_enabled=False)
            core_names = {d.name for d in definitions}
            assert len(core_names) == 10, (
                f"core profile 应有 10 个工具，实际 {len(core_names)}: {sorted(core_names)}"
            )
        except ImportError:
            pytest.skip("tool_registry 尚未实现（M1 阶段）")

    def test_core_tools_match_spec(self):
        """core profile 工具集必须与 spec 定义一致。"""
        try:
            from src.mcp.tool_registry import select_tools
            definitions = select_tools("core", experimental_enabled=False)
            core_names = {d.name for d in definitions}
            assert core_names == CORE_TOOLS, (
                f"不匹配 — 缺少: {CORE_TOOLS - core_names}, 多余: {core_names - CORE_TOOLS}"
            )
        except ImportError:
            pytest.skip("tool_registry 尚未实现（M1 阶段）")

    def test_core_excludes_forbidden_tools(self):
        """core profile 不能包含 wiki/graph/memory/CRUD 工具和别名。"""
        try:
            from src.mcp.tool_registry import select_tools
            definitions = select_tools("core", experimental_enabled=False)
            core_names = {d.name for d in definitions}
            leaked = core_names & FORBIDDEN_IN_CORE
            assert not leaked, f"core profile 不应包含: {sorted(leaked)}"
        except ImportError:
            pytest.skip("tool_registry 尚未实现（M1 阶段）")


# ---- Legacy Profile 与冻结 Snapshot 一致性 ----

class TestLegacyProfileSnapshot:
    """legacy profile 必须与冻结的 mcp_tools_legacy.json 一致。"""

    def test_legacy_snapshot_exists(self):
        assert LEGACY_SNAPSHOT.exists(), f"缺少 legacy snapshot: {LEGACY_SNAPSHOT}"

    def test_legacy_snapshot_has_tools(self):
        data = json.loads(LEGACY_SNAPSHOT.read_text(encoding="utf-8"))
        assert "tools" in data
        assert len(data["tools"]) > 0

    def test_legacy_original_tools_count(self):
        """legacy snapshot 原始工具数 >= 51。"""
        data = json.loads(LEGACY_SNAPSHOT.read_text(encoding="utf-8"))
        originals = [t for t in data["tools"] if not t.get("is_alias")]
        assert len(originals) >= 51, f"原始工具数 {len(originals)} < 51"

    def test_legacy_aliases_count(self):
        """legacy snapshot 别名数 >= 45。"""
        data = json.loads(LEGACY_SNAPSHOT.read_text(encoding="utf-8"))
        aliases = [t for t in data["tools"] if t.get("is_alias")]
        assert len(aliases) >= 45, f"别名数 {len(aliases)} < 45"

    def test_legacy_profile_matches_snapshot(self):
        """legacy profile 注册的工具集应与 snapshot 一致。"""
        try:
            from src.mcp.tool_registry import select_tools
            definitions = select_tools("legacy", experimental_enabled=True)
            profile_names = {d.name for d in definitions}
            data = json.loads(LEGACY_SNAPSHOT.read_text(encoding="utf-8"))
            {t["name"] for t in data["tools"]}
            # profile 应覆盖 snapshot 中所有原始工具
            snapshot_originals = {
                t["name"] for t in data["tools"] if not t.get("is_alias")
            }
            missing = snapshot_originals - profile_names
            assert not missing, f"legacy profile 缺少原始工具: {sorted(missing)}"
        except ImportError:
            pytest.skip("tool_registry 尚未实现（M1 阶段）")


# ---- 配置兼容规则 ----

class TestConfigCompatibility:
    """配置兼容规则测试。"""

    def test_new_config_defaults_to_full(self):
        """新配置缺省值为 full。"""
        try:
            from src.mcp.tool_registry import resolve_tool_profile
            # 模拟无 mcp.tool_profile 的新配置
            profile = resolve_tool_profile({"mcp.tool_profile": None})
            # 新配置（没有旧工具使用痕迹）应默认为 full
            assert profile == "full", (
                f"缺省 profile 应为 full，实际: {profile}"
            )
        except ImportError:
            pytest.skip("tool_registry 尚未实现（M1 阶段）")

    def test_old_config_without_profile_resolves_to_legacy(self):
        """已有配置未出现 mcp.tool_profile 时解析为 legacy。"""
        try:
            from src.mcp.tool_registry import resolve_tool_profile
            # 模拟老配置：有 mcp 段但没有 tool_profile 字段
            profile = resolve_tool_profile({"mcp.write_policy": "interactive"})
            assert profile == "legacy", (
                f"老配置无 tool_profile 应走 legacy，实际: {profile}"
            )
        except ImportError:
            pytest.skip("tool_registry 尚未实现（M1 阶段）")

    def test_aliases_disabled_when_not_legacy(self):
        """enable_legacy_aliases=false 时不注册别名。"""
        try:
            from src.mcp.tool_registry import select_tools
            definitions = select_tools("core", experimental_enabled=False)
            names = {d.name for d in definitions}
            alias_names = {n for n in names if "." in n}
            assert not alias_names, f"core profile 不应含别名: {sorted(alias_names)}"
        except ImportError:
            pytest.skip("tool_registry 尚未实现（M1 阶段）")

    def test_experimental_tools_hidden_by_default(self):
        """experimental_tools_enabled=false 时隐藏 Wiki/Graph/Memory。"""
        try:
            from src.mcp.tool_registry import select_tools
            definitions = select_tools("full", experimental_enabled=False)
            names = {d.name for d in definitions}
            experimental_groups = {"save_to_wiki", "graph_traverse", "remember_fact"}
            leaked = names & experimental_groups
            assert not leaked, f"experimental disabled 时不应包含: {sorted(leaked)}"
        except ImportError:
            pytest.skip("tool_registry 尚未实现（M1 阶段）")
