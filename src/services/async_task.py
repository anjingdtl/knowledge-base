"""异步任务数据模型与数据库操作"""
import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

from src.services.db import Database


class JobPriority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class JobStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AsyncJob:
    id: str
    job_type: str
    status: str
    params: dict = field(default_factory=dict)
    progress: int = 0
    progress_message: str = ""
    result: Optional[dict] = None
    error_message: str = ""
    retry_count: int = 0
    max_retries: int = 3
    priority: int = 1
    created_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    @classmethod
    def _safe_json_parse(cls, value) -> dict | list | None:
        """安全 JSON 解析 — 跳过已解析的 dict/list/None。"""
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return value

    @classmethod
    def from_db(cls, row: dict) -> "AsyncJob":
        """从数据库行转换为 AsyncJob"""
        return cls(
            id=row["id"],
            job_type=row["job_type"],
            status=row["status"],
            params=cls._safe_json_parse(row.get("params", "{}")) or {},
            progress=row.get("progress", 0),
            progress_message=row.get("progress_message", ""),
            result=cls._safe_json_parse(row.get("result")),
            error_message=row.get("error_message", ""),
            retry_count=row.get("retry_count", 0),
            max_retries=row.get("max_retries", 3),
            priority=row.get("priority", 1),
            created_at=row.get("created_at", ""),
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
        )


class AsyncTaskService:
    """异步任务服务封装"""

    @staticmethod
    def create_job(job_type: str, params: dict | None = None,
                   priority: int = 1, max_retries: int = 3) -> str:
        """创建新任务"""
        return Database.create_job(job_type, params, priority, max_retries)

    @staticmethod
    def get_job(job_id: str) -> Optional[AsyncJob]:
        """获取任务"""
        row = Database.get_job(job_id)
        return AsyncJob.from_db(row) if row else None

    @staticmethod
    def list_jobs(status: str | None = None, job_type: str | None = None,
                  limit: int = 50, offset: int = 0) -> list[AsyncJob]:
        """列出任务"""
        rows = Database.list_jobs(status, job_type, limit, offset)
        return [AsyncJob.from_db(r) for r in rows]

    @staticmethod
    def update_progress(job_id: str, progress: int, message: str = ""):
        """更新进度"""
        Database.update_job_progress(job_id, progress, message)

    @staticmethod
    def update_status(job_id: str, status: str,
                      result: dict | None = None, error: str = ""):
        """更新状态"""
        Database.update_job_status(job_id, status, result, error)

    @staticmethod
    def claim_job() -> Optional[AsyncJob]:
        """认领下一个待处理任务"""
        row = Database.claim_next_pending_job()
        return AsyncJob.from_db(row) if row else None

    @staticmethod
    def cancel_job(job_id: str) -> bool:
        """取消任务"""
        return Database.cancel_job(job_id)

    @staticmethod
    def delete_job(job_id: str) -> bool:
        """删除已完成任务"""
        return Database.delete_job(job_id)

    @staticmethod
    def cleanup(retention_days: int = 7):
        """清理过期任务"""
        Database.cleanup_old_jobs(retention_days)

    @staticmethod
    def get_stats() -> dict:
        """获取统计"""
        return Database.get_job_stats()
