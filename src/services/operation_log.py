"""操作日志服务 — 统一记录所有写操作审计日志"""
import json
import logging
from datetime import datetime
from typing import Any, Optional

from src.utils.config import Config

logger = logging.getLogger(__name__)


# Phase 4: 可撤销的操作类型 + target_type 组合
_UNDOABLE_KINDS: dict[tuple[str, str], str] = {
    ("update", "knowledge"): "restore_update",
    ("create", "knowledge"): "undo_create",
    ("delete", "knowledge"): "restore_delete",
    ("ingest", "knowledge"): "undo_ingest",
}


class OperationLogService:
    def __init__(self, repo=None, knowledge_repo=None):
        self._repo = repo
        self._knowledge_repo = knowledge_repo

    def attach_knowledge_repo(self, knowledge_repo) -> None:
        """Phase 4：注入 knowledge_repo 以支持 undo。"""
        self._knowledge_repo = knowledge_repo

    def log(self, operation: str, target_type: str, target_id: str,
            operator: str = "system", source: str = "mcp",
            before: dict | None = None,
            after: dict | None = None,
            metadata: dict | None = None) -> str:
        if not Config.get("safety.operation_log.enabled", True):
            return ""
        try:
            return str(self._repo.insert({
                "operation": operation,
                "target_type": target_type,
                "target_id": target_id,
                "operator": operator,
                "source": source,
                "snapshot_before": json.dumps(before, ensure_ascii=False, default=str) if before else None,
                "snapshot_after": json.dumps(after, ensure_ascii=False, default=str) if after else None,
                "metadata": metadata or {},
                "created_at": datetime.now().isoformat(),
            }))
        except Exception:
            logger.warning("Failed to log operation %s on %s/%s", operation, target_type, target_id, exc_info=True)
            return ""

    def query(self, target_type=None, target_id=None, operation=None,
              source=None, limit=50, offset=0) -> list[dict]:
        return list(self._repo.query(
            target_type=target_type, target_id=target_id,
            operation=operation, source=source,
            limit=limit, offset=offset,
        ))

    def get_by_target(self, target_type: str, target_id: str, limit=20) -> list[dict]:
        return list(self._repo.get_by_target(target_type, target_id, limit=limit))

    # ---- Phase 4 / Sprint 3: undo ----

    def can_undo(self, log_id: str) -> bool:
        """检查某条操作是否可撤销。"""
        entry = self._repo.get_by_id(log_id)
        if not entry:
            return False
        return (entry.get("operation"), entry.get("target_type")) in _UNDOABLE_KINDS

    def undo(self, log_id: str, operator: str = "system") -> dict:
        """根据 operation_log 记录执行反向操作。

        Args:
            log_id: 要撤销的 operation_log ID
            operator: 撤销操作的操作者

        Returns:
            ``{"ok": True, "data": {"undone_log_id": "...", "operation": "..."}}``
            或 ``{"ok": False, "error": {"code": "...", "message": "..."}}``
        """
        if self._repo is None:
            return {"ok": False, "error": {"code": "INTERNAL_ERROR",
                                            "message": "operation_log repo 未初始化"}}

        entry = self._repo.get_by_id(log_id)
        if not entry:
            return {"ok": False, "error": {"code": "NOT_FOUND",
                                            "message": f"operation_log 不存在: {log_id}",
                                            "details": {"log_id": log_id}}}

        op = entry.get("operation")
        target_type = entry.get("target_type")
        target_id = entry.get("target_id")
        undo_op = _UNDOABLE_KINDS.get((op, target_type))
        if not undo_op:
            return {"ok": False, "error": {
                "code": "PRECONDITION_FAILED",
                "message": f"操作 {op}/{target_type} 不支持撤销",
                "details": {"operation": op, "target_type": target_type,
                             "supported": list(_UNDOABLE_KINDS.keys())},
            }}

        if self._knowledge_repo is None:
            return {"ok": False, "error": {"code": "INTERNAL_ERROR",
                                            "message": "knowledge_repo 未注入，无法执行 undo"}}

        try:
            before_snapshot = _parse_snapshot(entry.get("snapshot_before"))
            after_snapshot = _parse_snapshot(entry.get("snapshot_after"))

            if undo_op == "restore_update":
                # 把字段回滚到 before
                if not before_snapshot:
                    return {"ok": False, "error": {
                        "code": "PRECONDITION_FAILED",
                        "message": "snapshot_before 缺失，无法撤销 update",
                    }}
                # 过滤掉不能 undo 的字段
                fields = _filter_undoable_fields(before_snapshot)
                if not fields:
                    return {"ok": False, "error": {
                        "code": "PRECONDITION_FAILED",
                        "message": f"snapshot_before 中无可撤销字段: {list(before_snapshot.keys())}",
                    }}
                # 记录当前状态到 log（用于二次 undo）
                current = self._knowledge_repo.get(target_id, include_deleted=True) or {}
                self._knowledge_repo.update(target_id, **fields)
                new_log_id = self.log(
                    "undo_update", "knowledge", target_id, operator=operator,
                    before=_serialize_for_log(current),
                    after=fields,
                    metadata={"undone_log_id": log_id, "original_operation": op},
                )
                return {"ok": True, "data": {
                    "undone_log_id": new_log_id,
                    "operation": "undo_update",
                    "target_id": target_id,
                    "restored_fields": list(fields.keys()),
                }}

            if undo_op == "undo_create":
                # 把刚创建的条目软删
                ok_flag = self._knowledge_repo.soft_delete_knowledge(target_id) \
                    if hasattr(self._knowledge_repo, "soft_delete_knowledge") \
                    else self._knowledge_repo.delete(target_id, hard=False)
                # 走 repo 的 soft-delete 路径（get 会过滤掉，所以用 include_deleted 检查）
                # 通过 metadata 记录（undone log）
                new_log_id = self.log(
                    "undo_create", "knowledge", target_id, operator=operator,
                    before=after_snapshot or {},
                    metadata={"undone_log_id": log_id, "original_operation": op,
                              "soft_deleted": True},
                )
                return {"ok": True, "data": {
                    "undone_log_id": new_log_id,
                    "operation": "undo_create",
                    "target_id": target_id,
                    "soft_deleted": True,
                }}

            if undo_op == "undo_ingest":
                # 导入的反向操作是软删导入产生的条目。
                ok_flag = self._knowledge_repo.soft_delete_knowledge(target_id) \
                    if hasattr(self._knowledge_repo, "soft_delete_knowledge") \
                    else self._knowledge_repo.delete(target_id, hard=False)
                if ok_flag is False:
                    return {"ok": False, "error": {
                        "code": "PRECONDITION_FAILED",
                        "message": f"条目 {target_id} 已删除或不存在，无法撤销导入",
                    }}
                new_log_id = self.log(
                    "undo_ingest", "knowledge", target_id, operator=operator,
                    before=after_snapshot or {},
                    metadata={"undone_log_id": log_id, "original_operation": op,
                              "soft_deleted": True},
                )
                return {"ok": True, "data": {
                    "undone_log_id": new_log_id,
                    "operation": "undo_ingest",
                    "target_id": target_id,
                    "soft_deleted": True,
                }}

            if undo_op == "restore_delete":
                # 软删 → 恢复（清除 deleted_at）
                if not self._knowledge_repo.restore(target_id):
                    # 可能条目已被硬删或从未软删
                    return {"ok": False, "error": {
                        "code": "PRECONDITION_FAILED",
                        "message": f"条目 {target_id} 不在软删状态，无法恢复",
                    }}
                new_log_id = self.log(
                    undo_op, "knowledge", target_id, operator=operator,
                    after={"restored": True},
                    metadata={"undone_log_id": log_id, "original_operation": op},
                )
                return {"ok": True, "data": {
                    "undone_log_id": new_log_id,
                    "operation": undo_op,
                    "target_id": target_id,
                    "restored": True,
                }}

            return {"ok": False, "error": {
                "code": "INTERNAL_ERROR",
                "message": f"未实现的 undo_op: {undo_op}",
            }}
        except Exception as exc:
            logger.exception("undo failed for log_id=%s", log_id)
            return {"ok": False, "error": {
                "code": "INTERNAL_ERROR",
                "message": f"撤销失败: {exc}",
                "details": {"log_id": log_id, "exception": str(exc)},
            }}


def _parse_snapshot(raw: Any) -> Optional[dict]:
    """把 snapshot_before/after 字段解析成 dict（None → None）。"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _filter_undoable_fields(snapshot: dict) -> dict:
    """从 snapshot 中挑出 knowledge_repo.update 可接受的字段。"""
    allowed = {"title", "content", "tags", "source_type", "source_path",
               "file_type", "file_size", "file_created_at", "file_modified_at", "quality"}
    return {k: v for k, v in snapshot.items() if k in allowed}


def _serialize_for_log(item: dict) -> dict:
    """把 knowledge_item dict 序列化成 log-friendly dict。"""
    if not item:
        return {}
    out = {}
    for k in ("title", "content", "tags", "source_type", "source_path",
              "file_type", "file_size", "quality"):
        v = item.get(k)
        if v is not None:
            out[k] = v
    return out
