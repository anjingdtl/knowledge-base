"""Global AppContainer access — compatibility whitelist only (WP3).

Allowed production call sites:
  - src/mcp/runtime.py (MCP process singleton)
  - src/compatibility/container_access.py (this module)

Other modules should receive AppContainer / Provider explicitly.
``src.core.container.get_active_container`` re-exports this for one release.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.container import AppContainer

_active_container: "AppContainer | None" = None


def get_active_container() -> "AppContainer | None":
    """Return the process-active DI container, if any."""
    return _active_container


def set_active_container(container: "AppContainer | None") -> None:
    """Set or clear the process-active DI container."""
    global _active_container
    _active_container = container
