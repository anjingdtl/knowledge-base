"""Shared MCP tool support — container, policy, registration helpers.

Domain modules import from here instead of src.mcp.server to avoid cycles.
"""
from __future__ import annotations

import functools
import logging
import os
from typing import Any, Callable, ParamSpec, TypeVar

from src.core.container import AppContainer
from src.mcp.envelopes import ErrorCode, fail
from src.mcp.tool_registry import tool_definition
from src.services.mcp_heartbeat import beat
from src.utils.config import Config

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


def get_container() -> AppContainer:
    """MCP AppContainer (runtime-backed; test-patchable via server._container).

    Uses ``sys.modules`` lookup to avoid import cycles when domain tools are
    imported from ``server.py`` during module init.
    """
    import sys

    from src.mcp import runtime as rt

    server_mod = sys.modules.get("src.mcp.server")
    if server_mod is not None:
        # Honor test patches that assign server._container directly
        local = getattr(server_mod, "_container", None)
        if local is not None:
            rt.set_container(local)
            return local
    return rt.get_container()


def heartbeat(fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        beat()
        return fn(*args, **kwargs)

    return wrapper


def define_tool(
    *,
    name: str,
    description: str,
    annotations: dict,
    group: str,
    side_effect: str,
    profiles: frozenset | None = None,
    experimental: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    return tool_definition(
        name=name,
        description=description,
        annotations=annotations,
        group=group,
        side_effect=side_effect,
        profiles=profiles,
        experimental=experimental,
    )


def content_preview(text: Any, max_len: int = 200) -> str:
    if not text:
        return ""
    s = str(text)
    return s[:max_len] + ("..." if len(s) > max_len else "")


def check_write_policy(tool_name: str, *, dry_run: bool = False) -> dict | None:
    """Return fail envelope if write blocked; None if allowed."""
    if dry_run:
        return None

    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if (
        transport in {"streamable-http", "sse"}
        and not bool(Config.get("mcp.allow_http_write", False))
    ):
        return fail(
            ErrorCode.PERMISSION_DENIED,
            "HTTP 模式写操作已禁用，请设置 mcp.allow_http_write=true 后重试",
            tool=tool_name,
        )

    policy = str(Config.get("mcp.write_policy", "")).lower()
    if not policy:
        return None

    if policy == "disabled":
        return fail(
            ErrorCode.PERMISSION_DENIED,
            "写操作已被安全策略禁用 (mcp.write_policy=disabled)",
        )

    if policy == "preview_only":
        return fail(
            ErrorCode.PERMISSION_DENIED,
            "当前策略仅允许预览 (mcp.write_policy=preview_only)，请使用 preview_operation 工具进行 dry_run",
        )

    if policy == "token_required":
        if transport in {"streamable-http", "sse"}:
            expected_token = Config.get("mcp.auth_token", "")
            if not expected_token:
                logger.warning(
                    "mcp.write_policy=token_required 但未配置 mcp.auth_token，写操作将被拒绝",
                )
                return fail(
                    ErrorCode.PERMISSION_DENIED,
                    "MCP 写操作需要认证 token 但未配置 auth_token",
                )

    if policy == "local_confirm":
        if transport in {"streamable-http", "sse"}:
            return fail(
                ErrorCode.PERMISSION_DENIED,
                "HTTP 模式下 local_confirm 策略要求通过本地 GUI 确认",
            )

    return None


def op_log(
    operation,
    target_type,
    target_id,
    operator="system",
    source="mcp",
    before=None,
    after=None,
    metadata=None,
) -> str:
    try:
        return str(
            get_container().operation_log.log(
                operation=operation,
                target_type=target_type,
                target_id=target_id,
                operator=operator,
                source=source,
                before=before,
                after=after,
                metadata=metadata,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("operation_log failed: %s", exc)
        return ""
