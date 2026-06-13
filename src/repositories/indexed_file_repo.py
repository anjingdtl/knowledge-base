"""indexed_files 仓库 — 文件索引追踪记录 CRUD"""
from __future__ import annotations

import os
import threading
from typing import Optional

from src.services.db import Database


def _normalize_path(path: str) -> str:
    """Windows 兼容的路径标准化"""
    return os.path.normcase(os.path.normpath(path))


class IndexedFileRepository:
    """indexed_files 表的仓库层

    所有写入操作通过 threading.Lock 串行化，避免 SQLite 并发写入冲突。
    路径在存储前统一 normcase + normpath，确保 Windows 下大小写/斜杠一致。
    """

    def __init__(self, db=None):
        self._db = db or Database
        self._write_lock = threading.Lock()

    def _conn(self):
        return self._db.get_conn()

    # ---- CRUD ----

    def get(self, path: str) -> Optional[dict]:
        """按标准化路径查询记录"""
        norm = _normalize_path(path)
        row = self._conn().execute(
            "SELECT * FROM indexed_files WHERE path = ?", (norm,)
        ).fetchone()
        return dict(row) if row else None

    def upsert(self, record: dict) -> None:
        """插入或更新文件记录

        必需键: path, size, mtime_ns, sha256
        可选键: knowledge_id, status, last_indexed_at, last_error
        """
        norm = _normalize_path(record["path"])
        with self._write_lock:
            self._conn().execute(
                """INSERT INTO indexed_files
                   (path, knowledge_id, size, mtime_ns, sha256, status, last_indexed_at, last_error)
                   VALUES (:path, :knowledge_id, :size, :mtime_ns, :sha256,
                           :status, :last_indexed_at, :last_error)
                   ON CONFLICT(path) DO UPDATE SET
                     knowledge_id = excluded.knowledge_id,
                     size = excluded.size,
                     mtime_ns = excluded.mtime_ns,
                     sha256 = excluded.sha256,
                     status = excluded.status,
                     last_indexed_at = excluded.last_indexed_at,
                     last_error = excluded.last_error""",
                {
                    "path": norm,
                    "knowledge_id": record.get("knowledge_id"),
                    "size": record["size"],
                    "mtime_ns": record["mtime_ns"],
                    "sha256": record["sha256"],
                    "status": record.get("status", "pending"),
                    "last_indexed_at": record.get("last_indexed_at"),
                    "last_error": record.get("last_error"),
                },
            )
            self._conn().commit()

    def mark_failed(self, path: str, error: str) -> None:
        """标记文件为 failed 状态并记录错误信息"""
        norm = _normalize_path(path)
        with self._write_lock:
            self._conn().execute(
                "UPDATE indexed_files SET status = 'failed', last_error = ? WHERE path = ?",
                (error, norm),
            )
            self._conn().commit()

    def mark_deleted(self, path: str) -> None:
        """标记文件为 deleted 状态"""
        norm = _normalize_path(path)
        with self._write_lock:
            self._conn().execute(
                "UPDATE indexed_files SET status = 'deleted' WHERE path = ?",
                (norm,),
            )
            self._conn().commit()

    def list_by_root(self, root: str) -> list[dict]:
        """列出指定根目录下所有文件记录（path LIKE root||'%'）"""
        norm = _normalize_path(root)
        # 确保根路径以分隔符结尾，避免前缀误匹配
        if not norm.endswith(os.sep):
            norm = norm + os.sep
        rows = self._conn().execute(
            "SELECT * FROM indexed_files WHERE path LIKE ?",
            (norm + "%",),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_by_status(self, status: str, limit: int = 100) -> list[dict]:
        """按状态列出记录"""
        rows = self._conn().execute(
            "SELECT * FROM indexed_files WHERE status = ? LIMIT ?",
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, path: str) -> None:
        """硬删除记录"""
        norm = _normalize_path(path)
        with self._write_lock:
            self._conn().execute(
                "DELETE FROM indexed_files WHERE path = ?", (norm,)
            )
            self._conn().commit()
