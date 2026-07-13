"""Declarative tool registry with profile-based filtering."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, ParamSpec, TypeVar

from fastmcp import FastMCP

from src.mcp.tool_profiles import (
    ADMIN_TOOLS,
    CORE_TOOLS,
    EXPERIMENTAL_GROUPS,
    EXTENDED_TOOLS,
    PROFILES,
)

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True)
class ToolDefinition:
    """A tool's registration metadata for profile filtering."""
    name: str
    function: Callable[..., Any]
    description: str
    annotations: dict[str, Any]
    group: str           # ops, kb, wiki, graph, memory
    side_effect: str     # read, write, destructive
    profiles: frozenset[str]  # which profiles include this tool
    experimental: bool = False


# Global registry
_DEFINITIONS: dict[str, ToolDefinition] = {}


def _compute_profiles(name: str, group: str, experimental: bool) -> frozenset[str]:
    """Compute which profiles a tool belongs to based on its name and group."""
    if experimental or group in EXPERIMENTAL_GROUPS:
        return frozenset({"legacy"})
    if name in CORE_TOOLS:
        return frozenset({"core", "extended", "admin", "full", "legacy"})
    if name in EXTENDED_TOOLS:
        return frozenset({"extended", "admin", "full", "legacy"})
    if name in ADMIN_TOOLS:
        return frozenset({"admin", "full", "legacy"})
    # Non-experimental tools not in any named set → full + legacy only
    return frozenset({"full", "legacy"})


def tool_definition(
    *, name: str, description: str, annotations: dict[str, Any],
    group: str, side_effect: str, profiles: frozenset[str] | None = None,
    experimental: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to register a tool function with profile metadata."""
    if profiles is None:
        profiles = _compute_profiles(name, group, experimental)

    def decorator(function: Callable[P, R]) -> Callable[P, R]:
        _DEFINITIONS[name] = ToolDefinition(
            name=name, function=function, description=description,
            annotations=annotations, group=group, side_effect=side_effect,
            profiles=profiles, experimental=experimental,
        )
        return function
    return decorator


def get_definitions() -> dict[str, ToolDefinition]:
    """Return a copy of all registered tool definitions."""
    return dict(_DEFINITIONS)


def select_tools(
    profile: str,
    experimental_enabled: bool = False,
    *,
    write_policy: str | None = None,
    knowledge_mode: str | None = None,
    authoring_enabled: bool | None = None,
) -> list[ToolDefinition]:
    """Select tools for a given profile with write/authoring policy (Phase 6).

    Spec §9.2–§9.3:
    - write_policy=disabled → hide write/destructive tools (full/legacy included)
    - authoring tools only when knowledge_mode=authoring AND authoring_enabled
      AND write_policy != disabled
    """
    if profile == "full":
        # full = all non-experimental tools (unless experimental_enabled)
        selected = [d for d in _DEFINITIONS.values()
                    if experimental_enabled or not d.experimental]
    elif profile == "legacy":
        # legacy = all tools (including experimental) — still subject to write_policy
        selected = list(_DEFINITIONS.values())
    else:
        # core/extended/admin: filter by profile membership
        selected = [d for d in _DEFINITIONS.values()
                    if profile in d.profiles
                    and (experimental_enabled or not d.experimental)]

    policy = (write_policy or "").strip().lower()
    if policy == "disabled":
        selected = [
            d for d in selected
            if d.side_effect not in ("write", "destructive")
        ]

    # Authoring / wiki write tools — only filter when mode/flags explicitly set
    # (backward compatible: omit kwargs → no authoring filter)
    if knowledge_mode is not None or authoring_enabled is not None or policy == "disabled":
        from src.utils.knowledge_mode import allows_authoring, resolve_knowledge_mode

        authoring_ok = True
        if policy == "disabled":
            authoring_ok = False
        if authoring_enabled is False:
            authoring_ok = False
        if knowledge_mode is not None:
            try:
                authoring_ok = authoring_ok and allows_authoring(
                    resolve_knowledge_mode(knowledge_mode),
                )
            except Exception:  # noqa: BLE001
                authoring_ok = authoring_ok and str(knowledge_mode) == "authoring"
        if authoring_enabled is True and knowledge_mode is None and policy != "disabled":
            authoring_ok = True

        if not authoring_ok:
            selected = [
                d for d in selected
                if d.group != "wiki" or d.side_effect == "read"
            ]

    return selected


def list_hidden_by_policy(
    profile: str,
    experimental_enabled: bool = False,
    *,
    write_policy: str | None = None,
    knowledge_mode: str | None = None,
    authoring_enabled: bool | None = None,
) -> list[str]:
    """Tool names present in profile but hidden by write/authoring policy."""
    if profile == "full":
        unrestricted = [d for d in _DEFINITIONS.values()
                        if experimental_enabled or not d.experimental]
    elif profile == "legacy":
        unrestricted = list(_DEFINITIONS.values())
    else:
        unrestricted = [d for d in _DEFINITIONS.values()
                        if profile in d.profiles
                        and (experimental_enabled or not d.experimental)]
    filtered = select_tools(
        profile,
        experimental_enabled,
        write_policy=write_policy,
        knowledge_mode=knowledge_mode,
        authoring_enabled=authoring_enabled,
    )
    filtered_names = {d.name for d in filtered}
    unrestricted_names = {d.name for d in unrestricted}
    return sorted(unrestricted_names - filtered_names)


def register_tools(server: FastMCP, definitions: Iterable[ToolDefinition]) -> None:
    """Register a list of tool definitions with a FastMCP server."""
    for d in definitions:
        server.tool(d.function, name=d.name, description=d.description,
                    annotations=d.annotations)


def resolve_tool_profile(config_dict: dict) -> str:
    """Determine the active tool profile from config.

    Rules:
    - If mcp.tool_profile is explicitly set, use it
    - If config has mcp settings but no tool_profile, default to "legacy" (old user)
    - If config has no mcp settings at all, default to "extended" (new user)
    """
    profile: str = config_dict.get("mcp.tool_profile")  # type: ignore[assignment]
    if profile and profile in PROFILES:
        return profile

    # Check if this looks like an old config (has mcp settings but no profile)
    has_mcp_settings = any(
        k.startswith("mcp.") and k != "mcp.tool_profile"
        for k in config_dict
        if config_dict.get(k) is not None and config_dict.get(k) != ""
    )

    if has_mcp_settings:
        return "legacy"
    return "extended"
