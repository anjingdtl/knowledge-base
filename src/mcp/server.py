"""ShineHeKnowledge MCP Server — protocol shell (WP2 round-3).

Responsibilities:
  FastMCP instance, domain tool import, registration bootstrap,
  prompt/resource attach, compatibility re-exports.

Tool implementations live under ``src/mcp/tools/*``.
"""
from __future__ import annotations

import logging

from fastmcp import FastMCP

from src.core.container import AppContainer
from src.mcp.auth import build_auth_provider as _build_auth_provider
from src.mcp.prompts import register_prompts
from src.mcp.registration import STATE as _REG_STATE
from src.mcp.registration import bootstrap as _bootstrap_registration
from src.mcp.registration import compute_hidden_groups as _compute_hidden_groups
from src.mcp.resources import register_resources
from src.mcp.runtime import server_lifespan
from src.mcp.tool_catalog import TOOL_ALIASES as _TOOL_ALIASES
from src.mcp.tool_catalog import TOOL_METADATA as _TOOL_METADATA
from src.mcp.tools.support import check_write_policy as _check_write_policy
from src.mcp.tools.support import get_container as _support_get_container
from src.services.file_parser import parse_file, parse_url  # re-export for tests/patches
from src.utils.config import Config
from src.version import VERSION

logger = logging.getLogger(__name__)

# ---- Container (test-patch surface: assign ``_container`` / ``_get_container``) ----

_container = None  # mirrored for tests that assign mcp_server._container


def _sync_container_from_runtime():
    global _container
    from src.mcp import runtime as _rt

    _container = _rt.get_raw_container()


def _get_container() -> AppContainer:
    """获取 Container 实例（lifespan 未触发时延迟创建，主要用于测试）"""
    global _container
    from src.mcp import runtime as _rt

    if _container is not None:
        _rt.set_container(_container)
        return _container
    raw = _rt.get_raw_container()
    if raw is not None:
        _rt.set_container(None)
    c = _rt.get_container()
    _container = c
    return c


# Keep support.get_container aligned with server patch surface
# (support already honors server._container via sys.modules)

mcp = FastMCP(
    name="ShineHeKnowledge",
    version=VERSION,
    lifespan=server_lifespan,
    auth=_build_auth_provider(),
)

# ---- Domain tools (register ToolDefinitions on import) + public re-exports ----
from src.mcp.tools.exports import *  # noqa: E402, F403
from src.mcp.tools.exports import _do_ask  # noqa: E402, F401

# ---- Prompts & resources ----
register_prompts(mcp)
register_resources(mcp)

# Re-export prompt/resource callables for tests
kb_agent_research = register_prompts.kb_agent_research  # type: ignore[attr-defined]
kb_safe_update = register_prompts.kb_safe_update  # type: ignore[attr-defined]
kb_import_and_verify = register_prompts.kb_import_and_verify  # type: ignore[attr-defined]
kb_query_with_sources = register_prompts.kb_query_with_sources  # type: ignore[attr-defined]
knowledge_qa_prompt = register_prompts.knowledge_qa_prompt  # type: ignore[attr-defined]
get_knowledge_resource = register_resources.get_knowledge_resource  # type: ignore[attr-defined]
get_tags_resource = register_resources.get_tags_resource  # type: ignore[attr-defined]
get_stats_resource = register_resources.get_stats_resource  # type: ignore[attr-defined]

# ---- Profile registration ----
_registration = _bootstrap_registration(mcp)

# Compatibility module-level names read by older tests / introspection
_CURRENT_PROFILE = _registration.profile
_EXPERIMENTAL_ENABLED = _registration.experimental_enabled
_ENABLE_ALIASES = _registration.aliases_enabled
_VISIBLE_TOOL_NAMES = _registration.visible_tool_names
_REGISTERED_TOOL_ALIASES = _registration.registered_aliases
_HIDDEN_BY_POLICY = _registration.hidden_by_policy
_EFFECTIVE_SETTINGS = _registration.effective_settings


def main(argv=None):
    """CLI entry used by some launchers; prefer ``src.mcp_cli``."""
    from src.mcp_cli import main as _cli_main

    return _cli_main(argv)


__all__ = [
    "Config",
    "VERSION",
    "_CURRENT_PROFILE",
    "_ENABLE_ALIASES",
    "_EXPERIMENTAL_ENABLED",
    "_REGISTERED_TOOL_ALIASES",
    "_TOOL_ALIASES",
    "_TOOL_METADATA",
    "_VISIBLE_TOOL_NAMES",
    "_build_auth_provider",
    "_check_write_policy",
    "_compute_hidden_groups",
    "_container",
    "_do_ask",
    "_get_container",
    "get_knowledge_resource",
    "get_stats_resource",
    "get_tags_resource",
    "kb_agent_research",
    "kb_import_and_verify",
    "kb_query_with_sources",
    "kb_safe_update",
    "knowledge_qa_prompt",
    "main",
    "mcp",
]
