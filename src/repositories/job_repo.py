"""异步任务仓库 — async_jobs"""
import json
from datetime import datetime
from typing import Optional


class JobRepository:
    """异步任务队列管理"""

    def __init__(self, db=None):
        from src.services.db import Database
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    def create_job(self, job_type: str, params: dict | None = None,
                   priority: int = 1, max_retries: int = 3) -> str:
        import uuid
        job_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        self._conn().execute(
            """INSERT INTO async_jobs
               (id, job_type, status, params, priority, max_retries, created_at)
               VALUES (?, ?, 'pending', ?, ?, ?, ?)""",
            (job_id, job_type, json.dumps(params or {}), priority, max_retries, now),
        )
        self._conn().commit()
        return job_id

    def get_job(self, job_id: str) -> Optional[dict]:
        row = self._conn().execute("SELECT * FROM async_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["params"] = json.loads(result.get("params", "{}"))
        result["result"] = json.loads(result["result"]) if result.get("result") else None
        return result

    def list_jobs(self, status: str | None = None, job_type: str | None = None,
                  limit: int = 50, offset: int = 0) -> list[dict]:
        conditions, params = [], []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if job_type:
            conditions.append("job_type = ?")
            params.append(job_type)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self._conn().execute(
            f"SELECT * FROM async_jobs{where} ORDER BY priority DESC, created_at ASC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    def update_progress(self, job_id: str, progress: int, message: str = ""):
        self._conn().execute(
            "UPDATE async_jobs SET progress = ?, progress_message = ? WHERE id = ?",
            (progress, message, job_id),
        )
        self._conn().commit()

    def update_status(self, job_id: str, status: str, result: dict | None = None, error: str = ""):
        now = datetime.now().isoformat()
        conn = self._conn()
        job = self.get_job(job_id)
        if not job:
            return
        if status == "running" and not job.get("started_at"):
            conn.execute(
                "UPDATE async_jobs SET status = ?, started_at = ?, progress = ? WHERE id = ?",
                (status, now, 0, job_id),
            )
        elif status in ("completed", "failed", "cancelled"):
            conn.execute(
                "UPDATE async_jobs SET status = ?, completed_at = ?, result = ?, error_message = ? WHERE id = ?",
                (status, now, json.dumps(result) if result else None, error, job_id),
            )
        else:
            conn.execute("UPDATE async_jobs SET status = ? WHERE id = ?", (status, job_id))
        conn.commit()

    def claim_next_pending(self) -> Optional[dict]:
        """认领下一个待处理任务。

        使用 ``BEGIN IMMEDIATE`` 独占事务缩小并发窗口；优先尝试
        ``RETURNING *``（需 SQLite >= 3.35），若不支持则回退到
        SELECT + UPDATE 两步操作。

        注意：此实现仅适用于单进程场景。多进程共享同一数据库时，
        请使用独立连接并在应用层加分布式锁。
        """
        import sqlite3
        conn = self._conn()
        now = datetime.now().isoformat()

        # --- 优先尝试 RETURNING *（单条原子语句）---
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """UPDATE async_jobs
                   SET status = 'running', started_at = ?
                   WHERE id = (
                       SELECT id FROM async_jobs
                       WHERE status = 'pending'
                       ORDER BY priority DESC, created_at ASC LIMIT 1
                   )
                   RETURNING *""",
                (now,),
            ).fetchone()
            conn.commit()
            return dict(row) if row else None
        except sqlite3.OperationalError:
            # RETURNING 不被支持或 BEGIN 失败，回退到两步操作
            conn.rollback()

        # --- 回退：SELECT + UPDATE（SQLite < 3.35）---
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT id FROM async_jobs
                   WHERE status = 'pending'
                   ORDER BY priority DESC, created_at ASC LIMIT 1""",
            ).fetchone()
            if not row:
                conn.commit()
                return None
            job_id = row["id"]
            conn.execute(
                """UPDATE async_jobs
                   SET status = 'running', started_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (now, job_id),
            )
            conn.commit()
            # 重新查询完整行
            updated = conn.execute(
                "SELECT * FROM async_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return dict(updated) if updated else None
        except sqlite3.OperationalError:
            conn.rollback()
            return None

    def cancel_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        if job["status"] in ("pending", "running"):
            self.update_status(job_id, "cancelled")
            return True
        return False

    def delete_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job or job["status"] not in ("completed", "failed", "cancelled"):
            return False
        self._conn().execute("DELETE FROM async_jobs WHERE id = ?", (job_id,))
        self._conn().commit()
        return True

    def cleanup_old_jobs(self, retention_days: int = 7):
        self._conn().execute(
            """DELETE FROM async_jobs
               WHERE status IN ('completed', 'failed', 'cancelled')
               AND completed_at < datetime('now', 'localtime', '-' || ? || ' days')""",
            (retention_days,),
        )
        self._conn().commit()

    def get_job_stats(self) -> dict:
        rows = self._conn().execute(
            "SELECT status, COUNT(*) as count FROM async_jobs GROUP BY status"
        ).fetchall()
        return {row["status"]: row["count"] for row in rows}
