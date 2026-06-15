"""ShineHeKnowledge MCP Server — 将知识库服务暴露为 MCP 工具

安全说明：MCP 工具通过 stdio 或本地 HTTP 传输运行，信任调用方（如 Claude Desktop）。
所有写操作（create/update/delete/wiki_*）不做额外认证，依赖 MCP 传输层的信任模型。
REST API 层（routes.py）则需要 Bearer Token 认证。

Phase 0+1 重构（Sprint 1）：所有 MCP 工具统一返回 envelope：
    {"ok": true, "data": ..., "meta": ..., "operation_id": "..."}
    {"ok": false, "error": {"code": "NOT_FOUND", "message": "..."}}
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import Callable, ParamSpec, TypeVar

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, TokenVerifier

from src.core.container import AppContainer, create_container, shutdown_container
from src.mcp.aliases import register_aliases as _register_aliases
from src.mcp.tool_profiles import EXPERIMENTAL_GROUPS as _EXP_GROUPS
from src.mcp.tool_registry import get_definitions, register_tools, resolve_tool_profile, select_tools
from src.services.file_parser import parse_file, parse_url
from src.services.mcp_heartbeat import beat
from src.services.wiki_compiler import try_wiki_compile as _try_wiki_compile
from src.utils.config import Config
from src.utils.envelope import (
    ErrorCode,
    attach_operation_id,
    dry_run_preview,
    fail,
    ok,
)
from src.version import VERSION

WIKI_SEARCH_LIMIT = 3  # Wiki 结构化知识搜索结果上限，可通过配置覆盖
logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")

# ---- 心跳后台任务 ----

_heartbeat_task: asyncio.Task | None = None

# ---- Container ----

_container: AppContainer | None = None


def _get_container() -> AppContainer:
    """获取 Container 实例（lifespan 未触发时延迟创建，主要用于测试）"""
    global _container
    if _container is None:
        _container = create_container()
    return _container


async def _heartbeat_loop():
    """每 10 秒写一次心跳，确保 GUI 能感知 MCP 服务存活"""
    while True:
        beat()
        await asyncio.sleep(10)


# ---- Lifespan ----

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
    _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
        yield {}
    finally:
        _heartbeat_task.cancel()
        async_worker.stop_worker()
        shutdown_container(_container)


class _StaticTokenVerifier(TokenVerifier):
    """Verify a configured local bearer token for HTTP MCP transports."""

    def __init__(self, expected_token: str):
        super().__init__()
        self._expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not secrets.compare_digest(token, self._expected_token):
            return None
        return AccessToken(
            token=token,
            client_id="shinehe-mcp",
            scopes=[],
        )


def _build_auth_provider() -> TokenVerifier | None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    policy = str(Config.get("mcp.write_policy", "")).lower()
    if transport not in {"streamable-http", "sse"} or policy != "token_required":
        return None

    token = str(Config.get("mcp.auth_token", "") or "")
    if not token:
        raise RuntimeError(
            "mcp.write_policy=token_required 时必须配置 mcp.auth_token"
        )
    return _StaticTokenVerifier(token)


mcp = FastMCP(
    name="ShineHeKnowledge",
    version=VERSION,
    lifespan=server_lifespan,
    auth=_build_auth_provider(),
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
        # 已有事件循环（streamable-http 等场景）
        with concurrent.futures.ThreadPoolExecutor(max_workers=1):
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=timeout)
    else:
        # 无事件循环（stdio 线程池场景）
        return asyncio.run(coro)


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


def _load_json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _list_blocks_for_page(page_id: str) -> list[dict]:
    rows = _get_container().db.get_conn().execute(
        """SELECT id, parent_id, page_id, content, block_type, properties, order_idx,
                  created_at, updated_at
           FROM blocks
           WHERE page_id = ?
           ORDER BY order_idx ASC, created_at ASC""",
        (page_id,),
    ).fetchall()
    blocks = []
    for row in rows:
        block = dict(row)
        block["properties"] = _load_json_dict(block.get("properties"))
        blocks.append(block)
    return blocks


def _embedding_context_config() -> dict:
    return {
        "enabled": bool(Config.get("rag.embedding_context.enabled", False)),
        "include_parent_chain": bool(
            Config.get("rag.embedding_context.include_parent_chain", True)
        ),
        "include_links": bool(Config.get("rag.embedding_context.include_links", True)),
        "include_siblings": bool(
            Config.get("rag.embedding_context.include_siblings", False)
        ),
        "max_chars": int(Config.get("rag.embedding_context.max_chars", 1200) or 1200),
    }


# ---- Tools ----


@_define_tool(
    name="ping",
    description="轻量级连通性检测（ping）。客户端可用此工具验证 MCP 连接是否存活，"
    "无需访问数据库或 LLM，响应 <10ms。推荐在会话开始时和工具调用前调用。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="ops", side_effect="read",
)
def ping() -> dict:
    """轻量级连通性检测，返回服务状态和时间戳。"""
    import time
    return ok({
        "status": "alive",
        "timestamp": time.time(),
        "version": VERSION,
        "uptime_hint": "ok",
    })


@_define_tool(
    name="search",
    description="基于语义相似度搜索知识库。使用向量嵌入查找与查询含义最相关的知识条目。Wiki 结构化知识优先返回。",
    annotations={"readOnlyHint": True, "openWorldHint": False},
    group="kb", side_effect="read",
)
@_heartbeat
def search(query: str, top_k: int = 5) -> dict:
    """基于语义的向量搜索，查找与查询含义最相关的知识内容。

    Args:
        query: 搜索查询文本，支持自然语言描述
        top_k: 返回结果数量，默认5条
    """
    results = _get_container().search_service.search(query, top_k=top_k)
    return ok(results, total_estimate=len(results), top_k=top_k)


@_define_tool(
    name="search_fulltext",
    description="基于关键词的全文搜索（FTS5）。适用于精确匹配关键词的场景。Wiki 结构化知识优先返回。",
    annotations={"readOnlyHint": True, "openWorldHint": False},
    group="kb", side_effect="read",
)
@_heartbeat
def search_fulltext(query: str, limit: int = 20, offset: int = 0) -> dict:
    """使用 FTS5 全文索引搜索知识库。

    Args:
        query: 搜索关键词
        limit: 返回结果数量上限，默认20
        offset: 分页偏移量，默认0
    """
    container = _get_container()
    db = container.db
    output = []

    # Wiki 结构化知识优先
    wiki_results = db.search_wiki_fts(query, limit=3)
    for wr in wiki_results:
        summary = wr.get("concept_summary", "")
        content_preview = (wr.get("content", "") or "")[:300]
        output.append({
            "source": "wiki",
            "title": wr["title"],
            "summary": summary,
            "text": f"[Wiki] {wr['title']}: {summary}\n{content_preview}",
        })

    # FTS5 知识搜索
    kb_results = db.search_knowledge(query, limit=limit, offset=offset)
    for item in kb_results:
        item["source"] = "knowledge"
        output.append(item)

    has_more = len(kb_results) == limit
    return ok(
        output,
        limit=limit,
        offset=offset,
        next_offset=offset + len(output) if has_more else None,
        truncated=has_more,
        total_estimate=len(output),
    )


@_define_tool(
    name="ask",
    description="向知识库提问，使用 RAG（检索增强生成）流程自动检索相关内容并生成回答。"
    "返回结构化 payload：answer / sources / source_graph / route / query_plan / "
    "block_contexts / warnings。"
    "[耗时提示：通常 5-30 秒，首次调用可能更长，建议客户端超时 ≥ 60s]",
    annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="kb", side_effect="read",
)
@_heartbeat
def ask(
    question: str,
    include_graph: bool = True,
    include_context: bool = True,
    max_sources: int = 5,
    max_graph_nodes: int = 50,
) -> dict:
    """基于知识库的智能问答，返回 7 字段结构化 RAG payload。

    Args:
        question: 用户的问题

    Returns:
        envelope.data 包含：
            - answer: 生成的答复
            - sources: 引用来源列表，每项必含 block_id / knowledge_id / title
            - source_graph: 引用关系图（nodes / edges / truncated / node_count）
            - route: 路由决策（mode / explanation / query_spec / traverse）
            - query_plan: DSL 查询计划（structured 模式时有内容）
            - block_contexts: block_id → block 父链上下文的映射
            - warnings: 检索/生成阶段的告警
            - wiki_context: Wiki 知识上下文（仅当 wiki.enabled=true）
    """
    result = _do_ask(question)
    if max_sources and max_sources > 0:
        result["sources"] = list(result.get("sources", []))[:max_sources]
    if include_graph:
        from src.services.source_graph import build_source_graph
        result["source_graph"] = build_source_graph(
            result.get("sources", []),
            db=_get_container().db,
            max_nodes=max_graph_nodes,
        )
    else:
        result["source_graph"] = {"nodes": [], "edges": [], "truncated": False, "node_count": 0}
    if not include_context:
        result["block_contexts"] = {}
    return ok(
        result,
        source_count=len(result.get("sources", [])),
        warning_count=len(result.get("warnings", [])),
        route_mode=result.get("route", {}).get("mode", "unknown"),
        graph_truncated=result.get("source_graph", {}).get("truncated", False),
    )


def _do_ask(question: str) -> dict:
    return dict(_get_container().rag_pipeline.query(question))


@_define_tool(
    name="create",
    description="创建新的知识条目。自动将内容分块并向量化索引，支持纯文本、Markdown 和代码。"
    "[耗时提示：短文本 2-5 秒，长文本可能 10-30 秒]",
    annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False},
    group="kb", side_effect="write",
)
@_heartbeat
def create(
    title: str,
    content: str,
    tags: list[str] | None = None,
    file_type: str = "txt",
    source_type: str = "manual",
    dry_run: bool = False,
) -> dict:
    """创建一条知识并自动建立向量索引。

    Args:
        title: 知识标题
        content: 知识内容（支持纯文本、Markdown、代码）
        tags: 标签列表
        file_type: 内容类型 - txt（纯文本）、md（Markdown）、code（代码）
        source_type: 来源类型 - manual（手动）、file（文件）、web（网页）
        dry_run: 设为 True 时只预览不执行
    """
    _guard = _check_write_policy("create", dry_run=dry_run)
    if _guard:
        return _guard
    tags = tags or []
    container = _get_container()
    db = container.db
    # 哈希去重
    import hashlib
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = db.get_knowledge_by_hash(content_hash)
    if existing:
        return ok(
            {
                "id": existing["id"],
                "title": existing["title"],
                "message": "内容已存在，跳过导入",
                "skipped": True,
            }
        )

    if dry_run:
        return dry_run_preview(
            {
                "title": title,
                "content_preview": _content_preview(content),
                "tags": tags,
                "source_type": source_type,
                "file_type": file_type,
            }
        )

    item_id = container.file_graph_service.create_page(
        title,
        content,
        tags=tags,
        metadata={"source_type": source_type, "file_type": file_type},
    )
    # Wiki 编译
    _try_wiki_compile(item_id)
    item = db.get_knowledge(item_id) or {"title": title}
    log_id = _op_log("create", "knowledge", item_id, after={
        "title": item["title"], "content_preview": _content_preview(content),
        "tags": tags, "source_type": source_type, "file_type": file_type,
    })
    envelope = ok({
        "id": item_id,
        "title": item["title"],
        "path": item.get("source_path", ""),
        "message": "知识创建成功并已完成索引",
    })
    return attach_operation_id(envelope, log_id)


@_define_tool(
    name="read",
    description="根据 ID 获取指定知识条目的完整信息。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def read(
    item_id: str,
    include_blocks: bool = False,
    include_embedding_preview: bool = False,
    include_effective_properties: bool = False,
    include_linked_summaries: bool = False,
) -> dict:
    """读取指定 ID 的知识条目。

    Args:
        item_id: 知识条目的唯一 ID
    """
    container = _get_container()
    item = container.db.get_knowledge(item_id)
    if not item:
        return fail(ErrorCode.NOT_FOUND, f"知识条目不存在: {item_id}", item_id=item_id)
    if include_blocks or include_embedding_preview or include_effective_properties or include_linked_summaries:
        blocks = _list_blocks_for_page(item_id)
        if include_embedding_preview:
            from src.services.embedding import EmbeddingService
            embedding_service = EmbeddingService(container.config)
            config_snapshot = _embedding_context_config()
            for block in blocks:
                text = embedding_service.build_embedding_text(block)
                block["embedding_preview"] = {
                    "enabled": config_snapshot["enabled"],
                    "text": text,
                    "char_count": len(text),
                    "config": config_snapshot,
                }
        if include_effective_properties:
            service = container.effective_properties
            compute = getattr(service, "_compute_effective_for_block", None)
            for block in blocks:
                block["effective_properties"] = (
                    compute(block["id"]) if compute else service.refresh_block(block["id"])
                )
        if include_linked_summaries:
            from src.services.block_context import BlockContextService
            context_service = BlockContextService(db=container.db, config=container.config)
            max_links = int(Config.get("rag.link_expansion.max_links", 3) or 3)
            get_links = getattr(context_service, "_get_linked_summaries")
            for block in blocks:
                block["linked_summaries"] = get_links(block["id"], max_links)
        item = dict(item)
        item["blocks"] = blocks
    return ok(item)


@_define_tool(
    name="update",
    description="更新已有知识条目的标题、内容或标签。修改内容时会自动创建版本快照。",
    annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False},
    group="kb", side_effect="write",
)
@_heartbeat
def update(
    item_id: str,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """更新指定知识条目。

    Args:
        item_id: 要更新的知识条目 ID
        title: 新标题（可选）
        content: 新内容（可选）
        tags: 新标签列表（可选）
        dry_run: 设为 True 时只预览变更不执行
    """
    _guard = _check_write_policy("update", dry_run=dry_run)
    if _guard:
        return _guard
    container = _get_container()
    db = container.db
    existing = db.get_knowledge(item_id)
    if not existing:
        return fail(ErrorCode.NOT_FOUND, f"知识条目不存在: {item_id}", item_id=item_id)
    fields: dict = {}
    if title is not None:
        fields["title"] = title
    if content is not None:
        fields["content"] = content
    if tags is not None:
        fields["tags"] = tags
    if not fields:
        return ok({"message": "未提供需要更新的字段", "no_op": True})

    import json as _json
    changes: dict = {}
    for k, v in fields.items():
        old_val = existing.get(k)
        if isinstance(old_val, str) and k == "tags":
            try:
                old_val = _json.loads(old_val)
            except Exception:
                pass
        if k == "content":
            changes["content"] = {
                "before": _content_preview(old_val or ""),
                "after": _content_preview(v or ""),
            }
        else:
            changes[k] = {"before": old_val, "after": v}

    if dry_run:
        return dry_run_preview({"item_id": item_id, "changes": changes}, item_id=item_id)

    blocks = fields["content"] if "content" in fields else container.file_graph_service.read_page(item_id).blocks
    container.file_graph_service.update_page(item_id, blocks, metadata=fields)
    updated = db.get_knowledge(item_id) or {}
    log_id = _op_log("update", "knowledge", item_id, before={
        k: v["before"] for k, v in changes.items()
    }, after={
        k: v["after"] for k, v in changes.items()
    })
    envelope = ok({
        "message": "知识更新成功",
        "updated_fields": list(fields.keys()),
        "changes": changes,
        "version": updated.get("version"),
    })
    return attach_operation_id(envelope, log_id)


@_define_tool(
    name="delete",
    description="删除指定的知识条目（Phase 4 默认软删除，可通过 restore_knowledge 或 undo_operation 恢复）。",
    annotations={"destructiveHint": True},
    group="kb", side_effect="destructive",
)
@_heartbeat
def delete(item_id: str, dry_run: bool = False) -> dict:
    """删除指定 ID 的知识条目（Phase 4：默认软删除，可恢复）。

    Args:
        item_id: 要删除的知识条目 ID
        dry_run: 设为 True 时只预览将删除的数据不执行
    """
    _guard = _check_write_policy("delete", dry_run=dry_run)
    if _guard:
        return _guard
    container = _get_container()
    # 默认过滤已软删除条目 — 二次删除返 NOT_FOUND
    existing = container.db.get_knowledge(item_id, include_deleted=False)
    if not existing:
        return fail(
            ErrorCode.NOT_FOUND,
            f"知识条目不存在或已删除: {item_id}",
            item_id=item_id,
        )

    import json as _json
    deleted_tags = existing.get("tags", "[]")
    if isinstance(deleted_tags, str):
        try:
            deleted_tags = _json.loads(deleted_tags)
        except Exception:
            deleted_tags = []

    deleted_item = {
        "title": existing.get("title", ""),
        "tags": deleted_tags,
        "content_preview": _content_preview(existing.get("content", "")),
        "source_type": existing.get("source_type", ""),
        "file_type": existing.get("file_type", ""),
    }

    if dry_run:
        block_count = 0
        try:
            rows = container.db.get_conn().execute(
                "SELECT COUNT(*) as cnt FROM blocks WHERE page_id = ?", (item_id,)
            ).fetchone()
            block_count = rows["cnt"]
        except Exception:
            pass
        envelope = dry_run_preview(
            {"item_id": item_id,
             "would_delete": {**deleted_item, "block_count": block_count},
             "warning": "软删除可通过 restore_knowledge 或 undo_operation 恢复"},
            item_id=item_id,
            soft_deleted=True,
        )
        return envelope

    log_id = _op_log("delete", "knowledge", item_id, before=deleted_item, metadata={
        "version": existing.get("version"),
        "soft_delete": True,
    })
    # 1) DB 层软删除（设置 deleted_at）
    container.db.soft_delete_knowledge(item_id)
    # 2) MD 文件移到 .trash（幂等，文件不存在不抛错）
    try:
        container.file_graph_service.delete_page(item_id, move_to_trash=True)
    except Exception as exc:
        logger.warning("file_graph delete_page failed for %s: %s", item_id, exc)
    envelope = ok({
        "message": "知识已软删除（可恢复）",
        "id": item_id,
        "deleted_item": deleted_item,
        "version": existing.get("version"),
        "soft_deleted": True,
        "restore_via": "restore_knowledge 或 undo_operation",
    })
    return attach_operation_id(envelope, log_id)


@_define_tool(
    name="restore_knowledge",
    description="恢复已软删除的知识条目。清除 deleted_at 并将 MD 文件从 .trash 移回 pages/。",
    annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False},
    group="kb", side_effect="write",
)
@_heartbeat
def restore_knowledge(item_id: str) -> dict:
    """恢复软删除的知识条目（Phase 4）。

    Args:
        item_id: 要恢复的知识条目 ID
    """
    _guard = _check_write_policy("restore_knowledge")
    if _guard:
        return _guard
    container = _get_container()
    # 必须看到 deleted_at 非空
    existing = container.db.get_knowledge(item_id, include_deleted=True)
    if not existing:
        return fail(
            ErrorCode.NOT_FOUND,
            f"知识条目不存在: {item_id}",
            item_id=item_id,
        )
    if not existing.get("deleted_at"):
        return fail(
            ErrorCode.PRECONDITION_FAILED,
            f"知识条目未处于删除状态，无需恢复: {item_id}",
            item_id=item_id,
        )

    # 1) DB 层恢复
    container.db.restore_knowledge(item_id)
    # 2) MD 文件恢复（从 .trash 找）
    try:
        trash_dir = container.file_graph_service.ensure_graph() / ".trash"
        restored_file = None
        for path in sorted(trash_dir.glob(f"*{item_id[:8]}*.md"), reverse=True):
            try:
                container.file_graph_service.restore_page(path.name)
                restored_file = path.name
                break
            except Exception:
                continue
    except Exception as exc:
        logger.warning("MD restore from trash failed for %s: %s", item_id, exc)
        restored_file = None

    log_id = _op_log("restore", "knowledge", item_id, after={
        "restored_from": "soft_delete",
        "restored_file": restored_file,
    })
    envelope = ok({
        "message": "知识已恢复",
        "id": item_id,
        "restored_file": restored_file,
    })
    return attach_operation_id(envelope, log_id)


@_define_tool(
    name="reindex_all",
    description="重建所有知识条目的索引（向量索引、全文索引、分块索引）。当搜索结果异常时使用。"
    "[耗时提示：可能数分钟，大量数据时建议客户端超时 ≥ 300s]",
    annotations={'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True, 'openWorldHint': False},
    group="kb", side_effect="destructive",
)
@_heartbeat
def reindex_all(dry_run: bool = False) -> dict:
    """重建全部知识条目的索引。包括分块、向量化和全文索引。

    Args:
        dry_run: 设为 True 时只返回将重建的数量不执行
    """
    _guard = _check_write_policy("reindex_all", dry_run=dry_run)
    if _guard:
        return _guard
    container = _get_container()
    count = container.db.count_knowledge()
    if dry_run:
        from src.services.indexer import reindex_all as _reindex_all
        estimate = _reindex_all(dry_run=True)
        return dry_run_preview(
            {
                "item_count": count,
                "would_reindex": count,
                **estimate,
            },
            item_count=count,
            affected_items=estimate["affected_items"],
            affected_blocks=estimate["affected_blocks"],
            embedding_context_enabled=estimate["embedding_context_enabled"],
            estimated_batches=estimate["estimated_batches"],
        )
    log_id = _op_log("reindex", "system", "all", metadata={"count": count})
    from src.services.indexer import reindex_all as _reindex_all
    result = _reindex_all()
    envelope = ok({
        "message": "索引重建完成",
        "result": result,
    })
    return attach_operation_id(envelope, log_id)


@_define_tool(
    name="list_knowledge",
    description="列出知识库中的知识条目，支持按标签、文件类型筛选，分页和排序。",
    annotations={"readOnlyHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def list_knowledge(
    tag: str | None = None,
    file_type: str | None = None,
    sort_by: str = "updated_at",
    sort_order: str = "DESC",
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """列出知识条目，支持筛选和分页。

    Args:
        tag: 按标签筛选（可选）
        file_type: 按文件类型筛选（可选）
        sort_by: 排序字段 - updated_at、created_at、title
        sort_order: 排序方向 - DESC 或 ASC
        limit: 每页数量，默认20
        offset: 分页偏移量，默认0
    """
    db = _get_container().db
    items = db.list_knowledge(
        tag=tag, file_type=file_type, sort_by=sort_by,
        sort_order=sort_order, limit=limit, offset=offset,
    )
    total = db.count_knowledge(tag=tag)
    has_more = (offset + len(items)) < total
    return ok(
        items,
        total=total,
        limit=limit,
        offset=offset,
        next_offset=offset + limit if has_more else None,
        truncated=has_more,
    )


@_define_tool(
    name="index_path",
    description="索引文件或目录。扫描本地路径，自动检测变更并增量导入知识库。"
    "支持 PDF、DOCX、TXT、Markdown、代码等文档类型。",
    annotations={"readOnlyHint": False, "destructiveHint": False},
    group="kb", side_effect="write",
)
@_heartbeat
def index_path(path: str, recursive: bool = True, dry_run: bool = False, force: bool = False) -> dict:
    """索引文件或目录。

    Args:
        path: 文件或目录的本地路径
        recursive: 是否递归扫描子目录（默认 True）
        dry_run: 设为 True 时只预览变更不执行
        force: 强制重新索引所有文件
    """
    _guard = _check_write_policy("index_path", dry_run=dry_run)
    if _guard:
        return _guard
    from dataclasses import asdict
    from pathlib import Path

    try:
        validated_path = _validate_ingest_path(path)
    except FileNotFoundError as exc:
        return fail(ErrorCode.NOT_FOUND, str(exc), path=path)
    except PermissionError as exc:
        return fail(ErrorCode.PERMISSION_DENIED, str(exc), path=path)

    service = _get_container().path_indexer
    result = service.index_path(
        Path(validated_path),
        recursive=recursive,
        dry_run=dry_run,
        force=force,
    )
    return ok(asdict(result), dry_run=dry_run)


@_define_tool(
    name="tags",
    description="获取知识库中所有已使用的标签列表。",
    annotations={"readOnlyHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def tags() -> dict:
    """返回知识库中所有标签的排序列表。"""
    all_tags = _get_container().db.get_all_tags()
    return ok(all_tags, count=len(all_tags))


@_define_tool(
    name="ingest_file",
    description="解析本地文件并将其内容导入知识库。支持 PDF、DOCX、TXT、Markdown、HTML、图片及代码文件。"
    "Excel 文件的每个工作表独立导入。大文件自动转异步任务（返回 job_id）。"
    "[耗时提示：小文件 3-10 秒，大文件自动转异步]",
    annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False},
    group="kb", side_effect="write",
)
@_heartbeat
def ingest_file(file_path: str, tags: list[str] | None = None, dry_run: bool = False) -> dict:
    """解析本地文件并创建知识条目。

    大文件（文件大小/sheet数/页数/段落数超过配置阈值）自动转异步任务，
    返回 job_id 供 get_job 轮询进度。小文件仍同步返回。

    Args:
        file_path: 本地文件的绝对路径
        tags: 要附加的标签列表
        dry_run: 设为 True 时只预览将解析的文件信息不执行
    """
    _guard = _check_write_policy("ingest_file", dry_run=dry_run)
    if _guard:
        return _guard
    try:
        validated_path = _validate_file_path(file_path)
    except (FileNotFoundError, PermissionError) as exc:
        return fail(
            ErrorCode.INGEST_FAILED if isinstance(exc, FileNotFoundError)
            else ErrorCode.PERMISSION_DENIED,
            str(exc),
            file_path=file_path,
        )

    # 估算文件复杂度
    from src.services.async_tasks import _estimate_file_complexity
    complexity = _estimate_file_complexity(validated_path)

    if dry_run:
        return dry_run_preview({
            "file_path": validated_path,
            "size_bytes": complexity.get("size_bytes", 0),
            "sheet_count": complexity.get("sheet_count", 0),
            "page_count": complexity.get("page_count", 0),
            "paragraph_count": complexity.get("paragraph_count", 0),
            "would_route_async": complexity.get("needs_async", False),
            "would_parse": True,
        }, file_path=validated_path)

    # 大文件自动路由到异步
    if complexity.get("needs_async", False):
        from src.services.async_task import AsyncTaskService
        job_id = AsyncTaskService.create_job(
            "file_ingest",
            {"file_path": validated_path, "tags": tags or []},
        )
        return ok({
            "job_id": job_id,
            "status": "pending",
            "routed_async": True,
            "reason": complexity.get("reason", "大文件自动转异步"),
            "size_bytes": complexity.get("size_bytes", 0),
            "message": f"大文件已创建异步导入任务，请用 get_job 查询进度: {job_id}",
        })

    try:
        result = _do_ingest_file(validated_path, tags)
    except Exception as exc:
        logger.exception("ingest_file failed: %s", file_path)
        return fail(ErrorCode.INGEST_FAILED, str(exc), file_path=file_path)
    operation_id = result.get("operation_id")
    if not operation_id:
        for row in result.get("sheets", []) if isinstance(result.get("sheets"), list) else []:
            operation_id = row.get("operation_id")
            if operation_id:
                break
    return ok(result, operation_id=operation_id)


@_define_tool(
    name="ingest_url",
    description="解析网页 URL 并将其内容导入知识库。支持 HTTP/HTTPS 网页，自动提取正文文本。",
    annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False},
    group="kb", side_effect="write",
)
@_heartbeat
def ingest_url(url: str, tags: list[str] | None = None, dry_run: bool = False) -> dict:
    """抓取网页并创建知识条目。

    Args:
        url: 要导入的网页 URL（HTTP 或 HTTPS）
        tags: 要附加的标签列表
        dry_run: 设为 True 时只预览将导入的 URL，不抓取网络
    """
    _guard = _check_write_policy("ingest_url", dry_run=dry_run)
    if _guard:
        return _guard
    if dry_run:
        return dry_run_preview({
            "url": url,
            "tags": tags or [],
            "would_fetch": True,
            "would_parse": True,
        }, url=url)
    try:
        result = _do_ingest_url(url, tags)
    except Exception as exc:
        logger.exception("ingest_url failed: %s", url)
        return fail(ErrorCode.INGEST_FAILED, str(exc), url=url)
    return ok(result, operation_id=result.get("operation_id"))


def _validate_ingest_path(path: str) -> str:
    """验证文件或目录在允许的目录范围内，防止路径遍历攻击。

    允许的目录包括:
    1. config.yaml 中 security.allowed_ingest_dirs 配置的目录
    2. SHINEHE_HOME 环境变量指向的目录
    3. 项目数据目录
    """
    from src.utils.paths import get_data_dir

    resolved = os.path.realpath(path)

    if not os.path.exists(resolved):
        raise FileNotFoundError(f"路径不存在: {path}")

    # 构建允许的目录白名单
    allowed_dirs: list[str] = []

    # 配置文件中的显式白名单
    configured_dirs = Config.get("security.allowed_ingest_dirs", [])
    if configured_dirs:
        for d in configured_dirs:
            allowed_dirs.append(os.path.realpath(d))

    # SHINEHE_HOME 或项目根目录
    env_root = os.environ.get("SHINEHE_HOME")
    if env_root:
        allowed_dirs.append(os.path.realpath(env_root))

    # 项目数据目录
    allowed_dirs.append(str(get_data_dir()))

    # 项目根目录（源码模式下）
    from src.utils.paths import get_project_root
    allowed_dirs.append(str(get_project_root()))

    # 用户主目录（作为常见导入来源）
    home = os.path.expanduser("~")
    allowed_dirs.append(os.path.realpath(home))

    # 检查文件是否在任一允许的目录下
    resolved_norm = os.path.normcase(resolved)
    for allowed in allowed_dirs:
        allowed_norm = os.path.normcase(os.path.realpath(allowed))
        try:
            if os.path.commonpath((resolved_norm, allowed_norm)) == allowed_norm:
                return resolved
        except ValueError:
            # Windows 不同盘符无法计算 commonpath，视为不在授权目录中。
            continue

    raise PermissionError(
        f"路径不在允许的目录范围内: {path}。"
        f"请在 config.yaml 的 security.allowed_ingest_dirs 中添加允许的目录。"
    )


def _validate_file_path(file_path: str) -> str:
    """验证待导入文件路径，并确保目标是普通文件。"""
    resolved = os.path.realpath(file_path)
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"文件不存在: {file_path}")
    return _validate_ingest_path(resolved)


def _do_ingest_file(file_path: str, tags: list[str] | None = None) -> dict:
    tags = tags or []

    # 安全校验：路径规范化 + 白名单验证
    validated_path = _validate_file_path(file_path)
    parsed_list = parse_file(validated_path)

    import hashlib
    from datetime import datetime, timezone

    # 读取文件创建时间戳（使用 UTC 时区避免 naive datetime）
    file_created_at = ""
    try:
        file_created_at = datetime.fromtimestamp(
            os.path.getctime(validated_path), tz=timezone.utc
        ).isoformat()
    except OSError:
        pass

    # 读取文件修改时间戳（使用 UTC 时区避免 naive datetime）
    file_modified_at = ""
    try:
        file_modified_at = datetime.fromtimestamp(
            os.path.getmtime(validated_path), tz=timezone.utc
        ).isoformat()
    except OSError:
        pass

    try:
        os.path.getsize(validated_path)
    except OSError:
        pass

    results = []
    container = _get_container()
    db = container.db
    for parsed in parsed_list:
        content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()
        existing = db.get_knowledge_by_hash(content_hash)
        if existing:
            results.append({
                "id": existing["id"],
                "title": existing["title"],
                "file_type": existing.get("file_type", ""),
                "message": "内容已存在，跳过导入",
                "skipped": True,
            })
            continue

        blocks = parsed.structured if parsed.structured else parsed.content
        item_id = container.file_graph_service.create_page(
            parsed.title,
            blocks,
            tags=tags,
            metadata={
                "source_type": "file",
                "source_path": parsed.source_path,
                "file_type": parsed.file_type,
                "file_created_at": file_created_at,
                "file_modified_at": file_modified_at,
            },
        )
        item = db.get_knowledge(item_id) or {"title": parsed.title, "file_type": parsed.file_type}
        _try_wiki_compile(item_id)
        log_id = _op_log("ingest", "knowledge", item_id, after={
            "title": item["title"], "file_type": item.get("file_type", parsed.file_type),
        }, metadata={"source": "file", "path": validated_path})
        results.append({
            "id": item_id,
            "title": item["title"],
            "file_type": item.get("file_type", parsed.file_type),
            "path": item.get("source_path", ""),
            "operation_id": log_id,
            "message": "文件解析并索引成功",
        })

    if len(results) == 1:
        return results[0]
    return {
        "message": f"共导入 {len(results)} 个工作表",
        "sheets": results,
        "operation_ids": [r["operation_id"] for r in results if r.get("operation_id")],
    }


def _do_ingest_url(url: str, tags: list[str] | None = None) -> dict:
    tags = tags or []
    parsed = parse_url(url)
    container = _get_container()
    db = container.db

    import hashlib
    content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()
    existing = db.get_knowledge_by_hash(content_hash)
    if existing:
        return {
            "id": existing["id"],
            "title": existing["title"],
            "file_type": existing.get("file_type", ""),
            "message": "网页内容已存在，跳过导入",
            "skipped": True,
        }

    blocks = parsed.structured if parsed.structured else parsed.content
    item_id = container.file_graph_service.create_page(
        parsed.title,
        blocks,
        tags=tags,
        metadata={"source_type": "web", "source_path": parsed.source_path, "file_type": parsed.file_type},
    )
    item = db.get_knowledge(item_id) or {"title": parsed.title, "file_type": parsed.file_type}
    _try_wiki_compile(item_id)
    log_id = _op_log("ingest", "knowledge", item_id, after={
        "title": item["title"], "file_type": item.get("file_type", parsed.file_type),
    }, metadata={"source": "url", "url": url})
    return {
        "id": item_id,
        "title": item["title"],
        "file_type": item.get("file_type", parsed.file_type),
        "path": item.get("source_path", ""),
        "operation_id": log_id,
        "message": "网页解析并索引成功",
    }


# ---- Phase 5 / Sprint 4: 大文件异步任务 ----

@_define_tool(
    name="create_ingest_job",
    description="创建异步文件/URL导入任务。大文件自动走此路径（也可手动调用强制异步）。"
    "返回 job_id 供 get_job 轮询。",
    annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False},
    group="kb", side_effect="write",
)
@_heartbeat
def create_ingest_job(
    file_path: str | None = None,
    url: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """创建异步导入任务。

    至少提供 file_path 或 url 其中之一。file_path 和 url 同时提供时优先 file_path。

    Args:
        file_path: 本地文件路径（与 url 二选一）
        url: 网页 URL（与 file_path 二选一）
        tags: 附加标签列表
    """
    _guard = _check_write_policy("create_ingest_job")
    if _guard:
        return _guard
    from src.services.async_task import AsyncTaskService

    tags = tags or []

    if file_path:
        # 验证路径
        try:
            validated_path = _validate_file_path(file_path)
        except (FileNotFoundError, PermissionError) as exc:
            return fail(
                ErrorCode.INGEST_FAILED if isinstance(exc, FileNotFoundError)
                else ErrorCode.PERMISSION_DENIED,
                str(exc),
                file_path=file_path,
            )
        job_id = AsyncTaskService.create_job(
            "file_ingest",
            {"file_path": validated_path, "tags": tags},
        )
        return ok({
            "job_id": job_id,
            "status": "pending",
            "job_type": "file_ingest",
            "file_path": validated_path,
            "message": f"文件导入任务已创建，请用 get_job 查询进度: {job_id}",
        })

    if url:
        job_id = AsyncTaskService.create_job(
            "url_ingest",
            {"url": url, "tags": tags},
        )
        return ok({
            "job_id": job_id,
            "status": "pending",
            "job_type": "url_ingest",
            "url": url,
            "message": f"URL 导入任务已创建，请用 get_job 查询进度: {job_id}",
        })

    return fail(
        ErrorCode.VALIDATION_ERROR,
        "必须提供 file_path 或 url 其中之一",
        hint="file_path 和 url 至少提供一个",
    )


@_define_tool(
    name="get_job",
    description="查询异步任务状态和进度。返回 job 详情含 progress / progress_message / result。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def get_job(job_id: str) -> dict:
    """查询异步任务状态（get_async_job 的 Agent 友好别名）。

    Args:
        job_id: 任务 ID
    """
    from src.services.async_task import AsyncTaskService
    job = AsyncTaskService.get_job(job_id)
    if not job:
        return fail(ErrorCode.JOB_NOT_FOUND, f"任务不存在: {job_id}", job_id=job_id)
    # 返回核心字段，排除内部实现细节
    return ok({
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "progress_message": job.progress_message,
        "result": job.result,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    })


@_define_tool(
    name="list_jobs",
    description="列出异步任务，可按状态/类型筛选。",
    annotations={"readOnlyHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def list_jobs(
    status: str | None = None,
    job_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """列出异步任务（list_async_jobs 的 Agent 友好别名）。

    Args:
        status: 状态筛选 (pending/running/completed/failed/cancelled)
        job_type: 类型筛选 (file_ingest/url_ingest/reindex_all/wiki_compile/...)
        limit: 返回数量上限
        offset: 分页偏移
    """
    from src.services.async_task import AsyncTaskService
    jobs = AsyncTaskService.list_jobs(status, job_type, limit, offset)
    items = [{
        "id": j.id,
        "job_type": j.job_type,
        "status": j.status,
        "progress": j.progress,
        "progress_message": j.progress_message,
        "created_at": j.created_at,
    } for j in jobs]
    return ok(items, count=len(items), limit=limit, offset=offset)


@_define_tool(
    name="cancel_job",
    description="取消异步任务。仅 pending/running 状态的任务可取消。",
    annotations={"destructiveHint": True},
    group="kb", side_effect="destructive",
)
@_heartbeat
def cancel_job(job_id: str) -> dict:
    """取消异步任务（cancel_async_job 的 Agent 友好别名）。

    Args:
        job_id: 任务 ID
    """
    _guard = _check_write_policy("cancel_job")
    if _guard:
        return _guard
    from src.services.async_task import AsyncTaskService
    from src.services.async_worker import TaskRegistry
    # 1) 在 TaskRegistry 中标记取消（让运行中的 handler 能检查到）
    TaskRegistry.cancel_job(job_id)
    # 2) 更新 DB 状态
    success = AsyncTaskService.cancel_job(job_id)
    if success:
        return ok({"success": True, "message": "任务已取消", "job_id": job_id})
    return fail(
        ErrorCode.PRECONDITION_FAILED,
        "无法取消（可能已完成或不存在）",
        job_id=job_id,
    )


@_define_tool(
    name="save_to_wiki",
    description="将好的问答回答保存为 Wiki 页面，实现知识沉淀和复利增长。",
    annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False},
    group="wiki", side_effect="write",
    experimental=True,
)
@_heartbeat
def save_to_wiki(question: str, answer: str, source_ids: list[str] | None = None) -> dict:
    """将问答保存为 Wiki 页面。

    Args:
        question: 用户的问题
        answer: AI 的回答
        source_ids: 引用的知识条目 ID 列表
    """
    _guard = _check_write_policy("save_to_wiki")
    if _guard:
        return _guard
    if not Config.get("wiki.enabled", False):
        return fail(ErrorCode.WIKI_DISABLED, "Wiki 功能未启用")
    container = _get_container()
    compiler = container.wiki_compiler
    page_id = compiler.save_answer(question, answer, source_ids)
    if page_id:
        log_id = _op_log("wiki_create", "wiki_page", page_id, after={
            "question": question[:100], "source_ids": source_ids,
        })
        envelope = ok({"page_id": page_id, "message": "回答已保存为 Wiki 页面"})
        return attach_operation_id(envelope, log_id)
    return ok({"message": "回答内容过短，未达到保存阈值", "no_op": True})


@_define_tool(
    name="wiki_lint",
    description="对知识库 Wiki 执行健康检查，找出孤立页面、过时信息和损坏链接。",
    annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="wiki", side_effect="read",
    experimental=True,
)
@_heartbeat
def wiki_lint() -> dict:
    """运行 Wiki 体检，返回健康报告。"""
    if not Config.get("wiki.enabled", False):
        return fail(ErrorCode.WIKI_DISABLED, "Wiki 功能未启用")
    from src.services.wiki_lint import WikiLint
    linter = WikiLint()
    report = linter.run()
    return ok(report)


@_define_tool(
    name="fix_dead_references",
    description="使用 LLM 智能修复 Wiki 页面中 [[...]] 死链。"
    "对每个死链分析上下文后选择修复策略：重定向到已有页面、创建占位页面或移除引用。"
    "修复前会先尝试解析有效引用写入 wiki_links 表。"
    "注意：会消耗 LLM 调用次数（每个含死链的页面约 1 次 LLM 调用）。",
    annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="wiki", side_effect="write",
    experimental=True,
)
@_heartbeat
def fix_dead_references(max_pages: int = 50, dry_run: bool = False) -> dict:
    """LLM 驱动的 Wiki 死链智能修复。

    Args:
        max_pages: 最多处理多少个含死链的页面（默认 50，控制 LLM 成本）
        dry_run: 仅扫描报告死链，不执行修复（默认 false）
    """
    _guard = _check_write_policy("fix_dead_references")
    if _guard:
        return _guard
    if not Config.get("wiki.enabled", False):
        return fail(ErrorCode.WIKI_DISABLED, "Wiki 功能未启用")

    # 第一步：先解析有效引用
    from src.services.wiki_compiler import WikiCompiler, resolve_all_content_links
    link_result = resolve_all_content_links()

    if dry_run:
        # 仅报告，不修复
        return ok({
            "mode": "dry_run",
            "links_created": link_result["links_created"],
            "dead_reference_count": link_result["dead_reference_count"],
            "dead_references": link_result["dead_references"],
        })

    # 第二步：LLM 修复
    compiler = WikiCompiler()
    repair_result = compiler.repair_dead_references(max_pages=max_pages)

    log_id = _op_log("fix_dead_references", "wiki", "",
                     metadata={
                         "link_scan": link_result,
                         "repair": repair_result,
                     })
    envelope = ok({
        "link_scan": {
            "scanned": link_result["scanned"],
            "links_created": link_result["links_created"],
        },
        "repair": repair_result,
    })
    return attach_operation_id(envelope, log_id)


# ---- Wiki Workflow MCP Tools ----

@_define_tool(
    name="wiki_submit_review",
    description="提交 Wiki 页面进行审核（draft -> review）", annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="wiki", side_effect="write",
    experimental=True,
)
@_heartbeat
def wiki_submit_review(page_id: str, operator: str = "system", comment: str = "") -> dict:
    """提交页面审核"""
    _guard = _check_write_policy("wiki_submit_review")
    if _guard:
        return _guard
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.submit_for_review(page_id, operator, comment)
    if result.success:
        log_id = _op_log("workflow_transition", "wiki_page", page_id, operator=operator,
                         before={"status": "draft"}, after={"status": "review"},
                         metadata={"comment": comment})
        envelope = ok({"success": result.success, "message": result.message,
                       "page_id": page_id, "from": "draft", "to": "review"})
        return attach_operation_id(envelope, log_id)
    return ok({"success": False, "message": result.message, "page_id": page_id})


@_define_tool(
    name="wiki_approve",
    description="审批通过 Wiki 页面（review -> published）", annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="wiki", side_effect="write",
    experimental=True,
)
@_heartbeat
def wiki_approve(page_id: str, operator: str = "system", comment: str = "") -> dict:
    """审批通过"""
    _guard = _check_write_policy("wiki_approve")
    if _guard:
        return _guard
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.approve(page_id, operator, comment)
    if result.success:
        log_id = _op_log("workflow_transition", "wiki_page", page_id, operator=operator,
                         before={"status": "review"}, after={"status": "published"},
                         metadata={"comment": comment})
        envelope = ok({"success": result.success, "message": result.message,
                       "page_id": page_id, "from": "review", "to": "published"})
        return attach_operation_id(envelope, log_id)
    return ok({"success": False, "message": result.message, "page_id": page_id})


@_define_tool(
    name="wiki_reject",
    description="驳回 Wiki 页面（review -> draft）", annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="wiki", side_effect="write",
    experimental=True,
)
@_heartbeat
def wiki_reject(page_id: str, operator: str = "system", comment: str = "") -> dict:
    """驳回页面"""
    _guard = _check_write_policy("wiki_reject")
    if _guard:
        return _guard
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.reject(page_id, operator, comment)
    if result.success:
        log_id = _op_log("workflow_transition", "wiki_page", page_id, operator=operator,
                         before={"status": "review"}, after={"status": "draft"},
                         metadata={"comment": comment})
        envelope = ok({"success": result.success, "message": result.message,
                       "page_id": page_id, "from": "review", "to": "draft"})
        return attach_operation_id(envelope, log_id)
    return ok({"success": False, "message": result.message, "page_id": page_id})


@_define_tool(
    name="wiki_deprecate",
    description="弃用 Wiki 页面（published -> deprecated）", annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="wiki", side_effect="write",
    experimental=True,
)
@_heartbeat
def wiki_deprecate(page_id: str, operator: str = "system", comment: str = "") -> dict:
    """弃用页面"""
    _guard = _check_write_policy("wiki_deprecate")
    if _guard:
        return _guard
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.deprecate(page_id, operator, comment)
    if result.success:
        log_id = _op_log("workflow_transition", "wiki_page", page_id, operator=operator,
                         before={"status": "published"}, after={"status": "deprecated"},
                         metadata={"comment": comment})
        envelope = ok({"success": result.success, "message": result.message,
                       "page_id": page_id, "from": "published", "to": "deprecated"})
        return attach_operation_id(envelope, log_id)
    return ok({"success": False, "message": result.message, "page_id": page_id})


@_define_tool(
    name="wiki_workflow_history",
    description="获取 Wiki 页面工作流历史", annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="wiki", side_effect="read",
    experimental=True,
)
@_heartbeat
def wiki_workflow_history(page_id: str) -> dict:
    """获取工作流历史"""
    from src.services.wiki_workflow import WikiWorkflow
    history = WikiWorkflow.get_history(page_id)
    return ok({"history": history}, page_id=page_id, count=len(history))


@_define_tool(
    name="wiki_list_versions",
    description="获取 Wiki 页面版本列表", annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="wiki", side_effect="read",
    experimental=True,
)
@_heartbeat
def wiki_list_versions(page_id: str) -> dict:
    """列出页面所有版本"""
    versions = _get_container().db.list_wiki_versions(page_id)
    return ok({"versions": versions}, page_id=page_id, count=len(versions))


@_define_tool(
    name="wiki_restore_version",
    description="恢复到指定版本的 Wiki 页面", annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False},
    group="wiki", side_effect="write",
    experimental=True,
)
@_heartbeat
def wiki_restore_version(page_id: str, version: int) -> dict:
    """恢复到指定版本"""
    _guard = _check_write_policy("wiki_restore_version")
    if _guard:
        return _guard
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.restore_version(page_id, version)
    if result.success:
        log_id = _op_log("wiki_update", "wiki_page", page_id,
                         after={"restored_version": version})
        envelope = ok({"success": result.success, "message": result.message,
                       "page_id": page_id, "restored_version": version})
        return attach_operation_id(envelope, log_id)
    return ok({"success": False, "message": result.message, "page_id": page_id})


# ---- Async Jobs MCP Tools ----

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


@_define_tool(
    name="structured_query",
    description="执行结构化查询 DSL，返回知识条目列表", annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="kb", side_effect="read",
)
@_heartbeat
def structured_query(query_dsl: str, limit: int = 100, offset: int = 0) -> dict:
    """Execute a structured JSON DSL query against the knowledge base.

    The DSL supports tag, property, fulltext, link, file_type, source_type filters
    combined with and/or/not groups.

    Args:
        query_dsl: JSON string with the query DSL（也接受 dict）
        limit: Maximum results to return
        offset: 分页偏移量
    """
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    container = _get_container()
    try:
        dsl = json.loads(query_dsl) if isinstance(query_dsl, str) else query_dsl
        spec = QuerySpec.from_json(dsl)
        spec.limit = min(spec.limit, limit)
        spec.offset = offset
        executor = QueryExecutor(db=container.db)
        results = executor.execute(spec)
        results_list = list(results) if not isinstance(results, list) else results
        has_more = len(results_list) == limit
        return ok(
            results_list,
            limit=limit,
            offset=offset,
            next_offset=offset + len(results_list) if has_more else None,
            truncated=has_more,
        )
    except Exception as exc:
        logger.exception("structured_query failed: %s", exc)
        return fail(ErrorCode.QUERY_PARSE_ERROR, str(exc))


@_define_tool(
    name="explain_query",
    description="解释结构化查询的执行计划与匹配条件", annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="kb", side_effect="read",
)
@_heartbeat
def explain_query(query_dsl: str) -> dict:
    """Explain a structured query: show human-readable summary, execution plan, and condition tree.

    Args:
        query_dsl: JSON string with the query DSL（也接受 dict）
    """
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    try:
        dsl = json.loads(query_dsl) if isinstance(query_dsl, str) else query_dsl
        spec = QuerySpec.from_json(dsl)
        explainer = QueryExplainer()
        return ok(explainer.explain(spec))
    except Exception as exc:
        logger.exception("explain_query failed: %s", exc)
        return fail(ErrorCode.QUERY_PARSE_ERROR, str(exc))


@_define_tool(
    name="graph_traverse",
    description="从给定节点遍历知识图谱（多跳、限深度、限节点数）", annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="graph", side_effect="read",
    experimental=True,
)
@_heartbeat
def graph_traverse(
    start_ids: str,
    max_depth: int = 2,
    start_type: str = "knowledge",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Traverse the knowledge graph starting from given page/block IDs.

    Args:
        start_ids: JSON array of starting node IDs (e.g. '["page-id-1", "page-id-2"]')
        max_depth: Maximum traversal depth
        start_type: Type of start nodes (knowledge or block)
        limit: 节点数上限
        offset: 分页偏移
    """
    from src.services.graph_traversal import GraphTraversalService

    container = _get_container()
    try:
        ids = json.loads(start_ids) if isinstance(start_ids, str) else start_ids
        service = GraphTraversalService(db=container.db)
        result = service.traverse(start_ids=ids, start_type=start_type, max_depth=max_depth)
        # 截断节点数
        nodes = result.get("nodes", [])
        edges = result.get("edges", [])
        truncated = len(nodes) > limit
        if truncated:
            nodes = nodes[offset:offset + limit]
        return ok(
            {
                "nodes": nodes,
                "edges": edges,
                "paths": result.get("paths", []),
                "truncated": truncated or result.get("truncated", False),
            },
            limit=limit,
            offset=offset,
            max_depth=max_depth,
        )
    except Exception as exc:
        logger.exception("graph_traverse failed: %s", exc)
        return fail(ErrorCode.QUERY_PARSE_ERROR, str(exc))


# ---- Sprint 2: Agentic Query 入口 ----

@_define_tool(
    name="route_query",
    description="仅路由分析，不执行检索。返回 mode (structured|graph|hybrid) + query_spec + "
    "traverse + explanation，Agent 据此决定下一步走 execute_query 还是 ask_with_query。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def route_query(question: str) -> dict:
    """路由分析：识别问题是结构化 / 图谱 / 模糊语义。

    Args:
        question: 用户原始问题

    Returns:
        envelope.data 字段：
            - mode: structured | graph | hybrid
            - query_spec: QuerySpec JSON dict（structured 模式）
            - traverse: 遍历配置（graph 模式，max_depth 等）
            - explanation: 路由选择的理由
    """
    from src.services.agentic_router import AgenticRouter, serialize_route
    container = _get_container()
    try:
        router = AgenticRouter(db=container.db, llm=container.llm)
        routing = router.route(question)
        payload = serialize_route(routing)
        return ok(payload, mode=payload.get("mode"))
    except Exception as exc:
        logger.exception("route_query failed: %s", exc)
        return fail(ErrorCode.INTERNAL_ERROR, str(exc), question=question)


@_define_tool(
    name="execute_query",
    description="执行显式 QuerySpec DSL。支持 type=structured（条件过滤）/ graph（图遍历）/ "
    "hybrid（混合搜索）。分页透传 limit/offset/next_offset/truncated。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def execute_query(
    query_spec: dict,
    type: str = "structured",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """执行 QuerySpec DSL。

    Args:
        query_spec: QuerySpec JSON dict（含 filter / sort / limit / offset / include_blocks）
        type: structured | graph | hybrid — 决定走哪个执行器
        limit: 返回结果数量上限
        offset: 分页偏移量

    Returns:
        envelope.data 列表 + meta.{total_estimate, limit, offset, next_offset, truncated, type}
    """
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    container = _get_container()
    try:
        spec = QuerySpec.from_json(query_spec) if isinstance(query_spec, dict) else query_spec
        spec.limit = min(spec.limit, limit)
        spec.offset = offset

        if type == "graph":
            from src.services.graph_traversal import GraphTraversalService
            start_ids = (query_spec or {}).get("start_ids", [])
            start_type = (query_spec or {}).get("start_type", "knowledge")
            max_depth = (query_spec or {}).get("max_depth", 2)
            if not start_ids:
                return fail(
                    ErrorCode.VALIDATION_ERROR,
                    "graph 模式需要在 query_spec.start_ids 提供起点节点",
                )
            traversal = GraphTraversalService(db=container.db).traverse(
                start_ids=start_ids, start_type=start_type, max_depth=max_depth,
            )
            nodes = traversal.get("nodes", [])
            has_more = len(nodes) == limit
            sliced = nodes[offset:offset + limit] if has_more else nodes
            return ok(
                {
                    "nodes": sliced,
                    "edges": traversal.get("edges", []),
                    "paths": traversal.get("paths", []),
                    "truncated": traversal.get("truncated", False) or has_more,
                },
                type=type,
                limit=limit,
                offset=offset,
                next_offset=offset + len(sliced) if has_more else None,
                total_estimate=len(nodes),
            )

        if type == "structured":
            executor = QueryExecutor(db=container.db)
            results = executor.execute(spec)
            results_list = list(results) if not isinstance(results, list) else results
            has_more = len(results_list) == limit
            return ok(
                results_list,
                type=type,
                limit=limit,
                offset=offset,
                next_offset=offset + len(results_list) if has_more else None,
                total_estimate=len(results_list),
                truncated=has_more,
            )

        return fail(
            ErrorCode.VALIDATION_ERROR,
            f"不支持的 type: {type}，仅支持 structured / graph / hybrid",
            type=type,
        )
    except Exception as exc:
        logger.exception("execute_query failed: %s", exc)
        return fail(ErrorCode.QUERY_PARSE_ERROR, str(exc), type=type)


@_define_tool(
    name="ask_with_query",
    description="用显式 QuerySpec 控制 RAG 检索阶段，再调用 LLM 生成回答。"
    "返回结构化 payload（含 answer / sources / route / query_plan / block_contexts / warnings）。"
    "[耗时提示：通常 5-30 秒，建议客户端超时 ≥ 60s]",
    annotations={"readOnlyHint": True, "idempotentHint": False},
    group="kb", side_effect="read",
)
@_heartbeat
def ask_with_query(
    question: str,
    query_spec: dict,
    top_k: int = 10,
) -> dict:
    """用显式 QuerySpec 控制 RAG 检索，再生成回答。

    Args:
        question: 用户问题
        query_spec: QuerySpec JSON dict，控制 RAG 检索阶段
        top_k: 检索阶段召回的候选数

    Returns:
        与 ``ask`` 工具相同的 7 字段结构化 payload（data 内）
    """
    from src.models.query_dsl import QuerySpec
    from src.services.rag_pipeline import DEFAULT_PIPELINE_CONFIG, RagPipeline

    container = _get_container()
    try:
        spec = QuerySpec.from_json(query_spec) if isinstance(query_spec, dict) else query_spec
        pipeline = RagPipeline(
            pipeline_config=DEFAULT_PIPELINE_CONFIG,
            llm=container.llm,
            deps={
                "db": container.db,
                "llm": container.llm,
                "query_rewriter": container.query_rewriter,
                "reranker": container.reranker,
                "hybrid_search": container.hybrid_search,
            },
        )
        # 把 spec 注入 metadata，VectorSearchStage 会跳过自动路由直接使用
        # 使用 _run_async 安全执行，避免在已有事件循环中调用 asyncio.run()
        result = _run_async(
            pipeline.execute(
                question,
                query_spec_override=spec,
                top_k=top_k,
            ),
            timeout=120,
        )
        return ok(
            result,
            source_count=len(result.get("sources", [])),
            warning_count=len(result.get("warnings", [])),
            route_mode=result.get("route", {}).get("mode", "unknown"),
            graph_truncated=result.get("source_graph", {}).get("truncated", False),
        )
    except Exception as exc:
        logger.exception("ask_with_query failed: %s", exc)
        return fail(ErrorCode.INTERNAL_ERROR, str(exc), question=question)


@_define_tool(
    name="get_source_graph",
    description="根据 sources、block_ids 或 knowledge_ids 构建 bounded source graph，供 Agent 追溯 RAG 证据链。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def get_source_graph(
    sources: list[dict] | str | None = None,
    block_ids: list[str] | str | None = None,
    knowledge_ids: list[str] | str | None = None,
    max_nodes: int = 50,
) -> dict:
    """Build a local source graph from RAG sources or explicit IDs."""
    def parse_list(value):
        if value is None:
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else [value]
            except json.JSONDecodeError:
                return [value]
        return value if isinstance(value, list) else [value]

    source_rows = parse_list(sources)
    if any(not isinstance(row, dict) for row in source_rows):
        return fail(ErrorCode.VALIDATION_ERROR, "sources must be a list of objects")

    for block_id in parse_list(block_ids):
        source_rows.append({"block_id": block_id})
    for knowledge_id in parse_list(knowledge_ids):
        source_rows.append({"knowledge_id": knowledge_id})

    if not source_rows:
        return fail(
            ErrorCode.VALIDATION_ERROR,
            "get_source_graph requires sources, block_ids, or knowledge_ids",
        )

    from src.services.source_graph import build_source_graph
    graph = build_source_graph(
        source_rows,
        db=_get_container().db,
        max_nodes=max(1, int(max_nodes or 50)),
    )
    return ok(
        graph,
        node_count=graph.get("node_count", 0),
        edge_count=len(graph.get("edges", [])),
        truncated=graph.get("truncated", False),
        max_nodes=max_nodes,
    )


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

@_define_tool(
    name="kb_capabilities",
    description="查询知识库 MCP 能力清单、payload 限制、推荐调用流程。Agent 第一个应调用的工具。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def kb_capabilities() -> dict:
    """返回当前 MCP 服务的能力、限制和推荐调用流程。"""
    # 工具签名从 FastMCP 实例动态生成（避免与实际注册的工具不一致）
    tool_summaries: list[dict] = []
    try:
        # FastMCP >= 0.4 通过 mcp._tool_manager._tools 暴露注册表
        registry = getattr(mcp, "_tool_manager", None) or getattr(mcp, "tool_manager", None)
        if registry is not None:
            tools = getattr(registry, "_tools", None) or getattr(registry, "tools", {}) or {}
            for name, tool in tools.items():
                tool_summaries.append({
                    "name": name,
                    "description": getattr(tool, "description", ""),
                })
    except Exception as exc:
        logger.debug("tool registry introspection failed: %s", exc)

    return ok({
        "name": "ShineHeKnowledge",
        "version": VERSION,
        "envelope": {
            "ok": "bool — 成功/失败判断字段",
            "data": "any — 成功时的负载",
            "error": "{code, message, details} — 失败时的稳定错误码",
            "meta": "分页/截断/计数等元信息",
            "operation_id": "写操作的 audit log id（与 get_operation_log 配合）",
            "dry_run": "预览变更不执行",
        },
        "error_codes": {
            "NOT_FOUND": "资源不存在",
            "VALIDATION_ERROR": "参数校验失败",
            "PERMISSION_DENIED": "路径越权",
            "INGEST_FAILED": "文件/URL 导入失败",
            "INTERNAL_ERROR": "内部异常",
            "WIKI_DISABLED": "Wiki 未启用",
            "JOB_NOT_FOUND": "任务不存在",
            "QUERY_PARSE_ERROR": "DSL/JSON 解析失败",
        },
        "limits": {
            "default_page_size": int(Config.get("mcp.default_page_size", 20)),
            "max_payload_bytes": int(Config.get("mcp.max_payload_bytes", 1_000_000)),
            "max_graph_nodes": int(Config.get("rag.max_graph_nodes", 200)),
            "max_graph_depth": int(Config.get("rag.max_graph_depth", 3)),
        },
        "tool_metadata": _TOOL_METADATA,
        "tool_aliases": _REGISTERED_TOOL_ALIASES,
        "tools": tool_summaries,
        "recommended_flows": {
            "research": ["kb_capabilities", "route_query", "execute_query|ask", "get_source_graph", "read"],
            "safe_update": ["read", "preview_operation", "update(dry_run=true)", "update", "get_operation_log"],
            "import": ["kb_capabilities", "create_ingest_job|ingest_file", "get_job", "structured_query", "ask"],
            "import_large": ["kb_capabilities", "create_ingest_job", "get_job", "structured_query", "ask"],
            "qna": ["route_query", "ask(include_graph=true, include_context=true)", "get_source_graph", "read"],
            "agent_memory": ["remember_fact", "recall_facts", "update_project_context", "search_decisions", "summarize_recent_changes"],
        },
        "tool_profile": _CURRENT_PROFILE,
        "write_policy": Config.get("mcp.write_policy", ""),
        "experimental_tools_enabled": _EXPERIMENTAL_ENABLED,
        "visible_tools": sorted({t["name"] for t in tool_summaries if "." not in t["name"]}),
        "hidden_groups": sorted(_compute_hidden_groups(tool_summaries)),
        "legacy_aliases_enabled": _ENABLE_ALIASES,
    })


# ---- Operation Log Query ----

@_define_tool(
    name="query_operation_logs",
    description="查询操作审计日志。可按目标类型、目标 ID、操作类型筛选。",
    annotations={"readOnlyHint": True},
    group="ops", side_effect="read",
)
@_heartbeat
def query_operation_logs(
    target_type: str | None = None,
    target_id: str | None = None,
    operation: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """查询操作日志。

    Args:
        target_type: 目标类型筛选 (knowledge/wiki_page/block/tag_relation)
        target_id: 目标 ID 筛选
        operation: 操作类型筛选 (create/update/delete/ingest/reindex/wiki_create/workflow_transition)
        source: 来源筛选 (mcp/api/gui)
        limit: 返回数量上限
        offset: 分页偏移量
    """
    logs = _get_container().operation_log_repo.query(
        target_type=target_type, target_id=target_id,
        operation=operation, source=source,
        limit=limit, offset=offset,
    )
    total = _get_container().operation_log_repo.count(
        target_type=target_type, operation=operation,
    )
    has_more = (offset + len(logs)) < total
    return ok(
        logs,
        count=len(logs),
        total_estimate=total,
        limit=limit,
        offset=offset,
        next_offset=offset + limit if has_more else None,
        truncated=has_more,
    )


# ---- Phase 4 / Sprint 3: 写操作安全闭环 ----

@_define_tool(
    name="preview_operation",
    description="预览写操作而不实际执行。支持 update / create / delete / ingest_file / "
    "reindex_all — 调用对应写工具的 dry_run 路径。Agent 在执行前必先调用。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def preview_operation(
    operation: str,
    item_id: str | None = None,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    file_path: str | None = None,
    file_type: str = "txt",
    source_type: str = "manual",
) -> dict:
    """预览写操作。

    Args:
        operation: 操作类型 — update / create / delete / ingest_file / reindex_all
        item_id: 目标 ID（update/delete 时必填）
        title / content / tags: update / create 时使用
        file_path: ingest_file 时使用
        file_type / source_type: create / ingest_file 时使用
    """
    op = (operation or "").lower()
    if op in ("update",):
        if not item_id:
            return fail(
                ErrorCode.VALIDATION_ERROR,
                "preview_operation: update 需要 item_id",
                operation=op,
            )
        return update(item_id=item_id, title=title, content=content, tags=tags, dry_run=True)
    if op in ("create",):
        if title is None or content is None:
            return fail(
                ErrorCode.VALIDATION_ERROR,
                "preview_operation: create 需要 title 和 content",
                operation=op,
            )
        return create(
            title=title, content=content, tags=tags,
            file_type=file_type, source_type=source_type, dry_run=True,
        )
    if op in ("delete",):
        if not item_id:
            return fail(
                ErrorCode.VALIDATION_ERROR,
                "preview_operation: delete 需要 item_id",
                operation=op,
            )
        return delete(item_id=item_id, dry_run=True)
    if op in ("ingest_file", "ingest"):
        if not file_path:
            return fail(
                ErrorCode.VALIDATION_ERROR,
                "preview_operation: ingest_file 需要 file_path",
                operation=op,
            )
        return ingest_file(file_path=file_path, tags=tags, dry_run=True)
    if op in ("reindex_all", "reindex"):
        return reindex_all(dry_run=True)
    return fail(
        ErrorCode.VALIDATION_ERROR,
        f"preview_operation: 不支持的操作 {operation}",
        operation=operation,
        supported=["update", "create", "delete", "ingest_file", "reindex_all"],
    )


@_define_tool(
    name="get_operation_log",
    description="按 ID 查询单条操作日志的完整记录。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="ops", side_effect="read",
)
@_heartbeat
def get_operation_log(operation_id: str) -> dict:
    """按 ID 查询操作日志。

    Args:
        operation_id: operation_log.id（envelope.operation_id 字段）
    """
    if not operation_id:
        return fail(ErrorCode.VALIDATION_ERROR, "operation_id 必填")
    entry = _get_container().operation_log_repo.get_by_id(operation_id)
    if not entry:
        return fail(
            ErrorCode.NOT_FOUND,
            f"operation_log 不存在: {operation_id}",
            operation_id=operation_id,
        )
    can_undo = _get_container().operation_log.can_undo(operation_id)
    return ok({**entry, "can_undo": can_undo})


@_define_tool(
    name="undo_operation",
    description="撤销某条操作。支持 update（恢复字段）/ create（软删新条目）/ "
    "delete（恢复软删条目）/ ingest（恢复）。返回 undone_log_id。",
    annotations={"idempotentHint": False, "destructiveHint": False},
    group="ops", side_effect="write",
)
@_heartbeat
def undo_operation(operation_id: str, operator: str = "system") -> dict:
    """撤销 operation。

    Args:
        operation_id: 要撤销的 operation_log.id
        operator: 撤销操作的操作者标识
    """
    _guard = _check_write_policy("undo_operation")
    if _guard:
        return _guard
    if not operation_id:
        return fail(ErrorCode.VALIDATION_ERROR, "operation_id 必填")
    container = _get_container()
    result = container.operation_log.undo(operation_id, operator=operator)
    if result.get("ok"):
        return ok(result.get("data") or {}, undone_log_id=result.get("data", {}).get("undone_log_id"))
    err = result.get("error") or {"code": "INTERNAL_ERROR", "message": "unknown"}
    return fail(err.get("code", "INTERNAL_ERROR"),
                err.get("message", "undo failed"),
                operation_id=operation_id,
                details=err.get("details"))


@_define_tool(
    name="list_recent_operations",
    description="列出最近的操作日志（query_operation_logs 的便捷别名）。"
    "按 created_at DESC 排序，缺省 limit=20。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="ops", side_effect="read",
)
@_heartbeat
def list_recent_operations(
    limit: int = 20,
    source: str | None = None,
    target_type: str | None = None,
    operation: str | None = None,
    offset: int = 0,
) -> dict:
    """列出最近的操作日志。

    Args:
        limit: 返回数量上限（默认 20）
        source: 来源筛选 (mcp/api/gui)
        target_type: 目标类型 (knowledge/wiki_page/...)
        operation: 操作类型 (create/update/delete/ingest/reindex/...)
        offset: 分页偏移
    """
    return query_operation_logs(
        target_type=target_type, target_id=None,
        operation=operation, source=source,
        limit=limit, offset=offset,
    )


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
    # --- graph.* ---
    "graph_traverse":       {"group": "graph", "side_effect": "read", "requires_confirmation": False, "short_desc": "图遍历"},
    # --- ops.* ---
    "query_operation_logs": {"group": "ops", "side_effect": "read",       "requires_confirmation": False, "short_desc": "查询日志"},
    "get_operation_log":    {"group": "ops", "side_effect": "read",       "requires_confirmation": False, "short_desc": "获取日志"},
    "undo_operation":       {"group": "ops", "side_effect": "write",      "requires_confirmation": False, "short_desc": "撤销操作"},
    "list_recent_operations":{"group": "ops", "side_effect": "read",      "requires_confirmation": False, "short_desc": "最近操作"},
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
}


# ---- Phase 4.2: Agent Memory Tools ----

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





# ---- Prompts ----


# ---- Profile-based registration ----

# Determine current profile from config
_CURRENT_PROFILE = resolve_tool_profile(
    {k: Config.get(k) for k in [
        "mcp.tool_profile", "mcp.write_policy", "mcp.allow_http_write",
        "mcp.auth_token", "mcp.enable_legacy_aliases",
        "mcp.experimental_tools_enabled",
    ]}
)
_EXPERIMENTAL_ENABLED = bool(Config.get("mcp.experimental_tools_enabled", False))
_ENABLE_ALIASES = bool(
    Config.get("mcp.enable_legacy_aliases", _CURRENT_PROFILE == "legacy")
)

# Select and register tools based on profile
_selected_tools = select_tools(_CURRENT_PROFILE, experimental_enabled=_EXPERIMENTAL_ENABLED)
register_tools(mcp, _selected_tools)
_VISIBLE_TOOL_NAMES = {d.name for d in _selected_tools}
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
