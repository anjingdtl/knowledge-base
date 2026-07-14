"""wiki domain MCP tools (WP2 extraction from server.py).

Implementations registered via tool_definition side-effect on import.
"""
from __future__ import annotations

import logging
from typing import ParamSpec, TypeVar

from src.mcp.envelopes import (
    ErrorCode,
    attach_operation_id,
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
from src.utils.config import Config

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


@_define_tool(
    name="save_to_wiki",
    description="将好的问答回答保存为 Wiki 页面，实现知识沉淀和复利增长。",
    annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False},
    group="wiki", side_effect="write",
    experimental=True,
)
@_heartbeat
def save_to_wiki(
    question: str,
    answer: str,
    source_ids: list[str] | None = None,
    auto_publish: bool | None = None,
    enhance: bool = True,
    save_mode: str = "manual",
    confidence: float = 0.0,
) -> dict:
    """将问答保存为 Wiki 页面。

    Args:
        question: 用户的问题
        answer: AI 的回答
        source_ids: 引用的知识条目 ID 列表
        auto_publish: 是否直接发布（True=published, False=draft 走审核流）。
            None（默认）沿用 Config 'wiki.auto_publish' 配置（默认 True）。
        enhance: 是否调用 LLM 增强内容（补背景、规范化、生成摘要/标签）。
            False 时直接用原始 answer 存储。默认 True。
    """
    _guard = _check_write_policy("save_to_wiki")
    if _guard:
        return _guard
    if not Config.get("wiki.enabled", False):
        return fail(ErrorCode.WIKI_DISABLED, "Wiki 功能未启用")
    container = _get_container()
    wr = container.wiki_write_service.save(
        question, answer, source_ids,
        confidence=confidence, save_mode=save_mode,
        auto_publish=auto_publish, enhance=enhance, timestamp="",
    )
    page_id = wr["sqlite_page_id"]
    if wr["errors"]:
        logger.warning("save_to_wiki partial failures: %s", wr["errors"])
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
    description="使用 LLM 智能修复 Wiki 页面中 [[...]] 死链，并自动清理过时的 source_ids。"
    "对每个死链分析上下文后选择修复策略：重定向到已有页面、创建占位页面或移除引用。"
    "同时自动清理 source_ids 中指向已删除知识条目的过时引用（lint 报告的 stale 问题）。"
    "修复前会先尝试解析有效引用写入 wiki_links 表。"
    "注意：死链修复会消耗 LLM 调用次数（每个含死链的页面约 1 次 LLM 调用）。",
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
        # 仅报告，不修复（同时包含 stale 扫描结果）
        from src.services.wiki_lint import WikiLint
        lint_report = WikiLint().run()
        stale_findings = [f for f in lint_report.get("findings", []) if f.get("category") == "stale"]
        return ok({
            "mode": "dry_run",
            "links_created": link_result["links_created"],
            "dead_reference_count": link_result["dead_reference_count"],
            "dead_references": link_result["dead_references"],
            "stale_pages": len(stale_findings),
            "stale_details": stale_findings,
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

@_define_tool(
    name="delete_wiki_page",
    description="删除 Wiki 页面及其链接与操作日志（硬删除，不可恢复）。",
    annotations={"destructiveHint": True},
    group="wiki", side_effect="destructive",
    experimental=True,
)
@_heartbeat
def delete_wiki_page(page_id: str) -> dict:
    """删除 Wiki 页面（BUG#12：补齐 wiki 删除能力，此前仅 knowledge 可删）。

    Args:
        page_id: 要删除的 Wiki 页面 ID
    """
    _guard = _check_write_policy("delete_wiki_page")
    if _guard:
        return _guard
    container = _get_container()
    existing = container.db.get_wiki_page(page_id)
    if not existing:
        return fail(
            ErrorCode.NOT_FOUND,
            f"Wiki 页面不存在: {page_id}",
            page_id=page_id,
        )
    deleted_page = {
        "title": existing.get("title", ""),
        "status": existing.get("status", ""),
    }
    log_id = _op_log("delete", "wiki_page", page_id, before=deleted_page)
    container.wiki_repo.delete_page(page_id)
    envelope = ok({
        "page_id": page_id,
        "deleted": True,
        "message": "Wiki 页面已删除",
    })
    return attach_operation_id(envelope, log_id)
