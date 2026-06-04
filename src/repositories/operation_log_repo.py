"""操作日志仓库 — operation_logs CRUD"""
import json
import threading
import uuid
from datetime import datetime
from typing import Optional

from src.services.db import Database


class OperationLogRepository:
    def __init__(self, db=None):
        self._db = db or Database
        self._write_lock = threading.Lock()

    def _conn(self):
        return self._db.get_conn()

    def insert(self, log_entry: dict) -> str:
        log_id = log_entry.get("id") or str(uuid.uuid4())
        self._conn().execute(
            """INSERT INTO operation_logs
               (id, operation, target_type, target_id, operator, source,
                snapshot_before, snapshot_after, metadata, created_at)
               VALUES (:id, :operation, :target_type, :target_id, :operator, :source,
                :snapshot_before, :snapshot_after, :metadata, :created_at)""",
            {
                "id": log_id,
                "operation": log_entry["operation"],
                "target_type": log_entry["target_type"],
                "target_id": log_entry["target_id"],
                "operator": log_entry.get("operator", "system"),
                "source": log_entry.get("source", "mcp"),
                "snapshot_before": log_entry["snapshot_before"] if isinstance(log_entry.get("snapshot_before"), str) else json.dumps(log_entry["snapshot_before"], ensure_ascii=False, default=str) if log_entry.get("snapshot_before") else None,
                "snapshot_after": log_entry["snapshot_after"] if isinstance(log_entry.get("snapshot_after"), str) else json.dumps(log_entry["snapshot_after"], ensure_ascii=False, default=str) if log_entry.get("snapshot_after") else None,
                "metadata": json.dumps(log_entry.get("metadata") or {}, ensure_ascii=False),
                "created_at": log_entry.get("created_at") or datetime.now().isoformat(),
            },
        )
        self._conn().commit()
        return log_id

    def query(self, target_type=None, target_id=None, operation=None,
              source=None, limit=50, offset=0) -> list[dict]:
        conditions, params = [], []
        if target_type:
            conditions.append("target_type = ?")
            params.append(target_type)
        if target_id:
            conditions.append("target_id = ?")
            params.append(target_id)
        if operation:
            conditions.append("operation = ?")
            params.append(operation)
        if source:
            conditions.append("source = ?")
            params.append(source)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self._conn().execute(
            f"SELECT * FROM operation_logs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    def get_by_target(self, target_type: str, target_id: str, limit=20) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM operation_logs WHERE target_type = ? AND target_id = ? ORDER BY created_at DESC LIMIT ?",
            (target_type, target_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_by_id(self, log_id: str) -> dict | None:
        """按 log_id 查询单条记录。Phase 4 的 ``get_operation_log`` 工具使用。"""
        row = self._conn().execute(
            "SELECT * FROM operation_logs WHERE id = ?", (log_id,),
        ).fetchone()
        return dict(row) if row else None

    def count(self, target_type=None, operation=None) -> int:
        conditions, params = [], []
        if target_type:
            conditions.append("target_type = ?")
            params.append(target_type)
        if operation:
            conditions.append("operation = ?")
            params.append(operation)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        row = self._conn().execute(
            f"SELECT COUNT(*) as cnt FROM operation_logs{where}", params
        ).fetchone()
        return row["cnt"]

    def cleanup(self, retention_days: int = 90):
        cutoff = datetime.now().isoformat()
        from datetime import timedelta
        try:
            dt = datetime.now() - timedelta(days=retention_days)
            cutoff = dt.isoformat()
        except Exception:
            pass
        self._conn().execute(
            "DELETE FROM operation_logs WHERE created_at < ?", (cutoff,)
        )
        self._conn().commit()
