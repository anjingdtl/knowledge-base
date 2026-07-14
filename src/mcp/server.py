"""ShineHeKnowledge MCP Server — protocol adapters + tool implementations.

Phase-3: this module is the implementation home; ``src/mcp_server.py`` is a
thin compatibility entrypoint. Runtime/auth/envelopes/policies are extracted
to dedicated modules; tool domain maps live under ``src/mcp/tools/``.

安全说明：MCP 工具通过 stdio 或本地 HTTP 传输运行，信任调用方（如 Claude Desktop）。
所有写操作（create/update/delete/wiki_*）不做额外认证，依赖 MCP 传输层的信任模型。
REST API 层（routes.py）则需要 Bearer Token 认证。

Phase 0+1 重构（Sprint 1）：所有 MCP 工具统一返回 envelope：
    {"ok": true, "data": ..., "meta": ..., "operation_id": "..."}
    {"ok": false, "error": {"code": "NOT_FOUND", "message": "..."}}
"""
from __future__ import annotations

import functools
import json
import logging
import os
from typing import Callable, ParamSpec, TypeVar, cast

from fastmcp import FastMCP

from src.core.container import AppContainer
from src.mcp.aliases import register_aliases as _register_aliases
from src.mcp.auth import StaticTokenVerifier as _StaticTokenVerifier
from src.mcp.auth import build_auth_provider as _build_auth_provider
from src.mcp.envelopes import (
    ErrorCode,
    attach_operation_id,
    dry_run_preview,
    fail,
    ok,
)
from src.mcp.policies import resolve_tool_selection_kwargs
from src.mcp.runtime import server_lifespan
from src.mcp.tool_profiles import EXPERIMENTAL_GROUPS as _EXP_GROUPS
from src.mcp.tool_registry import get_definitions, register_tools, select_tools
from src.services.file_parser import parse_file, parse_url
from src.services.mcp_heartbeat import beat
from src.services.wiki_compiler import try_wiki_compile as _try_wiki_compile
from src.utils.config import Config
from src.utils.knowledge_settings import resolve_effective_knowledge_settings
from src.version import VERSION

WIKI_SEARCH_LIMIT = 3  # Wiki 结构化知识搜索结果上限，可通过配置覆盖
logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")

# ---- Container (delegates to runtime; keep module-level for tests that patch) ----

_container = None  # mirrored for tests that assign mcp_server._container


def _sync_container_from_runtime():
    global _container
    from src.mcp import runtime as _rt
    _container = _rt.get_raw_container()


# Prefer runtime get_container; keep name _get_container for call sites / patches
def _get_container() -> AppContainer:
    """获取 Container 实例（lifespan 未触发时延迟创建，主要用于测试）"""
    global _container
    from src.mcp import runtime as _rt

    # Module-level _container is the test/patch surface (conftest sets it to None).
    if _container is not None:
        _rt.set_container(_container)
        return _container
    # Keep runtime in sync when tests reset _container to None
    raw = _rt.get_raw_container()
    if raw is not None:
        # Stale runtime after _container=None — drop it so we recreate on fresh DB
        _rt.set_container(None)
    c = _rt.get_container()
    _container = c
    return c


mcp = FastMCP(
    name="ShineHeKnowledge",
    version=VERSION,
    lifespan=server_lifespan,
    auth=_build_auth_provider(),
)


# ---- Domain tool modules (register ToolDefinitions on import) ----
from src.mcp.tools import administration as _admin_tools  # noqa: F401
from src.mcp.tools import graph as _graph_tools  # noqa: F401
from src.mcp.tools import ingest as _ingest_tools  # noqa: F401
from src.mcp.tools import memory as _memory_tools  # noqa: F401
from src.mcp.tools import operations as _operations_tools  # noqa: F401
from src.mcp.tools import retrieval as _retrieval_tools  # noqa: F401
from src.mcp.tools import wiki as _wiki_tools  # noqa: F401

# Re-export public tool callables for tests/import compatibility
from src.mcp.tools.administration import (  # noqa: E402
    create,
    delete,
    get_operation_log,
    list_recent_operations,
    preview_operation,
    query_operation_logs,
    restore_knowledge,
    undo_operation,
    update,
)
from src.mcp.tools.graph import (  # noqa: E402
    get_source_graph,
    graph_traverse,
)
from src.mcp.tools.ingest import (  # noqa: E402
    cancel_job,
    create_ingest_job,
    get_job,
    index_path,
    ingest_file,
    ingest_url,
    list_jobs,
    list_knowledge,
    reindex_all,
    tags,
)
from src.mcp.tools.memory import (  # noqa: E402
    delete_memory,
    extract_tasks_from_doc,
    recall_facts,
    remember_fact,
    search_decisions,
    summarize_recent_changes,
    update_project_context,
)
from src.mcp.tools.operations import (  # noqa: E402
    cancel_async_job,
    create_async_job,
    get_async_job,
    list_async_jobs,
)
from src.mcp.tools.retrieval import (  # noqa: E402
    _do_ask,
    ask,
    ask_with_query,
    auto_tag,
    execute_query,
    explain_query,
    get_trace,
    kb_capabilities,
    kb_health_check,
    ping,
    read,
    route_query,
    search,
    search_fulltext,
    structured_query,
)
from src.mcp.tools.wiki import (  # noqa: E402
    delete_wiki_page,
    fix_dead_references,
    save_to_wiki,
    wiki_approve,
    wiki_deprecate,
    wiki_lint,
    wiki_list_versions,
    wiki_reject,
    wiki_restore_version,
    wiki_submit_review,
    wiki_workflow_history,
)



# ---- 心跳装饰器 ----

def _heartbeat(fn: Callable[P, R]) -> Callable[P, R]:
    """为 MCP 工具函数添加心跳记录"""
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        beat()
        return fn(*args, **kwargs)
    return wrapper




# ---- Declarative tool definition (profile-based registration) ----

_PENDING_TOOLS: list[dict] = []


def _define_tool(
    *, name: str, description: str, annotations: dict,
    group: str, side_effect: str,
    profiles: frozenset | None = None,
    experimental: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator that records tool metadata without registering with FastMCP."""
    from src.mcp.tool_registry import tool_definition as _td
    return _td(
        name=name, description=description, annotations=annotations,
        group=group, side_effect=side_effect,
        profiles=profiles, experimental=experimental,
    )


def _run_async(coro, timeout: float = 120):
    """安全地运行异步协程，兼容已有/无事件循环两种场景。

    FastMCP 同步工具在线程池中执行，此时无运行中的事件循环。
    但某些传输模式（streamable-http）可能在有事件循环的上下文中
    调用同步工具，因此需要做分支处理。

    Args:
        coro: 异步协程对象
        timeout: 超时秒数（默认 120s）

    Returns:
        协程的返回值

    Raises:
        TimeoutError: 超时
    """
    import asyncio
    import concurrent.futures

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # 已有事件循环时，不能把协程投递回同一个 loop 再同步等待；
        # 那会阻塞当前 loop，导致 future 永远没有机会执行。
        import queue
        import threading

        result_queue: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

        def _runner():
            try:
                result_queue.put((True, asyncio.run(coro)))
            except BaseException as exc:  # noqa: BLE001 - propagate to caller
                result_queue.put((False, exc))

        thread = threading.Thread(target=_runner, name="MCPAsyncBridge", daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise concurrent.futures.TimeoutError()
        success, result = result_queue.get_nowait()
        if success:
            return result
        if isinstance(result, BaseException):
            raise result
        raise RuntimeError(str(result))
    else:
        # 无事件循环（stdio 线程池场景）。用 wait_for 兜住 timeout，否则
        # 协程内部永久挂起时 total_timeout 形同虚设（旧实现丢弃了 timeout）。
        try:
            return asyncio.run(asyncio.wait_for(coro, timeout=timeout))
        except asyncio.TimeoutError as exc:
            raise concurrent.futures.TimeoutError() from exc


def _op_log(operation, target_type, target_id, operator="system", source="mcp",
            before=None, after=None, metadata=None) -> str:
    """便捷操作日志记录。

    Returns:
        log_id（写入成功）或空字符串（失败/未启用）。调用方可塞进 envelope 的
        ``operation_id`` 字段，便于 ``get_operation_log`` 反查。
    """
    try:
        return str(_get_container().operation_log.log(
            operation=operation, target_type=target_type, target_id=target_id,
            operator=operator, source=source,
            before=before, after=after, metadata=metadata,
        ))
    except Exception as exc:
        logger.warning("operation_log failed: %s", exc)
        return ""


def _content_preview(text, max_len=200):
    if not text:
        return ""
    return text[:max_len] + ("..." if len(text) > max_len else "")


# ---- MCP 写操作安全守卫 ----

# 写操作工具名称集合（用于 _check_write_policy 快速判断）
_WRITE_TOOLS = {
    "create", "update", "delete", "restore_knowledge",
    "ingest_file", "ingest_url", "create_ingest_job", "index_path",
    "save_to_wiki",
    "wiki_submit_review", "wiki_approve", "wiki_reject",
    "wiki_deprecate", "wiki_restore_version",
    "create_async_job",
    "undo_operation", "fix_dead_references",
    "cancel_job", "cancel_async_job", "reindex_all",
    "remember_fact", "update_project_context", "extract_tasks_from_doc",
}

# 破坏性操作（更严格）
_DESTRUCTIVE_TOOLS = {"delete", "cancel_job", "cancel_async_job", "reindex_all"}


def _check_write_policy(tool_name: str, *, dry_run: bool = False) -> dict | None:
    """检查写操作是否被当前安全策略允许。

    Returns:
        None: 允许执行
        dict: 拒绝执行时返回 fail envelope
    """
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
        # 未配置策略时，stdio 向后兼容放行；HTTP 由 allow_http_write 控制。
        return None

    if policy == "disabled":
        return fail(ErrorCode.PERMISSION_DENIED, "写操作已被安全策略禁用 (mcp.write_policy=disabled)")

    if policy == "preview_only":
        return fail(ErrorCode.PERMISSION_DENIED,
                     "当前策略仅允许预览 (mcp.write_policy=preview_only)，请使用 preview_operation 工具进行 dry_run")

    if policy == "token_required":
        if transport in {"streamable-http", "sse"}:
            expected_token = Config.get("mcp.auth_token", "")
            if not expected_token:
                logger.warning("mcp.write_policy=token_required 但未配置 mcp.auth_token，写操作将被拒绝")
                return fail(ErrorCode.PERMISSION_DENIED, "MCP 写操作需要认证 token 但未配置 auth_token")
            # 注意: 实际 token 校验需要在传输层实现，这里做配置层面的守卫

    if policy == "local_confirm":
        # stdio 模式下放行（客户端本身就是本地确认）
        if transport in {"streamable-http", "sse"}:
            return fail(ErrorCode.PERMISSION_DENIED,
                         "HTTP 模式下 local_confirm 策略要求通过本地 GUI 确认")

    return None  # 允许


















# ---- Tools ----














































# ---- Phase 5 / Sprint 4: 大文件异步任务 ----















# ---- Wiki Workflow MCP Tools ----

















# ---- Async Jobs MCP Tools ----















# ---- Sprint 2: Agentic Query 入口 ----









@mcp.prompt(name="kb_agent_research", description="Standard MCP-first research workflow.")
def kb_agent_research(question: str) -> str:
    return (
        "Use the knowledge base through MCP tools only. Treat every tool result as an envelope.\n\n"
        f"Research question: {question}\n\n"
        "Required flow:\n"
        "1. Call kb_capabilities and inspect recommended_flows and limits.\n"
        "2. Call route_query with the research question.\n"
        "3. If route_query returns structured or graph intent, call execute_query with the returned query_spec; otherwise call ask.\n"
        "4. Call get_source_graph with the returned sources or block_ids.\n"
        "5. Call read for the most important knowledge_id values, using include_blocks=true when block evidence matters.\n"
        "6. Answer with source titles, block ids, warnings, and any truncation noted in meta.\n"
    )


@mcp.prompt(name="kb_safe_update", description="Safe audited update workflow.")
def kb_safe_update(item_id: str, fields: dict) -> str:
    fields_json = json.dumps(fields or {}, ensure_ascii=False, indent=2)
    return (
        "Perform a safe knowledge-base update. Do not write before previewing.\n\n"
        f"Target item_id: {item_id}\n"
        f"Requested fields:\n{fields_json}\n\n"
        "Required flow:\n"
        "1. Call read(item_id, include_blocks=true, include_embedding_preview=true).\n"
        "2. Call preview_operation(operation=\"update\", item_id=item_id, ...fields).\n"
        "3. Call update(item_id=item_id, ...fields, dry_run=true) and compare would_change with the request.\n"
        "4. If the preview is acceptable, call update(item_id=item_id, ...fields).\n"
        "5. Store the returned operation_id and call get_operation_log(operation_id).\n"
        "6. Report the operation_id and tell the user undo_operation(operation_id) can revert supported changes.\n"
    )


@mcp.prompt(name="kb_import_and_verify", description="Import a file and verify indexed evidence.")
def kb_import_and_verify(file_path: str) -> str:
    return (
        "Import a file into the knowledge base and verify it before answering.\n\n"
        f"File path: {file_path}\n\n"
        "Required flow:\n"
        "1. Call kb_capabilities and inspect ingest limits.\n"
        "2. Call ingest_file(file_path, dry_run=true) when a preview is useful.\n"
        "3. For large files, call create_ingest_job(file_path=file_path); for small files, call ingest_file(file_path=file_path).\n"
        "4. If a job_id is returned, poll get_job(job_id) until completed, failed, or cancelled. Use list_jobs if the job id is lost.\n"
        "5. Use cancel_job only when the user asks to stop an active job.\n"
        "6. Verify imported content with structured_query, then ask with sources.\n"
        "7. Report created_items, skipped_items, failed_items, block_count, and operation_id when present.\n"
    )


@mcp.prompt(name="kb_query_with_sources", description="Answer with block-level sources and graph provenance.")
def kb_query_with_sources(question: str) -> str:
    return (
        "Answer the question with explicit source evidence.\n\n"
        f"Question: {question}\n\n"
        "Required flow:\n"
        "1. Call route_query(question).\n"
        "2. Call ask(question, include_graph=true, include_context=true).\n"
        "3. Call get_source_graph with ask.data.sources or their block_id values.\n"
        "4. Call read for the cited knowledge_id values, using include_blocks=true and include_embedding_preview=true when useful.\n"
        "5. Final answer must mention source titles and block_id values. If sources are weak or warnings are present, say so.\n"
    )


# ---- Resources ----

@mcp.resource("kb://knowledge/{item_id}")
def get_knowledge_resource(item_id: str) -> str:
    """获取指定知识条目的完整内容。"""
    item = _get_container().db.get_knowledge(item_id)
    if not item:
        return json.dumps(
            {"ok": False, "error": {"code": ErrorCode.NOT_FOUND,
                                    "message": f"知识条目不存在: {item_id}",
                                    "details": {"item_id": item_id}}},
            ensure_ascii=False,
        )
    return json.dumps({"ok": True, "data": item}, ensure_ascii=False, indent=2)


@mcp.resource("kb://tags")
def get_tags_resource() -> str:
    """获取知识库中所有标签。"""
    tags_list = _get_container().db.get_all_tags()
    return json.dumps(
        {"ok": True, "data": {"tags": tags_list, "count": len(tags_list)}},
        ensure_ascii=False, indent=2,
    )


@mcp.resource("kb://stats")
def get_stats_resource() -> str:
    """获取知识库统计信息。"""
    c = _get_container()
    try:
        chunk_count = c.block_store.count()
    except Exception:
        chunk_count = 0
    payload = {
        "ok": True,
        "data": {
            "knowledge_items": c.db.count_knowledge(),
            "vector_chunks": chunk_count,
            "tags": len(c.db.get_all_tags()),
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)




# ---- Capabilities (Sprint 1 新增) ----





# ---- Operation Log Query ----



# ---- Phase 4 / Sprint 3: 写操作安全闭环 ----















# ---- Phase 4: Tool Schema 标准化 ----

_TOOL_METADATA = {
    # --- ops.* ---
    "ping":            {"group": "ops",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "连通性检测"},
    "kb_capabilities":  {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "能力清单"},
    # --- kb.* ---
    "search":           {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "语义搜索"},
    "search_fulltext":  {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "全文搜索"},
    "ask":              {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "RAG 问答"},
    "ask_with_query":   {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "QuerySpec RAG"},
    "create":           {"group": "kb",  "side_effect": "write",      "requires_confirmation": False, "short_desc": "创建知识"},
    "read":             {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "读取知识"},
    "update":           {"group": "kb",  "side_effect": "write",      "requires_confirmation": False, "short_desc": "更新知识"},
    "delete":           {"group": "kb",  "side_effect": "destructive","requires_confirmation": True,  "short_desc": "删除知识"},
    "restore_knowledge":{"group": "kb",  "side_effect": "write",      "requires_confirmation": False, "short_desc": "恢复知识"},
    "reindex_all":      {"group": "kb",  "side_effect": "destructive","requires_confirmation": True,  "short_desc": "重建索引"},
    "list_knowledge":   {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "列出知识"},
    "index_path":       {"group": "kb",  "side_effect": "write",      "requires_confirmation": False, "short_desc": "索引路径"},
    "tags":             {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "列出标签"},
    "ingest_file":      {"group": "kb",  "side_effect": "write",      "requires_confirmation": False, "short_desc": "导入文件"},
    "ingest_url":       {"group": "kb",  "side_effect": "write",      "requires_confirmation": False, "short_desc": "导入网页"},
    "create_ingest_job":{"group": "kb",  "side_effect": "write",      "requires_confirmation": False, "short_desc": "创建导入任务"},
    "get_job":          {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "查询任务"},
    "list_jobs":        {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "列出任务"},
    "cancel_job":       {"group": "kb",  "side_effect": "destructive","requires_confirmation": True,  "short_desc": "取消任务"},
    "route_query":      {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "路由分析"},
    "execute_query":    {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "执行 DSL"},
    "structured_query": {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "结构化查询"},
    "explain_query":    {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "查询解释"},
    "get_source_graph": {"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "来源图谱"},
    "preview_operation":{"group": "kb",  "side_effect": "read",       "requires_confirmation": False, "short_desc": "预览操作"},
    # --- wiki.* ---
    "save_to_wiki":         {"group": "wiki", "side_effect": "write", "requires_confirmation": False, "short_desc": "保存到 Wiki"},
    "wiki_lint":            {"group": "wiki", "side_effect": "read",  "requires_confirmation": False, "short_desc": "Wiki 体检"},
    "fix_dead_references":  {"group": "wiki", "side_effect": "write", "requires_confirmation": False, "short_desc": "修复死链"},
    "wiki_submit_review":   {"group": "wiki", "side_effect": "write", "requires_confirmation": False, "short_desc": "提交审核"},
    "wiki_approve":         {"group": "wiki", "side_effect": "write", "requires_confirmation": False, "short_desc": "审批通过"},
    "wiki_reject":          {"group": "wiki", "side_effect": "write", "requires_confirmation": False, "short_desc": "驳回"},
    "wiki_deprecate":       {"group": "wiki", "side_effect": "write", "requires_confirmation": False, "short_desc": "弃用"},
    "wiki_workflow_history":{"group": "wiki", "side_effect": "read",  "requires_confirmation": False, "short_desc": "工作流历史"},
    "wiki_list_versions":   {"group": "wiki", "side_effect": "read",  "requires_confirmation": False, "short_desc": "版本列表"},
    "wiki_restore_version": {"group": "wiki", "side_effect": "write", "requires_confirmation": False, "short_desc": "恢复版本"},
    "delete_wiki_page":     {"group": "wiki", "side_effect": "destructive","requires_confirmation": True,  "short_desc": "删除 Wiki 页面"},
    # --- graph.* ---
    "graph_traverse":       {"group": "graph", "side_effect": "read", "requires_confirmation": False, "short_desc": "图遍历"},
    # --- ops.* ---
    "query_operation_logs": {"group": "ops", "side_effect": "read",       "requires_confirmation": False, "short_desc": "查询日志"},
    "get_operation_log":    {"group": "ops", "side_effect": "read",       "requires_confirmation": False, "short_desc": "获取日志"},
    "undo_operation":       {"group": "ops", "side_effect": "write",      "requires_confirmation": False, "short_desc": "撤销操作"},
    "list_recent_operations":{"group": "ops", "side_effect": "read",      "requires_confirmation": False, "short_desc": "最近操作"},
    "kb_health_check":      {"group": "ops", "side_effect": "read",       "requires_confirmation": False, "short_desc": "健康检查"},
    "auto_tag":             {"group": "ops", "side_effect": "write",      "requires_confirmation": True,  "short_desc": "自动打标"},
    "get_trace":            {"group": "ops", "side_effect": "read",       "requires_confirmation": False, "short_desc": "链路追踪"},
    "create_async_job":     {"group": "ops", "side_effect": "write",      "requires_confirmation": False, "short_desc": "创建任务"},
    "get_async_job":        {"group": "ops", "side_effect": "read",       "requires_confirmation": False, "short_desc": "获取任务"},
    "list_async_jobs":      {"group": "ops", "side_effect": "read",       "requires_confirmation": False, "short_desc": "列出任务"},
    "cancel_async_job":     {"group": "ops", "side_effect": "destructive","requires_confirmation": True,  "short_desc": "取消任务"},
    # --- memory.* (Phase 4.2) ---
    "remember_fact":        {"group": "memory", "side_effect": "write", "requires_confirmation": False, "short_desc": "记住事实"},
    "recall_facts":         {"group": "memory", "side_effect": "read",  "requires_confirmation": False, "short_desc": "搜索记忆"},
    "update_project_context":{"group": "memory", "side_effect": "write", "requires_confirmation": False, "short_desc": "更新项目上下文"},
    "search_decisions":     {"group": "memory", "side_effect": "read",  "requires_confirmation": False, "short_desc": "搜索决策"},
    "summarize_recent_changes":{"group": "memory", "side_effect": "read","requires_confirmation": False, "short_desc": "变更总结"},
    "extract_tasks_from_doc":{"group": "memory", "side_effect": "write", "requires_confirmation": False, "short_desc": "提取任务"},
    "delete_memory":         {"group": "memory", "side_effect": "destructive","requires_confirmation": True, "short_desc": "删除记忆"},
}

# 工具分组别名映射: namespaced_name → original_function_name
_TOOL_ALIASES = {
    # kb.* — Core knowledge base operations
    "kb.search": "search",
    "kb.search_fulltext": "search_fulltext",
    "kb.ask": "ask",
    "kb.ask_with_query": "ask_with_query",
    "kb.create": "create",
    "kb.read": "read",
    "kb.update": "update",
    "kb.delete": "delete",
    "kb.restore": "restore_knowledge",
    "kb.reindex": "reindex_all",
    "kb.list": "list_knowledge",
    "kb.tags": "tags",
    "kb.ingest_file": "ingest_file",
    "kb.ingest_url": "ingest_url",
    "kb.preview": "preview_operation",
    "kb.capabilities": "kb_capabilities",
    "kb.route_query": "route_query",
    "kb.execute_query": "execute_query",
    "kb.structured_query": "structured_query",
    "kb.explain_query": "explain_query",
    "kb.get_source_graph": "get_source_graph",
    "kb.get_job": "get_job",
    "kb.list_jobs": "list_jobs",
    "kb.cancel_job": "cancel_job",
    "kb.create_ingest_job": "create_ingest_job",
    # wiki.*
    "wiki.save": "save_to_wiki",
    "wiki.lint": "wiki_lint",
    "wiki.fix_dead_refs": "fix_dead_references",
    "wiki.submit_review": "wiki_submit_review",
    "wiki.approve": "wiki_approve",
    "wiki.reject": "wiki_reject",
    "wiki.deprecate": "wiki_deprecate",
    "wiki.history": "wiki_workflow_history",
    "wiki.list_versions": "wiki_list_versions",
    "wiki.restore_version": "wiki_restore_version",
    "wiki.delete": "delete_wiki_page",
    # graph.*
    "graph.traverse": "graph_traverse",
    # ops.* — Operations
    "ops.ping": "ping",
    "ops.query_logs": "query_operation_logs",
    "ops.get_log": "get_operation_log",
    "ops.undo": "undo_operation",
    "ops.list_recent": "list_recent_operations",
    "ops.create_job": "create_async_job",
    "ops.get_job": "get_async_job",
    "ops.list_jobs": "list_async_jobs",
    "ops.cancel_job": "cancel_async_job",
    # memory.* — Agent Memory tools
    "memory.remember": "remember_fact",
    "memory.recall": "recall_facts",
    "memory.update_context": "update_project_context",
    "memory.search_decisions": "search_decisions",
    "memory.summarize_changes": "summarize_recent_changes",
    "memory.extract_tasks": "extract_tasks_from_doc",
    "memory.delete": "delete_memory",
}


# ---- Phase 4.2: Agent Memory Tools ----


















# ---- Prompts ----


# ---- Profile-based registration ----

# Determine current profile from the shared effective settings contract.
_EFFECTIVE_SETTINGS = resolve_effective_knowledge_settings()
_CURRENT_PROFILE = _EFFECTIVE_SETTINGS.mcp_tool_profile
_EXPERIMENTAL_ENABLED = bool(Config.get("mcp.experimental_tools_enabled", False))
_ENABLE_ALIASES = bool(
    Config.get("mcp.enable_legacy_aliases", _CURRENT_PROFILE == "legacy")
)

# Select and register tools based on profile + write/authoring policy (Phase 6)
def _resolve_tool_selection_kwargs() -> dict:
    return resolve_tool_selection_kwargs(_EFFECTIVE_SETTINGS)


_TOOL_SELECTION_KWARGS = _resolve_tool_selection_kwargs()
_selected_tools = select_tools(
    _CURRENT_PROFILE,
    experimental_enabled=_EXPERIMENTAL_ENABLED,
    **_TOOL_SELECTION_KWARGS,
)
_HIDDEN_BY_POLICY = []
try:
    from src.mcp.tool_registry import list_hidden_by_policy as _list_hidden
    _HIDDEN_BY_POLICY = _list_hidden(
        _CURRENT_PROFILE,
        experimental_enabled=_EXPERIMENTAL_ENABLED,
        **_TOOL_SELECTION_KWARGS,
    )
except Exception:  # noqa: BLE001
    _HIDDEN_BY_POLICY = []

register_tools(mcp, _selected_tools)
_VISIBLE_TOOL_NAMES = {d.name for d in _selected_tools}

# Startup diagnostics (Phase 6)
try:
    logger.info(
        "MCP start: knowledge_mode=%s wiki_read=%s authoring=%s profile=%s "
        "write_policy=%s tools=%d hidden_by_policy=%d fallback=raw_retrieval",
        _EFFECTIVE_SETTINGS.mode,
        _EFFECTIVE_SETTINGS.wiki_read_enabled,
        _EFFECTIVE_SETTINGS.authoring_enabled,
        _CURRENT_PROFILE,
        _EFFECTIVE_SETTINGS.mcp_write_policy,
        len(_VISIBLE_TOOL_NAMES),
        len(_HIDDEN_BY_POLICY),
    )
except Exception as _startup_exc:  # noqa: BLE001
    logger.info("MCP start: profile=%s tools=%d (%s)", _CURRENT_PROFILE, len(_VISIBLE_TOOL_NAMES), _startup_exc)
_REGISTERED_TOOL_ALIASES = {
    alias_name: original_name
    for alias_name, original_name in _TOOL_ALIASES.items()
    if _ENABLE_ALIASES and original_name in _VISIBLE_TOOL_NAMES
}

# Register aliases if enabled
if _ENABLE_ALIASES:
    _register_aliases(mcp, get_definitions(), _VISIBLE_TOOL_NAMES)


def _compute_hidden_groups(tool_summaries_list):
    """Compute which experimental groups are hidden."""
    all_defs = get_definitions()
    visible_names = {t["name"] for t in tool_summaries_list if "." not in t["name"]}
    visible_groups = set()
    for n in visible_names:
        d = all_defs.get(n)
        if d:
            visible_groups.add(d.group)
    return _EXP_GROUPS - visible_groups


@mcp.prompt(name="kb_qa", description="知识库问答提示模板")
def knowledge_qa_prompt(question: str) -> str:
    return (
        "你是一个专业的知识库助手。请基于知识库中的内容准确回答用户问题。"
        "回答时请标注引用的知识来源，如果知识库中没有相关信息请明确说明。\n\n"
        f"用户问题：{question}"
    )
