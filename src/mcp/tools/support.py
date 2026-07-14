"""Shared MCP tool support — container, policy, registration helpers.

Domain modules import from here instead of src.mcp.server to avoid cycles.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import logging
import os
from typing import Any, Callable, Coroutine, ParamSpec, TypeVar

from src.core.container import AppContainer
from src.mcp.envelopes import ErrorCode, fail
from src.mcp.tool_registry import tool_definition
from src.services.mcp_heartbeat import beat
from src.utils.config import Config

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


def get_container() -> AppContainer:
    """MCP AppContainer (runtime-backed; test-patchable via server).

    Patch surfaces (tests):
      - ``src.mcp.server._container = mock``
      - ``src.mcp.server._get_container = lambda: mock``
      - same on ``src.mcp_server`` (compat re-export)
    """
    import sys

    from src.mcp import runtime as rt

    def _try_module(mod) -> AppContainer | None:
        if mod is None:
            return None
        local = getattr(mod, "_container", None)
        if local is not None:
            rt.set_container(local)
            return local
        getter = getattr(mod, "_get_container", None)
        if not callable(getter):
            return None
        # Test stubs replace _get_container with a lambda / non-server function.
        # Production server._get_container lives in server.py — skip that path
        # here (runtime owns creation) to avoid recursion via support.
        code = getattr(getter, "__code__", None)
        if code is None:
            # Builtin or C function — treat as explicit patch
            return getter()
        filename = code.co_filename or ""
        if code.co_name == "<lambda>" or not filename.endswith("server.py"):
            return getter()
        return None

    for name in ("src.mcp.server", "src.mcp_server"):
        found = _try_module(sys.modules.get(name))
        if found is not None:
            return found

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


def run_async(coro: Coroutine[Any, Any, R], timeout: float | None = None) -> R:
    """Run a coroutine from sync code, even if a loop is already running.

    Uses a worker thread + new event loop when called inside a running loop
    (common under FastMCP / nested ask paths).
    """
    def _drive() -> R:
        if timeout is None:
            return asyncio.run(coro)
        return asyncio.run(asyncio.wait_for(coro, timeout=timeout))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _drive()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_drive)
        wait = None if timeout is None else float(timeout) + 5.0
        return fut.result(timeout=wait)


def check_write_policy(tool_name: str, *, dry_run: bool = False) -> dict | None:
    """Return fail envelope if write blocked; None if allowed.

    Honors test monkeypatches on ``src.mcp.server._check_write_policy`` when present.
    """
    import sys

    server = sys.modules.get("src.mcp.server")
    if server is not None:
        patched = getattr(server, "_check_write_policy", None)
        # Identity differs when tests replace the server attribute
        if callable(patched) and patched is not check_write_policy:
            return patched(tool_name, dry_run=dry_run)

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
