"""ingest domain MCP tools (WP2 extraction from server.py).

Implementations registered via tool_definition side-effect on import.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import ParamSpec, TypeVar

from src.mcp.envelopes import (
    ErrorCode,
    attach_operation_id,
    dry_run_preview,
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
    get_container as _get_container,
)
from src.mcp.tools.support import (
    heartbeat as _heartbeat,
)
from src.mcp.tools.support import (
    op_log as _op_log,
)
from src.services.file_parser import parse_file, parse_url
from src.services.wiki_compiler import try_wiki_compile as _try_wiki_compile
from src.utils.config import Config

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


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
    total = db.count_knowledge(tag=tag, file_type=file_type)
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

def _resolve_parse_file():
    """Prefer server.parse_file so tests can monkeypatch src.mcp.server.parse_file."""
    import sys

    server = sys.modules.get("src.mcp.server")
    if server is not None:
        fn = getattr(server, "parse_file", None)
        if callable(fn):
            return fn
    return parse_file


def _do_ingest_file(file_path: str, tags: list[str] | None = None) -> dict:
    tags = tags or []

    # 安全校验：路径规范化 + 白名单验证
    validated_path = _validate_file_path(file_path)
    parsed_list = _resolve_parse_file()(validated_path)

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
        content_hash = hashlib.sha256(parsed.content.encode("utf-8", errors="surrogatepass")).hexdigest()
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
            content_hash=content_hash,
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

    content_hash = hashlib.sha256(parsed.content.encode("utf-8", errors="surrogatepass")).hexdigest()
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
        content_hash=content_hash,
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
    # 失败：查询当前状态给出具体原因，便于 Agent 区分「已完成 / 已失败 / 不存在」，
    # 而不是统一返回模糊的 PRECONDITION_FAILED。
    current_status = "not_found"
    try:
        job = _get_container().db.get_job(job_id)
        if job:
            current_status = job.get("status", "unknown")
    except Exception as exc:
        logger.debug("cancel_job: failed to inspect job status: %s", exc)
        current_status = "unknown"
    return fail(
        ErrorCode.PRECONDITION_FAILED,
        f"无法取消：任务当前状态为 {current_status}（仅 pending/running 可取消）",
        job_id=job_id,
        current_status=current_status,
    )
