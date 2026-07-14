"""administration domain MCP tools (WP2 extraction from server.py).

Implementations registered via tool_definition side-effect on import.
"""
from __future__ import annotations

import functools
import json
import logging
import os
from typing import Callable, ParamSpec, TypeVar

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
from src.services.wiki_compiler import try_wiki_compile as _try_wiki_compile
from src.utils.config import Config

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


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
    content_hash = hashlib.sha256(content.encode("utf-8", errors="surrogatepass")).hexdigest()
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
        content_hash=content_hash,
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
        # BUG#6 修复：快照保留 quality，供 restore/undo 回填（防止恢复后 quality 丢失）
        "quality": existing.get("quality", ""),
        "quality_score": existing.get("quality_score"),
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

    # BUG#6 修复：恢复后回填 quality（若当前为空但删除快照中有值）。
    # 软删本身不动 quality，但某些路径可能已清空；从最近 delete 操作日志的
    # before 快照恢复，保证有值不丢失。空值属正常不强制回填。
    try:
        current = container.db.get_knowledge(item_id, include_deleted=False) or {}
        if not current.get("quality"):
            log_row = container.db.get_conn().execute(
                """SELECT snapshot_before FROM operation_logs
                   WHERE target_type = 'knowledge' AND target_id = ?
                     AND operation = 'delete'
                   ORDER BY created_at DESC LIMIT 1""",
                (item_id,),
            ).fetchone()
            if log_row and log_row["snapshot_before"]:
                import json as _json6
                snap = _json6.loads(log_row["snapshot_before"]) \
                    if isinstance(log_row["snapshot_before"], str) \
                    else log_row["snapshot_before"]
                snap_quality = snap.get("quality") if isinstance(snap, dict) else None
                if snap_quality:
                    container.db.update_knowledge(
                        item_id, quality=snap_quality
                    )
    except Exception as exc:
        logger.debug("quality backfill on restore skipped: %s", exc)

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
    # 默认排除观测 trace（target_type='trace'），保持审计列表纯净；
    # 显式查 trace 时（target_type='trace'）不排除。
    exclude_trace = target_type != "trace"
    logs = _get_container().operation_log_repo.query(
        target_type=target_type, target_id=target_id,
        operation=operation, source=source,
        limit=limit, offset=offset, exclude_trace=exclude_trace,
    )
    total = _get_container().operation_log_repo.count(
        target_type=target_type, operation=operation, exclude_trace=exclude_trace,
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
