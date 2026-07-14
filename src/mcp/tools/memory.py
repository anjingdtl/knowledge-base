"""memory domain MCP tools (WP2 round-2 extraction from server.py).

Implementations registered via tool_definition side-effect on import.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, ParamSpec, TypeVar, cast

from src.mcp.envelopes import (
    ErrorCode,
    attach_operation_id,
    dry_run_preview,
    fail,
    ok,
)
from src.mcp.tools.support import (
    check_write_policy as _check_write_policy,
    content_preview as _content_preview,
    define_tool as _define_tool,
    get_container as _get_container,
    heartbeat as _heartbeat,
    op_log as _op_log,
)
from src.services.file_parser import parse_file, parse_url
from src.services.wiki_compiler import try_wiki_compile as _try_wiki_compile
from src.utils.config import Config
from src.version import VERSION

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


@_define_tool(
    name="remember_fact",
    description="记住一个事实、决策、上下文或任务，持久化到知识库。"
    "相同 key 会覆盖已有记忆。category: fact | decision | context | task。",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    group="memory", side_effect="write",
    experimental=True,
)
@_heartbeat
def remember_fact(key: str, value: str, category: str = "fact") -> dict:
    """记住一个事实/决策/上下文/任务。

    Args:
        key: 记忆键名（唯一标识，相同 key 会覆盖）
        value: 记忆内容
        category: 分类 — fact（事实）、decision（决策）、context（上下文）、task（任务）
    """
    _guard = _check_write_policy("remember_fact")
    if _guard:
        return _guard
    result = _get_container().agent_memory.remember_fact(key, value, category)
    log_id = _op_log("remember", "agent_memory", result.get("id", ""), after={
        "key": key, "category": category, "value_preview": _content_preview(value),
    })
    return attach_operation_id(ok(result), log_id)

@_define_tool(
    name="recall_facts",
    description="搜索已记住的事实/决策/上下文/任务。支持全文关键词匹配。",
    annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    group="memory", side_effect="read",
    experimental=True,
)
@_heartbeat
def recall_facts(query: str, category: str | None = None, limit: int = 5) -> dict:
    """搜索已记住的事实/决策。

    Args:
        query: 搜索关键词
        category: 可选分类过滤 (fact | decision | context | task)
        limit: 返回数量上限
    """
    results = _get_container().agent_memory.recall_facts(query, category=category, limit=limit)
    return ok(results, count=len(results), query=query)

@_define_tool(
    name="update_project_context",
    description="更新项目整体上下文描述。Agent 可通过此工具记住项目的全局背景信息，"
    "在后续会话中通过 recall_facts(query='project_context') 回忆。",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    group="memory", side_effect="write",
    experimental=True,
)
@_heartbeat
def update_project_context(summary: str) -> dict:
    """更新项目整体上下文描述。

    Args:
        summary: 项目上下文描述（会覆盖之前的内容）
    """
    _guard = _check_write_policy("update_project_context")
    if _guard:
        return _guard
    result = _get_container().agent_memory.update_project_context(summary)
    log_id = _op_log("update_context", "agent_memory", "", after={
        "summary_preview": _content_preview(summary),
    })
    return attach_operation_id(ok(result), log_id)

@_define_tool(
    name="search_decisions",
    description="搜索架构/技术决策记录（category=decision 的记忆）。",
    annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    group="memory", side_effect="read",
    experimental=True,
)
@_heartbeat
def search_decisions(query: str, limit: int = 5) -> dict:
    """搜索决策记录。

    Args:
        query: 搜索关键词
        limit: 返回数量上限
    """
    results = _get_container().agent_memory.search_decisions(query, limit=limit)
    return ok(results, count=len(results), query=query)

@_define_tool(
    name="summarize_recent_changes",
    description="总结近期知识库变更（记忆 + 操作日志）。可指定时间范围。",
    annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    group="memory", side_effect="read",
    experimental=True,
)
@_heartbeat
def summarize_recent_changes(since_hours: int = 24) -> dict:
    """总结近期知识库变更。

    Args:
        since_hours: 统计最近多少小时的变更（默认 24）
    """
    result = _get_container().agent_memory.summarize_recent_changes(since_hours=since_hours)
    return ok(result)

@_define_tool(
    name="extract_tasks_from_doc",
    description="从文档内容中提取待办任务。使用 LLM 智能提取（如可用），否则启发式匹配。"
    "自动将提取结果存为 category=task 的记忆。",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    group="memory", side_effect="write",
    experimental=True,
)
@_heartbeat
def extract_tasks_from_doc(content: str) -> dict:
    """从文档中提取待办任务并存储。

    Args:
        content: 文档内容文本
    """
    _guard = _check_write_policy("extract_tasks_from_doc")
    if _guard:
        return _guard
    result = _get_container().agent_memory.extract_tasks_from_doc(content)
    return ok(result, tasks_found=result.get("total_found", 0), stored=result.get("stored", 0))

@_define_tool(
    name="delete_memory",
    description="删除 agent_memory 记忆条目（按 item_id 或 key，二选一）。",
    annotations={"destructiveHint": True},
    group="memory", side_effect="destructive",
    experimental=True,
)
@_heartbeat
def delete_memory(item_id: str | None = None, key: str | None = None) -> dict:
    """删除记忆条目（BUG#12：补齐 memory 删除能力）。

    Args:
        item_id: 要删除的记忆条目 ID（与 key 二选一）
        key: 要删除的记忆 key（与 item_id 二选一）
    """
    _guard = _check_write_policy("delete_memory")
    if _guard:
        return _guard
    if not item_id and not key:
        return fail(
            ErrorCode.VALIDATION_ERROR,
            "delete_memory 需要 item_id 或 key 参数（二选一）",
        )
    container = _get_container()
    repo = container.agent_memory_repo
    if item_id:
        existing = repo.get_by_id(item_id)
        if not existing:
            return fail(
                ErrorCode.NOT_FOUND,
                f"记忆条目不存在: {item_id}",
                item_id=item_id,
            )
        deleted_meta = {"key": existing.get("key", ""), "category": existing.get("category", "")}
        log_id = _op_log("delete", "agent_memory", item_id, before=deleted_meta)
        deleted = repo.delete(item_id)
        envelope = ok({
            "item_id": item_id, "deleted": deleted, "message": "记忆条目已删除",
        })
        return attach_operation_id(envelope, log_id)
    else:
        existing = repo.get_by_key(cast(str, key))
        if not existing:
            return fail(
                ErrorCode.NOT_FOUND,
                f"记忆条目不存在（key={key}）",
                key=key,
            )
        deleted_meta = {"key": key, "category": existing.get("category", "")}
        log_id = _op_log("delete", "agent_memory", existing["id"], before=deleted_meta)
        deleted = repo.delete_by_key(cast(str, key))
        envelope = ok({
            "key": key, "deleted": deleted, "message": "记忆条目已删除",
        })
        return attach_operation_id(envelope, log_id)
