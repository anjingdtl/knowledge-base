"""Phase 6: MCP tool selection respects write_policy and authoring mode."""
from __future__ import annotations

from src.mcp.tool_registry import (
    get_definitions,
    list_hidden_by_policy,
    select_tools,
)


def _ensure_defs():
    """Import mcp_server so tools register; safe if already imported."""
    if get_definitions():
        return
    import src.mcp_server  # noqa: F401


class TestWritePolicyFilter:
    def test_disabled_hides_write_tools_in_core(self):
        _ensure_defs()
        tools = select_tools("core", write_policy="disabled")
        names = {d.name for d in tools}
        # core still has read tools
        assert "search" in names or "ask" in names or "ping" in names
        # write side_effect tools hidden
        for d in tools:
            assert d.side_effect not in ("write", "destructive"), d.name
        # index_path / reindex_all are write in core
        assert "index_path" not in names
        assert "reindex_all" not in names

    def test_disabled_also_filters_full_and_legacy(self):
        _ensure_defs()
        for profile in ("full", "legacy", "admin"):
            tools = select_tools(profile, experimental_enabled=True, write_policy="disabled")
            for d in tools:
                assert d.side_effect not in ("write", "destructive"), f"{profile}:{d.name}"

    def test_verified_hides_wiki_write_when_mode_set(self):
        _ensure_defs()
        tools = select_tools(
            "legacy",
            experimental_enabled=True,
            write_policy="local_confirm",
            knowledge_mode="verified",
            authoring_enabled=False,
        )
        for d in tools:
            if d.group == "wiki":
                assert d.side_effect == "read", d.name

    def test_authoring_keeps_wiki_write_when_allowed(self):
        _ensure_defs()
        tools = select_tools(
            "legacy",
            experimental_enabled=True,
            write_policy="local_confirm",
            knowledge_mode="authoring",
            authoring_enabled=True,
        )
        wiki_writes = [d for d in tools if d.group == "wiki" and d.side_effect == "write"]
        # legacy+experimental should still expose some wiki writes under authoring
        assert len(wiki_writes) >= 1

    def test_hidden_by_policy_lists_names(self):
        _ensure_defs()
        hidden = list_hidden_by_policy("core", write_policy="disabled")
        assert "index_path" in hidden or "reindex_all" in hidden

    def test_backward_compat_no_extra_kwargs(self):
        """Omitting policy kwargs must not change historical profile selection."""
        _ensure_defs()
        a = {d.name for d in select_tools("core")}
        b = {d.name for d in select_tools("core", experimental_enabled=False)}
        assert a == b
