"""MCP runtime — container lifecycle and heartbeat (Phase-3)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastmcp import FastMCP

from src.core.container import AppContainer, create_container, shutdown_container
from src.services.mcp_heartbeat import beat
from src.utils.config import Config

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_container: AppContainer | None = None
_heartbeat_task: asyncio.Task | None = None


def get_container() -> AppContainer:
    """Get or lazily create the MCP AppContainer (test-friendly fallback)."""
    global _container
    if _container is None:
        _container = create_container()
    return _container


def set_container(container: AppContainer | None) -> None:
    global _container
    _container = container


def get_raw_container() -> AppContainer | None:
    return _container


async def heartbeat_loop() -> None:
    while True:
        beat()
        await asyncio.sleep(10)


@asynccontextmanager
async def server_lifespan(server: FastMCP):
    global _heartbeat_task, _container
    _container = create_container()
    beat()
    from src.services import async_worker

    async_worker.start_worker(
        poll_interval=float(Config.get("jobs.poll_interval", 1.0) or 1.0),
        max_workers=int(Config.get("jobs.max_workers", 2) or 2),
    )
    _heartbeat_task = asyncio.create_task(heartbeat_loop())
    try:
        yield {}
    finally:
        if _heartbeat_task is not None:
            _heartbeat_task.cancel()
        async_worker.stop_worker()
        shutdown_container(_container)
        _container = None


# Back-compat alias used by legacy modules
_get_container = get_container
