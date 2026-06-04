"""MCP / API 统一返回信封 — Phase 0+1 重构基础设施。

所有 MCP 工具与受控 API 路由统一返回以下结构：

成功：
    {"ok": true, "data": <payload>, "meta": {...}, "operation_id": "<uuid>"}

失败：
    {"ok": false, "error": {"code": "<CODE>", "message": "...", "details": {...}}}

dry_run 预览：
    {"ok": true, "dry_run": true, "data": {"would_change": {...}}, "meta": {...}}

Agent 客户端应使用 ``ok`` 字段做成功/失败分支，``error.code`` 做细粒度判断。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


class ErrorCode:
    """稳定错误码常量 — Agent 用作 if-elif 分支判断。"""

    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INGEST_FAILED = "INGEST_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    WIKI_DISABLED = "WIKI_DISABLED"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    QUERY_PARSE_ERROR = "QUERY_PARSE_ERROR"


@dataclass
class ResponseEnvelope:
    """信封数据模型。"""

    ok: bool
    data: Any = None
    error: Optional[dict] = None
    meta: dict = field(default_factory=dict)
    operation_id: Optional[str] = None
    dry_run: bool = False

    def to_dict(self) -> dict:
        """序列化为 dict；空字段省略以减小 payload。"""
        d: dict = {"ok": self.ok}
        if self.data is not None:
            d["data"] = self.data
        if self.error is not None:
            d["error"] = self.error
        if self.meta:
            d["meta"] = self.meta
        if self.operation_id:
            d["operation_id"] = self.operation_id
        if self.dry_run:
            d["dry_run"] = True
        return d


def ok(
    data: Any = None,
    *,
    operation_id: str | None = None,
    **meta: Any,
) -> dict:
    """构造成功信封。"""
    return ResponseEnvelope(
        ok=True,
        data=data,
        meta=dict(meta),
        operation_id=operation_id,
    ).to_dict()


def fail(code: str, message: str, **details: Any) -> dict:
    """构造失败信封。"""
    return ResponseEnvelope(
        ok=False,
        error={"code": code, "message": message, "details": details},
    ).to_dict()


def dry_run_preview(would_change: dict, **meta: Any) -> dict:
    """构造 dry_run 预览信封。``data.would_change`` 包含将变更的字段。"""
    return ResponseEnvelope(
        ok=True,
        dry_run=True,
        data={"would_change": would_change},
        meta=dict(meta),
    ).to_dict()


def attach_operation_id(envelope: dict, log_id: str | None) -> dict:
    """把 operation_log 写入后得到的 log_id 注入 envelope。

    若 ``log_id`` 为空（写入失败或未启用），不动 envelope。
    """
    if log_id:
        envelope["operation_id"] = log_id
    return envelope
