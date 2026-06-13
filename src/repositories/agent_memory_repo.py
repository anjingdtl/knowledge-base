"""Agent Memory 仓库 — agent_memory CRUD + FTS5 搜索"""
import json
import threading
import uuid
from datetime import datetime, timedelta

from src.services.db import Database


class AgentMemoryRepository:
    """agent_memory 表的 CRUD 操作"""

    def __init__(self, db=None):
        self._db = db or Database
        self._write_lock = threading.Lock()

    def _conn(self):
        return self._db.get_conn()

    # ---- 写操作 ----

    def store(self, key: str, value: str, category: str = "fact",
              metadata: dict | None = None) -> str:
        """存储一条记忆，返回 ID"""
        mem_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with self._write_lock:
            self._conn().execute(
                """INSERT INTO agent_memory (id, key, value, category, metadata, created_at, updated_at)
                   VALUES (:id, :key, :value, :category, :metadata, :created_at, :updated_at)""",
                {
                    "id": mem_id,
                    "key": key,
                    "value": value,
                    "category": category,
                    "metadata": json.dumps(metadata or {}, ensure_ascii=False),
                    "created_at": now,
                    "updated_at": now,
                },
            )
            self._conn().commit()
        return mem_id

    def upsert(self, key: str, value: str, category: str = "fact",
               metadata: dict | None = None) -> str:
        """按 key upsert：存在则更新，不存在则创建（整体加锁防竞态）"""
        now = datetime.now().isoformat()
        with self._write_lock:
            existing = self._conn().execute(
                "SELECT id FROM agent_memory WHERE key = ? LIMIT 1", (key,)
            ).fetchone()
            if existing:
                self._conn().execute(
                    """UPDATE agent_memory
                       SET value = :value, category = :category,
                           metadata = :metadata, updated_at = :updated_at
                       WHERE key = :key""",
                    {
                        "key": key,
                        "value": value,
                        "category": category,
                        "metadata": json.dumps(metadata or {}, ensure_ascii=False),
                        "updated_at": now,
                    },
                )
                self._conn().commit()
                return str(existing["id"])
        # 不存在，走 store（store 自己有锁）
        return self.store(key, value, category, metadata)

    def delete(self, memory_id: str) -> bool:
        """按 ID 删除"""
        with self._write_lock:
            cursor = self._conn().execute("DELETE FROM agent_memory WHERE id = ?", (memory_id,))
            self._conn().commit()
            return bool(cursor.rowcount > 0)

    def delete_by_key(self, key: str) -> bool:
        """按 key 删除"""
        with self._write_lock:
            cursor = self._conn().execute("DELETE FROM agent_memory WHERE key = ?", (key,))
            self._conn().commit()
            return bool(cursor.rowcount > 0)

    # ---- 读操作 ----

    def get_by_id(self, memory_id: str) -> dict | None:
        """按 ID 获取"""
        row = self._conn().execute(
            "SELECT * FROM agent_memory WHERE id = ?", (memory_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_by_key(self, key: str) -> dict | None:
        """按 key 获取（key 应唯一）"""
        row = self._conn().execute(
            "SELECT * FROM agent_memory WHERE key = ? LIMIT 1", (key,)
        ).fetchone()
        return dict(row) if row else None

    def list_all(self, category: str | None = None, limit: int = 50,
                 offset: int = 0) -> list[dict]:
        """列出记忆，支持按 category 过滤"""
        if category:
            rows = self._conn().execute(
                """SELECT * FROM agent_memory
                   WHERE category = ?
                   ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
                (category, limit, offset),
            ).fetchall()
        else:
            rows = self._conn().execute(
                """SELECT * FROM agent_memory
                   ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self, category: str | None = None) -> int:
        """统计数量"""
        if category:
            row = self._conn().execute(
                "SELECT COUNT(*) as cnt FROM agent_memory WHERE category = ?",
                (category,),
            ).fetchone()
        else:
            row = self._conn().execute(
                "SELECT COUNT(*) as cnt FROM agent_memory"
            ).fetchone()
        return row["cnt"] if row else 0

    # ---- 搜索 ----

    def search_fts(self, query: str, category: str | None = None,
                   limit: int = 10) -> list[dict]:
        """FTS5 全文搜索"""
        params: list = [query, limit]
        cat_clause = "AND m.category = ?" if category else ""
        if category:
            params.insert(1, category)

        rows = self._conn().execute(
            f"""SELECT m.*, agent_memory_fts.rank
                FROM agent_memory_fts
                JOIN agent_memory m ON m.rowid = agent_memory_fts.rowid
                WHERE agent_memory_fts MATCH ? {cat_clause}
                ORDER BY agent_memory_fts.rank
                LIMIT ?""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def search_like(self, query: str, category: str | None = None,
                    limit: int = 10) -> list[dict]:
        """LIKE 模糊搜索（FTS 不可用时的 fallback）"""
        pattern = f"%{query}%"
        if category:
            rows = self._conn().execute(
                """SELECT * FROM agent_memory
                   WHERE (key LIKE ? OR value LIKE ?) AND category = ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (pattern, pattern, category, limit),
            ).fetchall()
        else:
            rows = self._conn().execute(
                """SELECT * FROM agent_memory
                   WHERE key LIKE ? OR value LIKE ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (pattern, pattern, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- 统计 ----

    def recent_changes(self, since_hours: int = 24) -> dict:
        """统计近期变更"""
        cutoff = (datetime.now() - timedelta(hours=since_hours)).isoformat()
        row = self._conn().execute(
            """SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN category = 'fact' THEN 1 END) as facts,
                COUNT(CASE WHEN category = 'decision' THEN 1 END) as decisions,
                COUNT(CASE WHEN category = 'context' THEN 1 END) as contexts,
                COUNT(CASE WHEN category = 'task' THEN 1 END) as tasks
               FROM agent_memory WHERE updated_at >= ?""",
            (cutoff,),
        ).fetchone()
        return dict(row) if row else {"total": 0, "facts": 0, "decisions": 0, "contexts": 0, "tasks": 0}

    def ensure_table(self):
        """确保表存在（用于测试和首次运行）"""
        self._conn().execute("""
            CREATE TABLE IF NOT EXISTS agent_memory (
                id TEXT PRIMARY KEY,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'fact',
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self._conn().execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_memory_key ON agent_memory(key)
        """)
        self._conn().execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_memory_category ON agent_memory(category)
        """)
        self._conn().execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_memory_updated ON agent_memory(updated_at)
        """)
        # FTS5（可能已存在）
        try:
            self._conn().execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS agent_memory_fts USING fts5(
                    key, value,
                    content=agent_memory,
                    content_rowid=rowid,
                    tokenize='unicode61'
                )
            """)
            # FTS5 同步触发器
            self._conn().execute("""
                CREATE TRIGGER IF NOT EXISTS agent_memory_ai AFTER INSERT ON agent_memory BEGIN
                    INSERT INTO agent_memory_fts(rowid, key, value)
                    VALUES (new.rowid, new.key, new.value);
                END
            """)
            self._conn().execute("""
                CREATE TRIGGER IF NOT EXISTS agent_memory_ad AFTER DELETE ON agent_memory BEGIN
                    INSERT INTO agent_memory_fts(agent_memory_fts, rowid, key, value)
                    VALUES ('delete', old.rowid, old.key, old.value);
                END
            """)
            self._conn().execute("""
                CREATE TRIGGER IF NOT EXISTS agent_memory_au AFTER UPDATE ON agent_memory BEGIN
                    INSERT INTO agent_memory_fts(agent_memory_fts, rowid, key, value)
                    VALUES ('delete', old.rowid, old.key, old.value);
                    INSERT INTO agent_memory_fts(rowid, key, value)
                    VALUES (new.rowid, new.key, new.value);
                END
            """)
        except Exception:
            pass
        self._conn().commit()
