"""MCP tool selection / write-policy helpers (Phase-3)."""
from __future__ import annotations

from typing import Any

from src.utils.knowledge_settings import resolve_effective_knowledge_settings


def resolve_tool_selection_kwargs(
    settings: Any | None = None,
) -> dict[str, Any]:
    """Kwargs for select_tools() from effective knowledge settings."""
    s = settings if settings is not None else resolve_effective_knowledge_settings()
    return {
        "write_policy": s.mcp_write_policy,
        "knowledge_mode": s.mode,
        "authoring_enabled": s.authoring_enabled,
    }


def resolve_profile_and_experimental() -> tuple[str, bool]:
    from src.utils.config import Config

    settings = resolve_effective_knowledge_settings()
    profile = settings.mcp_tool_profile
    experimental = bool(Config.get("mcp.experimental_tools_enabled", False))
    return profile, experimental
