"""operations domain MCP tools (WP2 round-2 extraction from server.py).

Implementations registered via tool_definition side-effect on import.
"""
from __future__ import annotations

import logging
from typing import ParamSpec, TypeVar

from src.mcp.envelopes import (
    ErrorCode,
    fail,
    ok,
)
from src.mcp.tools.support import (
    check_write_policy as _check_write_policy,
)
from src.mcp.tools.support import (
    define_tool as _define_tool,
)
from src.mcp.tools.support import (
    heartbeat as _heartbeat,
)

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


@_define_tool(
    name="create_async_job",
    description="创建异步任务", annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False},
    group="ops", side_effect="write",
)
@_heartbeat
def create_async_job(
    job_type: str,
    params: dict | None = None,
    priority: int = 1,
    max_retries: int = 3,
) -> dict:
    """创建异步任务"""
    _guard = _check_write_policy("create_async_job")
    if _guard:
        return _guard
    from src.services.async_task import AsyncTaskService
    job_id = AsyncTaskService.create_job(job_type, params or {}, priority, max_retries)
    return ok({"job_id": job_id, "status": "pending"})

@_define_tool(
    name="get_async_job",
    description="获取异步任务状态", annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="ops", side_effect="read",
)
@_heartbeat
def get_async_job(job_id: str) -> dict:
    """获取任务状态"""
    from src.services.async_task import AsyncTaskService
    job = AsyncTaskService.get_job(job_id)
    if not job:
        return fail(ErrorCode.JOB_NOT_FOUND, f"任务不存在: {job_id}", job_id=job_id)
    return ok(job.__dict__)

@_define_tool(
    name="list_async_jobs",
    description="列出异步任务", annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="ops", side_effect="read",
)
@_heartbeat
def list_async_jobs(
    status: str | None = None,
    job_type: str | None = None,
    limit: int = 20,
) -> dict:
    """列出任务"""
    from src.services.async_task import AsyncTaskService
    jobs = AsyncTaskService.list_jobs(status, job_type, limit)
    return ok([j.__dict__ for j in jobs], count=len(jobs), limit=limit)

@_define_tool(
    name="cancel_async_job",
    description="取消异步任务", annotations={'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True, 'openWorldHint': False},
    group="ops", side_effect="destructive",
)
@_heartbeat
def cancel_async_job(job_id: str) -> dict:
    """取消任务"""
    _guard = _check_write_policy("cancel_async_job")
    if _guard:
        return _guard
    from src.services.async_task import AsyncTaskService
    success = AsyncTaskService.cancel_job(job_id)
    if success:
        return ok({"success": True, "message": "任务已取消", "job_id": job_id})
    return ok({"success": False, "message": "无法取消（可能已完成或不存在）", "job_id": job_id})
