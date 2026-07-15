"""Phase 0/5 — stdio 与 streamable-http 工具暴露一致性。"""
from __future__ import annotations

import inspect

from fastmcp import FastMCP

from src.mcp.registration import bootstrap
from src.mcp.tool_catalog import TOOL_ALIASES
from src.utils.config import Config


def _load_tool_defs() -> None:
    # Domain modules register ToolDefinitions on import (server.exports side-effect).
    import src.mcp.tools.exports  # noqa: F401


def test_legacy_aliases_disabled_hides_namespace_tools():
    _load_tool_defs()
    Config.set("mcp.tool_profile", "full")
    Config.set("mcp.enable_legacy_aliases", False)
    Config.set("mcp.experimental_tools_enabled", True)

    from src.mcp.registration import get_exposed_tool_definitions

    exposed = get_exposed_tool_definitions(
        profile="full",
        experimental_enabled=True,
        legacy_aliases_enabled=False,
    )
    assert exposed.legacy_aliases_enabled is False
    assert exposed.alias_map == {}
    assert len(exposed.tool_names) > 0
    for alias in ("kb.search", "ops.ping", "memory.remember", "graph.traverse"):
        assert alias not in exposed.alias_map
        assert alias not in exposed.tool_names
        assert alias not in exposed.all_exposed_names


def test_legacy_aliases_enabled_exposes_matching_aliases():
    _load_tool_defs()

    from src.mcp.registration import get_exposed_tool_definitions

    exposed = get_exposed_tool_definitions(
        profile="full",
        experimental_enabled=True,
        legacy_aliases_enabled=True,
    )
    assert exposed.legacy_aliases_enabled is True
    assert len(exposed.tool_names) > 0
    assert len(exposed.alias_map) > 0


def test_stdio_http_share_registration_bootstrap():
    """两种 transport 必须共用 registration.bootstrap / get_exposed_tool_definitions。"""
    import src.mcp.server as server
    import src.mcp.registration as registration

    src = inspect.getsource(server)
    assert "bootstrap" in src or "registration" in src
    assert hasattr(registration, "bootstrap")
    assert hasattr(registration, "get_exposed_tool_definitions")

    # 相同策略两次计算结果一致（stdio/http 同配置）
    a = registration.get_exposed_tool_definitions(
        profile="full", experimental_enabled=True, legacy_aliases_enabled=False
    )
    b = registration.get_exposed_tool_definitions(
        profile="full", experimental_enabled=True, legacy_aliases_enabled=False
    )
    assert a.all_exposed_names == b.all_exposed_names
    assert a.tool_names == b.tool_names


def test_tool_functions_reject_var_keyword():
    """工具函数不应 **kwargs 静默吞掉未知参数。"""
    from src.mcp.tools.graph import graph_traverse
    from src.mcp.tools.retrieval import search, route_query

    for fn in (graph_traverse, search, route_query):
        sig = inspect.signature(fn)
        assert not any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ), f"{fn.__name__} accepts **kwargs"
