"""Profile-based tool registration and runtime capability state."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastmcp import FastMCP

from src.mcp.aliases import register_aliases
from src.mcp.policies import resolve_tool_selection_kwargs
from src.mcp.tool_catalog import TOOL_ALIASES
from src.mcp.tool_profiles import EXPERIMENTAL_GROUPS
from src.mcp.tool_registry import get_definitions, list_hidden_by_policy, register_tools, select_tools
from src.utils.config import Config
from src.utils.knowledge_settings import resolve_effective_knowledge_settings

logger = logging.getLogger(__name__)


@dataclass
class RegistrationState:
    profile: str
    experimental_enabled: bool
    aliases_enabled: bool
    visible_tool_names: set[str] = field(default_factory=set)
    registered_aliases: dict[str, str] = field(default_factory=dict)
    hidden_by_policy: list[str] = field(default_factory=list)
    effective_settings: Any = None


# Populated by bootstrap(); read by kb_capabilities etc.
STATE: RegistrationState | None = None


def compute_hidden_groups(tool_summaries_list: list[dict]) -> set[str]:
    all_defs = get_definitions()
    visible_names = {t["name"] for t in tool_summaries_list if "." not in t["name"]}
    visible_groups: set[str] = set()
    for n in visible_names:
        d = all_defs.get(n)
        if d:
            visible_groups.add(d.group)
    return set(EXPERIMENTAL_GROUPS - visible_groups)


def bootstrap(mcp: FastMCP) -> RegistrationState:
    """Select tools for current profile, register on FastMCP, store STATE."""
    global STATE
    settings = resolve_effective_knowledge_settings()
    profile = settings.mcp_tool_profile
    experimental = bool(Config.get("mcp.experimental_tools_enabled", False))
    enable_aliases = bool(
        Config.get("mcp.enable_legacy_aliases", profile == "legacy")
    )
    selection_kwargs = resolve_tool_selection_kwargs(settings)
    selected = select_tools(
        profile,
        experimental_enabled=experimental,
        **selection_kwargs,
    )
    hidden: list[str] = []
    try:
        hidden = list_hidden_by_policy(
            profile,
            experimental_enabled=experimental,
            **selection_kwargs,
        )
    except Exception:  # noqa: BLE001
        hidden = []

    register_tools(mcp, selected)
    visible = {d.name for d in selected}
    registered_aliases = {
        alias: original
        for alias, original in TOOL_ALIASES.items()
        if enable_aliases and original in visible
    }
    if enable_aliases:
        register_aliases(mcp, get_definitions(), visible)

    state = RegistrationState(
        profile=profile,
        experimental_enabled=experimental,
        aliases_enabled=enable_aliases,
        visible_tool_names=visible,
        registered_aliases=registered_aliases,
        hidden_by_policy=hidden,
        effective_settings=settings,
    )
    STATE = state

    try:
        logger.info(
            "MCP start: knowledge_mode=%s wiki_read=%s authoring=%s profile=%s "
            "write_policy=%s tools=%d hidden_by_policy=%d fallback=raw_retrieval",
            settings.mode,
            settings.wiki_read_enabled,
            settings.authoring_enabled,
            profile,
            settings.mcp_write_policy,
            len(visible),
            len(hidden),
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("MCP start: profile=%s tools=%d (%s)", profile, len(visible), exc)

    return state
