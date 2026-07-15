"""Phase 0/5 — stdio 与 streamable-http 工具暴露一致性。"""
from __future__ import annotations

import inspect

from fastmcp import FastMCP

from src.mcp.registration import bootstrap
from src.mcp.tool_catalog import TOOL_ALIASES
from src.utils.config import Config


def test_legacy_aliases_disabled_hides_namespace_tools(monkeypatch):
    monkeypatch.setenv("SHINEHE_HOME", str(Config._instance and "." or "."))
    Config.set("mcp.tool_profile", "full")
    Config.set("mcp.enable_legacy_aliases", False)
    Config.set("mcp.experimental_tools_enabled", True)

    mcp = FastMCP("parity-test")
    state = bootstrap(mcp)

    assert state.aliases_enabled is False
    assert state.registered_aliases == {}
    for alias in ("kb.search", "ops.ping", "memory.remember", "graph.traverse"):
        assert alias not in state.registered_aliases
        assert alias not in state.visible_tool_names


def test_legacy_aliases_enabled_exposes_matching_aliases():
    Config.set("mcp.tool_profile", "full")
    Config.set("mcp.enable_legacy_aliases", True)
    Config.set("mcp.experimental_tools_enabled", True)

    mcp = FastMCP("parity-alias-on")
    state = bootstrap(mcp)
    assert state.aliases_enabled is True
    # 至少应注册部分别名（原工具在可见集合中时）
    assert len(state.registered_aliases) > 0 or len(TOOL_ALIASES) == 0


def test_stdio_http_share_registration_bootstrap():
    """两种 transport 必须共用 registration.bootstrap 暴露源。"""
    import src.mcp.server as server

    src = inspect.getsource(server)
    assert "bootstrap" in src or "registration" in src

    # 期望最终存在 get_exposed_tool_definitions 统一入口（Phase 5 补齐）
    import src.mcp.registration as registration

    assert hasattr(registration, "bootstrap")
    # Phase 5 修复后应存在；基线可能失败
    assert hasattr(registration, "get_exposed_tool_definitions"), (
        "missing get_exposed_tool_definitions — stdio/http must share one exposure API"
    )


def test_tool_functions_reject_var_keyword():
    """工具函数不应 **kwargs 静默吞掉未知参数。"""
    from src.mcp.tools.graph import graph_traverse
    from src.mcp.tools.retrieval import search, route_query

    for fn in (graph_traverse, search, route_query):
        sig = inspect.signature(fn)
        assert not any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ), f"{fn.__name__} accepts **kwargs"
