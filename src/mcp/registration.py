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


@dataclass
class ExposedToolDefinitions:
    """Single source of truth for tool exposure across stdio / HTTP transports."""

    profile: str
    experimental_enabled: bool
    legacy_aliases_enabled: bool
    write_policy: str
    tool_names: set[str]
    alias_map: dict[str, str]
    selected_definitions: list[Any]
    hidden_by_policy: list[str]
    effective_settings: Any = None

    @property
    def all_exposed_names(self) -> set[str]:
        return set(self.tool_names) | set(self.alias_map.keys())


def compute_hidden_groups(tool_summaries_list: list[dict]) -> set[str]:
    all_defs = get_definitions()
    visible_names = {t["name"] for t in tool_summaries_list if "." not in t["name"]}
    visible_groups: set[str] = set()
    for n in visible_names:
        d = all_defs.get(n)
        if d:
            visible_groups.add(d.group)
    return set(EXPERIMENTAL_GROUPS - visible_groups)


def get_exposed_tool_definitions(
    *,
    profile: str | None = None,
    experimental_enabled: bool | None = None,
    legacy_aliases_enabled: bool | None = None,
    write_policy: str | None = None,
    settings: Any | None = None,
) -> ExposedToolDefinitions:
    """Compute the tool set both transports must expose under the same policy.

    Prefer explicit kwargs for tests; production bootstrap resolves settings/config.
    """
    resolved = settings or resolve_effective_knowledge_settings()
    profile_name = profile if profile is not None else resolved.mcp_tool_profile
    experimental = (
        bool(experimental_enabled)
        if experimental_enabled is not None
        else bool(Config.get("mcp.experimental_tools_enabled", False))
    )
    # Explicit False must win; only fall back to profile==legacy when unset.
    if legacy_aliases_enabled is not None:
        enable_aliases = bool(legacy_aliases_enabled)
    else:
        raw_alias = Config.get("mcp.enable_legacy_aliases", None)
        if raw_alias is None:
            enable_aliases = profile_name == "legacy"
        else:
            enable_aliases = bool(raw_alias)
    policy = (
        write_policy
        if write_policy is not None
        else str(getattr(resolved, "mcp_write_policy", "") or Config.get("mcp.write_policy", "") or "")
    )

    selection_kwargs = resolve_tool_selection_kwargs(resolved)
    selected = select_tools(
        profile_name,
        experimental_enabled=experimental,
        **selection_kwargs,
    )
    hidden: list[str] = []
    try:
        hidden = list_hidden_by_policy(
            profile_name,
            experimental_enabled=experimental,
            **selection_kwargs,
        )
    except Exception:  # noqa: BLE001
        hidden = []

    visible = {d.name for d in selected}
    alias_map = {
        alias: original
        for alias, original in TOOL_ALIASES.items()
        if enable_aliases and original in visible
    }
    return ExposedToolDefinitions(
        profile=profile_name,
        experimental_enabled=experimental,
        legacy_aliases_enabled=enable_aliases,
        write_policy=policy,
        tool_names=visible,
        alias_map=alias_map,
        selected_definitions=list(selected),
        hidden_by_policy=hidden,
        effective_settings=resolved,
    )


def bootstrap(mcp: FastMCP) -> RegistrationState:
    """Select tools for current profile, register on FastMCP, store STATE.

    stdio and streamable-http MUST both call this path (via server module import)
    so exposure stays identical for a given config.
    """
    global STATE
    exposed = get_exposed_tool_definitions()
    register_tools(mcp, exposed.selected_definitions)
    if exposed.legacy_aliases_enabled:
        register_aliases(mcp, get_definitions(), exposed.tool_names)

    state = RegistrationState(
        profile=exposed.profile,
        experimental_enabled=exposed.experimental_enabled,
        aliases_enabled=exposed.legacy_aliases_enabled,
        visible_tool_names=set(exposed.tool_names),
        registered_aliases=dict(exposed.alias_map),
        hidden_by_policy=list(exposed.hidden_by_policy),
        effective_settings=exposed.effective_settings,
    )
    STATE = state

    settings = exposed.effective_settings
    try:
        logger.info(
            "MCP start: knowledge_mode=%s wiki_read=%s authoring=%s profile=%s "
            "write_policy=%s tools=%d aliases=%d hidden_by_policy=%d fallback=raw_retrieval",
            getattr(settings, "mode", None),
            getattr(settings, "wiki_read_enabled", None),
            getattr(settings, "authoring_enabled", None),
            exposed.profile,
            exposed.write_policy,
            len(exposed.tool_names),
            len(exposed.alias_map),
            len(exposed.hidden_by_policy),
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "MCP start: profile=%s tools=%d aliases=%d (%s)",
            exposed.profile,
            len(exposed.tool_names),
            len(exposed.alias_map),
            exc,
        )

    return state
