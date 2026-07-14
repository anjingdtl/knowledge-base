"""SQLite 数据库操作 — 含版本控制与全文索引"""
import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from src.utils.config import Config

# sqlite_vec 提供 vec0 虚拟表模块（向量检索）。每条 SQLite 连接都必须
# 单独加载该扩展，否则查询 vec_chunks / vec_blocks 时会抛
# OperationalError: no such module: vec0。这里做软导入：未安装时降级为
# 关键词检索，而不是让整个 db 模块无法加载。
try:
    import sqlite_vec as _sqlite_vec
except Exception:  # pragma: no cover - 仅在精简环境触发
    _sqlite_vec = None

logger = logging.getLogger(__name__)

# Legacy schema SQL lives in src.compatibility.runtime_schema_migrate (WP5).
# Re-export for tests/tools that historically imported _SCHEMA from this module.
from src.compatibility.runtime_schema_migrate import (  # noqa: E402
    LEGACY_SCHEMA_SQL as _SCHEMA,  # noqa: F401  # re-export for historical imports
)


class _DatabaseMeta(type):
    """元类：自动将 Database.xxx() 类级调用委托到 Database._instance。

    Database.count_knowledge()  →  Database._instance.count_knowledge()
    db.count_knowledge()        →  正常实例方法调用（不经过此元类）
    """

    def __getattribute__(cls, name):
        # dunder / 元类内部属性 → 直接解析（避免无限递归）
        if name.startswith('__') or name.startswith('_DatabaseMeta'):
            return type.__getattribute__(cls, name)

        # _instance 和 @classmethod（connect）→ 直接解析
        cls_dict = type.__getattribute__(cls, '__dict__')
        if name == '_instance':
            return cls_dict.get('_instance')
        raw = cls_dict.get(name)
        if raw is not None and isinstance(raw, classmethod):
            return type.__getattribute__(cls, name)

        # 其他属性（实例方法等）→ 委托到 _instance
        inst = cls_dict.get('_instance')
        if inst is not None:
            return getattr(inst, name)

        return type.__getattribute__(cls, name)


class Database(metaclass=_DatabaseMeta):
    """SQLite 数据库操作层 — 实例模式 + 向后兼容委托

    推荐用法:
        db = Database(db_path)           # 创建实例
        db.list_knowledge()              # 调用实例方法

    向后兼容（通过 _bind_to_instance 描述符自动委托到 _instance）:
        Database.connect(db_path)        # 创建全局实例
        Database.list_knowledge()        # 委托到 Database._instance

    DI 注入:
        container.db.list_knowledge()    # 通过 Container
    """
    _instance: "Database | None" = None  # 全局实例引用（向后兼容入口）

    def __init__(self, db_path: str | Path):
        """Legacy/test constructor — applies compatibility schema helpers (not production).

        Prefer Database.open_runtime() after bootstrap gate for production boot.
        """
        self._db_path = str(db_path)
        self._local = threading.local()
        self._write_lock = threading.RLock()  # 可重入锁，防止嵌套调用死锁
        self._shutdown: bool = False
        self._base_conn: Optional[sqlite3.Connection] = None
        self._readonly: bool = False
        self._connect_internal()

    @classmethod
    def open_runtime(
        cls,
        db_path: str | Path,
        *,
        readonly: bool = False,
    ) -> "Database":
        """Open runtime DB after Migration Gate (caller must gate first).

        Runtime open never creates or upgrades formal schema (WP3-T4):
        only connection, PRAGMA, sqlite-vec, thread-local.

        - write: requires existing file (create via Alembic / legacy Database())
        - readonly: SQLite mode=ro; no WAL
        """
        if readonly:
            return cls._open_readonly_runtime(db_path)
        return cls._open_write_runtime(db_path)

    @classmethod
    def _open_write_runtime(cls, db_path: str | Path) -> "Database":
        """Write-mode runtime open — no _SCHEMA / _migrate."""
        path = Path(db_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"runtime open requires an existing database file "
                f"(use Alembic to create schema first): {path}"
            )
        obj = object.__new__(cls)
        obj._db_path = str(path)
        obj._local = threading.local()
        obj._write_lock = threading.RLock()
        obj._shutdown = False
        obj._readonly = False
        obj._base_conn = sqlite3.connect(
            str(path), check_same_thread=False, timeout=30.0
        )
        obj._configure_connection(obj._base_conn, readonly=False)
        Database._instance = obj
        return obj

    @classmethod
    def _open_readonly_runtime(cls, db_path: str | Path) -> "Database":
        path = Path(db_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"readonly open requires an existing database file: {path}"
            )
        obj = object.__new__(cls)
        obj._db_path = str(path)
        obj._local = threading.local()
        obj._write_lock = threading.RLock()
        obj._shutdown = False
        obj._readonly = True
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        obj._base_conn = sqlite3.connect(
            uri, uri=True, check_same_thread=False, timeout=30.0
        )
        obj._configure_connection(obj._base_conn, readonly=True)
        Database._instance = obj
        return obj

    def _connect_internal(self):
        """Legacy/test init only: apply compatibility schema helpers.

        Production must use Database.open_runtime() after Alembic (no schema DDL).
        """
        from src.compatibility.runtime_schema_migrate import (
            apply_legacy_column_migrate,
            apply_legacy_schema,
        )

        self._base_conn = sqlite3.connect(
            self._db_path, check_same_thread=False, timeout=30.0
        )
        self._configure_connection(self._base_conn, readonly=False)
        apply_legacy_schema(self._base_conn)
        apply_legacy_column_migrate(self._base_conn)
        self._base_conn.commit()
        self._shutdown = False
        Database._instance = self  # 设置全局引用


    def _configure_connection(
        self, conn: sqlite3.Connection, *, readonly: bool | None = None
    ) -> sqlite3.Connection:
        """为新创建的 SQLite 连接设置标准 PRAGMA 和 row_factory。"""
        ro = self._readonly if readonly is None else readonly
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if not ro:
            # WAL creates -wal/-shm; forbidden in readonly diagnostic mode
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA cache_size = -64000")  # 64MB cache
        self._load_vec_extension(conn)
        return conn

    @staticmethod
    def _load_vec_extension(conn: sqlite3.Connection) -> None:
        """为新连接加载 sqlite_vec 扩展（注册 vec0 虚拟表模块）。

        get_conn() 使用 threading.local 线程本地连接，工作线程首次调用时
        会建立全新连接。若不在每条连接上加载扩展，vec0 虚拟表查询会抛
        ``no such module: vec0``，导致向量通道整体降级为关键词匹配。
        加载失败仅记日志，不阻断连接创建（保留纯关键词检索能力）。
        """
        if _sqlite_vec is None:
            return
        try:
            conn.enable_load_extension(True)
            try:
                _sqlite_vec.load(conn)
            finally:
                conn.enable_load_extension(False)
        except Exception as exc:
            logger.warning("Failed to load sqlite_vec on new connection: %s", exc)

    @classmethod
    def connect(cls, db_path: str | Path | None = None):
        """向后兼容：创建全局 Database 实例。

        - 文件已存在 → open_runtime（不跑 schema DDL）
        - 文件不存在 → legacy constructor（仅测试/兼容；生产用 Alembic）
        """
        if db_path is None:
            db_path = Config.get_db_path()
        path = Path(db_path)
        if path.is_file():
            cls.open_runtime(path, readonly=False)
        else:
            cls._instance = cls(str(path))

    # NOTE: get_conn() 使用线程本地连接（threading.local），定义在 _migrate() 之后。
    # 每个线程拥有独立的 SQLite 连接，避免 SQLITE_MISUSE 并发错误。

    def _migrate(self):
        """Removed from production authority (WP5).

        Historical column backfill lives in
        ``src.compatibility.runtime_schema_migrate.apply_legacy_column_migrate``.
        Use ``shinehe db migrate`` for real databases.
        """
        raise RuntimeError(
            "Runtime schema migration removed; run `shinehe db migrate` "
            "(or use compatibility.runtime_schema_migrate for test fixtures only)"
        )

    def get_conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接（线程本地模式）。

        每个线程维护独立的 SQLite 连接，避免多线程共享单连接导致的
        SQLITE_MISUSE 错误（"bad parameter or other API misuse"）。
        WAL 模式下多连接并发读不会互相阻塞，写操作通过 busy_timeout
        自动等待锁释放。

        首次调用时创建新连接；后续调用复用同线程的连接。
        包含轻量健康检查，断开时自动重连。
        """
        if self._shutdown:
            raise RuntimeError("Database is shut down — connection no longer available")

        conn: sqlite3.Connection | None = getattr(self._local, 'conn', None)
        if conn is not None:
            # 健康检查：轻量 SELECT 验证连接存活
            try:
                conn.execute("SELECT 1").fetchone()
                return conn
            except Exception:
                logger.warning("Thread-local SQLite connection stale, reconnecting")
                try:
                    conn.close()
                except Exception:
                    pass
                self._local.conn = None

        # 创建新的线程本地连接
        if self._db_path is None:
            if self._base_conn is not None:
                # 主连接存在但线程本地连接不存在（首次在此线程调用）
                # 回退到主连接（兼容旧行为）
                return self._base_conn
            Database.connect()
        if self._db_path is None:
            raise RuntimeError("Database not connected and db_path unknown")

        if getattr(self, "_readonly", False):
            uri = f"file:{Path(self._db_path).resolve().as_posix()}?mode=ro"
            conn = sqlite3.connect(
                uri, uri=True, check_same_thread=False, timeout=30.0
            )
            self._configure_connection(conn, readonly=True)
        else:
            conn = sqlite3.connect(
                self._db_path, check_same_thread=False, timeout=30.0
            )
            self._configure_connection(conn, readonly=False)
        self._local.conn = conn
        return conn

    def close(self):
        # 关闭当前线程的本地连接
        local_conn = getattr(self._local, 'conn', None)
        if local_conn:
            try:
                local_conn.close()
            except Exception:
                pass
            self._local.conn = None
        # 关闭主连接
        if self._base_conn:
            try:
                self._base_conn.close()
            except Exception:
                pass
            self._base_conn = None
        # Note: _shutdown is NOT reset here — it is reset on the next connect()
        # call (via lifespan startup), preventing silent reconnects during shutdown.

    def transaction(self):
        """返回一个事务上下文管理器，用于包裹多步写操作。

        使用当前线程的本地连接，确保事务内的所有操作在同一连接上执行。
        """
        import contextlib
        @contextlib.contextmanager
        def _tx():
            conn = self.get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _tx()

    # ---- Knowledge Items ----

    def insert_knowledge(self, item: dict) -> str:
        with self._write_lock:
            conn = self.get_conn()
            conn.execute(
                """INSERT INTO knowledge_items
                   (id, title, content, source_type, source_path, file_type, file_size, content_hash, file_created_at, file_modified_at, tags, version, created_at, updated_at)
                   VALUES (:id, :title, :content, :source_type, :source_path, :file_type, :file_size, :content_hash, :file_created_at, :file_modified_at, :tags, :version, :created_at, :updated_at)""",
                item,
            )
            conn.commit()
        return str(item["id"])

    def get_knowledge(self, item_id: str, include_deleted: bool = False) -> Optional[dict]:
        """按 ID 查询知识条目。

        Args:
            item_id: 知识条目 ID
            include_deleted: 是否包含已软删除条目（默认过滤，Phase 4 / Sprint 3）
        """
        conn = self.get_conn()
        if include_deleted:
            row = conn.execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM knowledge_items WHERE id = ? AND deleted_at IS NULL",
                (item_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_knowledge_by_hash(self, content_hash: str, include_deleted: bool = False) -> Optional[dict]:
        """按内容哈希查重，返回第一条匹配记录"""
        conn = self.get_conn()
        clause = "AND deleted_at IS NULL" if not include_deleted else ""
        row = conn.execute(
            f"SELECT * FROM knowledge_items WHERE content_hash = ? {clause} LIMIT 1",
            (content_hash,),
        ).fetchone()
        return dict(row) if row else None

    def get_knowledge_batch(self, ids: list[str], include_deleted: bool = False) -> dict[str, dict]:
        """批量查询知识条目，返回 {id: row_dict}"""
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        clause = "AND deleted_at IS NULL" if not include_deleted else ""
        rows = self.get_conn().execute(
            f"SELECT * FROM knowledge_items WHERE id IN ({placeholders}) {clause}", ids
        ).fetchall()
        return {row["id"]: dict(row) for row in rows}

    def list_knowledge(self, tag: str | None = None, file_type: str | None = None,
                       quality: str | None = None,
                       sort_by: str = "updated_at", sort_order: str = "DESC",
                       limit: int = 100, offset: int = 0,
                       include_deleted: bool = False) -> list[dict]:
        """列出知识条目。默认过滤已软删除条目。"""
        conn = self.get_conn()
        conditions = []
        params = []
        if not include_deleted:
            conditions.append("deleted_at IS NULL")
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        if file_type:
            conditions.append("file_type = ?")
            params.append(file_type)
        if quality is not None:
            conditions.append("quality = ?")
            params.append(quality)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        valid_sorts = {"updated_at", "created_at", "title", "version"}
        sort_by = sort_by if sort_by in valid_sorts else "updated_at"
        sort_order = "DESC" if sort_order.upper() == "DESC" else "ASC"
        rows = conn.execute(
            f"SELECT * FROM knowledge_items{where} ORDER BY {sort_by} {sort_order} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    def search_knowledge(self, query: str, limit: int = 20, offset: int = 0,
                         include_deleted: bool = False) -> list[dict]:
        from src.utils.chinese_tokenizer import sanitize_fts_query, tokenize_mixed_query_terms
        conn = self.get_conn()
        deleted_clause = "" if include_deleted else " AND ki.deleted_at IS NULL"
        try:
            safe_query = sanitize_fts_query(query)
            if safe_query:
                fts_rows = conn.execute(
                    f"""SELECT ki.*, rank as fts_rank FROM knowledge_fts kf
                        JOIN knowledge_items ki ON ki.rowid = kf.rowid
                        WHERE knowledge_fts MATCH ?{deleted_clause}
                        ORDER BY fts_rank LIMIT ? OFFSET ?""",
                    (safe_query, limit, offset),
                ).fetchall()
                if fts_rows:
                    return [dict(r) for r in fts_rows]
            mixed_terms = tokenize_mixed_query_terms(query)
            mixed_query = sanitize_fts_query(" ".join(mixed_terms), is_tokenized=True)
            if mixed_query and mixed_query != safe_query:
                fts_rows = conn.execute(
                    f"""SELECT ki.*, rank as fts_rank FROM knowledge_fts kf
                        JOIN knowledge_items ki ON ki.rowid = kf.rowid
                        WHERE knowledge_fts MATCH ?{deleted_clause}
                        ORDER BY fts_rank LIMIT ? OFFSET ?""",
                    (mixed_query, limit, offset),
                ).fetchall()
                if fts_rows:
                    return [dict(r) for r in fts_rows]
        except sqlite3.OperationalError as e:
            logger.warning("FTS search failed, falling back to LIKE: %s", e)
        # 转义 LIKE 通配符，防止用户输入 % 或 _ 影响搜索行为
        escaped = query.replace('%', '\\%').replace('_', '\\_')
        deleted_clause2 = "" if include_deleted else " AND deleted_at IS NULL"
        try:
            rows = conn.execute(
                f"SELECT * FROM knowledge_items WHERE (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\'){deleted_clause2} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (f"%{escaped}%", f"%{escaped}%", limit, offset),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
            mixed_terms = tokenize_mixed_query_terms(query)
            like_terms = [t.replace('%', '\\%').replace('_', '\\_') for t in mixed_terms[:8]]
            if like_terms:
                conditions = " OR ".join(["title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\'"] * len(like_terms))
                params = []
                for term in like_terms:
                    params.extend([f"%{term}%", f"%{term}%"])
                rows = conn.execute(
                    f"SELECT * FROM knowledge_items WHERE ({conditions}){deleted_clause2} "
                    "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    params + [limit, offset],
                ).fetchall()
        except sqlite3.OperationalError as e:
            logger.error("LIKE fallback search failed: %s", e)
            return []
        return [dict(r) for r in rows]

    def get_all_classified_ids(self) -> set[str]:
        """返回所有已分类条目的 ID 集合"""
        rows = self.get_conn().execute(
            "SELECT DISTINCT knowledge_id FROM knowledge_categories"
        ).fetchall()
        return {row[0] for row in rows}

    def update_knowledge(self, item_id: str, **fields):
        if not fields:
            return
        allowed = {"title", "content", "source_type", "source_path", "file_type", "file_size", "content_hash", "file_created_at", "file_modified_at", "tags", "quality", "quality_score"}
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Invalid fields: {invalid}")
        with self._write_lock:
            conn = self.get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Phase 4: 默认过滤已软删除条目（不更新已删条目）
                old = self.get_knowledge(item_id, include_deleted=False)
                if not old:
                    raise ValueError(f"Knowledge item {item_id} not found or has been deleted")
                _version_fields = {"title", "content", "tags"}
                if _version_fields & set(fields):
                    self._save_version(item_id, old)
                sets = ", ".join(f"{k} = ?" for k in fields)
                values = list(fields.values()) + [datetime.now().isoformat(), item_id]
                cursor = conn.execute(
                    f"UPDATE knowledge_items SET {sets}, version = version + 1, updated_at = ? "
                    f"WHERE id = ? AND deleted_at IS NULL",
                    values,
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"Knowledge item {item_id} not found or has been deleted")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def soft_delete_knowledge(self, item_id: str, when: str | None = None) -> bool:
        """Phase 4 / Sprint 3：软删除 — 设置 deleted_at。

        Args:
            item_id: 知识条目 ID
            when: ISO 时间戳，缺省取当前时间

        Returns:
            True 如果条目存在并已标记为删除；False 如果条目不存在或已删除
        """
        when = when or datetime.now().isoformat()
        with self._write_lock:
            cursor = self.get_conn().execute(
                "UPDATE knowledge_items SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                (when, item_id),
            )
            self.get_conn().commit()
            return cursor.rowcount > 0

    def get_tag_vocab(self) -> list[str]:
        """获取知识库中所有去重标签词表"""
        conn = self.get_conn()
        rows = conn.execute(
            "SELECT DISTINCT tags FROM knowledge_items WHERE deleted_at IS NULL AND tags IS NOT NULL AND tags != '[]'"
        ).fetchall()
        vocab: set[str] = set()
        for row in rows:
            try:
                tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"]
                if isinstance(tags, list):
                    vocab.update(t for t in tags if isinstance(t, str) and t.strip())
            except (json.JSONDecodeError, TypeError):
                pass
        # 也从 tag_relations 表获取（如果存在）
        try:
            tag_rows = conn.execute(
                "SELECT DISTINCT parent_tag AS tag FROM tag_relations "
                "UNION SELECT DISTINCT child_tag AS tag FROM tag_relations"
            ).fetchall()
            for tr in tag_rows:
                if tr["tag"] and tr["tag"].strip():
                    vocab.add(tr["tag"].strip())
        except Exception:
            pass  # tag_relations 表可能不存在
        return sorted(vocab)

    def update_knowledge_tags(self, item_id: str, tags: list[str]):
        """更新知识条目标签（便捷方法，自动序列化为JSON）

        注：knowledge_au 触发器会自动同步 FTS，无需手动更新 knowledge_fts
        """
        tags_json = json.dumps(tags, ensure_ascii=False)
        self.update_knowledge(item_id, tags=tags_json)

    def restore_knowledge(self, item_id: str) -> bool:
        """Phase 4 / Sprint 3：恢复 — 清除 deleted_at。

        Returns:
            True 如果条目存在并已恢复（之前是软删状态）；False 如果条目不存在或未删
        """
        with self._write_lock:
            cursor = self.get_conn().execute(
                "UPDATE knowledge_items SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
                (item_id,),
            )
            self.get_conn().commit()
            return cursor.rowcount > 0

    def delete_knowledge(self, item_id: str, hard: bool = False):
        """删除知识条目。

        Args:
            item_id: 知识条目 ID
            hard: True=硬删（彻底删除所有关联数据），False=软删（设置 deleted_at）

        注意：调用方需自行负责向量存储清理（VectorStore().delete_by_knowledge），
        以避免 db ↔ vectorstore 循环导入。
        """
        if not hard:
            # Phase 4: 软删除是默认行为
            self.soft_delete_knowledge(item_id)
            return
        with self._write_lock:
            conn = self.get_conn()
            self._delete_chunks_fts_unlocked(item_id)
            conn.execute(
                "DELETE FROM block_property_index WHERE block_id IN (SELECT id FROM blocks WHERE page_id = ?)",
                (item_id,),
            )
            conn.execute(
                "DELETE FROM block_refs WHERE source_id IN (SELECT id FROM blocks WHERE page_id = ?) OR target_id IN (SELECT id FROM blocks WHERE page_id = ?)",
                (item_id, item_id),
            )
            conn.execute("DELETE FROM blocks WHERE page_id = ?", (item_id,))
            conn.execute(
                "DELETE FROM entity_refs WHERE (source_type = 'knowledge' AND source_id = ?) OR (target_type = 'knowledge' AND target_id = ?)",
                (item_id, item_id),
            )
            conn.execute("DELETE FROM knowledge_chunks WHERE knowledge_id = ?", (item_id,))
            conn.execute("DELETE FROM knowledge_versions WHERE knowledge_id = ?", (item_id,))
            conn.execute("DELETE FROM knowledge_graph_relations WHERE source_knowledge_id = ? OR target_knowledge_id = ?", (item_id, item_id))
            conn.execute("DELETE FROM knowledge_graph_nodes WHERE knowledge_id = ?", (item_id,))
            conn.execute("DELETE FROM knowledge_items WHERE id = ?", (item_id,))
            conn.commit()

    def purge_knowledge(self, item_id: str) -> bool:
        """Phase 4: 硬删 — 彻底删除条目及其所有关联数据。

        Returns:
            True 如果条目存在并被删除；False 如果条目不存在
        """
        with self._write_lock:
            conn = self.get_conn()
            existing = conn.execute(
                "SELECT id FROM knowledge_items WHERE id = ?", (item_id,),
            ).fetchone()
            if not existing:
                return False
            self._delete_chunks_fts_unlocked(item_id)
            conn.execute(
                "DELETE FROM block_property_index WHERE block_id IN (SELECT id FROM blocks WHERE page_id = ?)",
                (item_id,),
            )
            conn.execute(
                "DELETE FROM block_refs WHERE source_id IN (SELECT id FROM blocks WHERE page_id = ?) OR target_id IN (SELECT id FROM blocks WHERE page_id = ?)",
                (item_id, item_id),
            )
            conn.execute("DELETE FROM blocks WHERE page_id = ?", (item_id,))
            conn.execute(
                "DELETE FROM entity_refs WHERE (source_type = 'knowledge' AND source_id = ?) OR (target_type = 'knowledge' AND target_id = ?)",
                (item_id, item_id),
            )
            conn.execute("DELETE FROM knowledge_chunks WHERE knowledge_id = ?", (item_id,))
            conn.execute("DELETE FROM knowledge_versions WHERE knowledge_id = ?", (item_id,))
            conn.execute("DELETE FROM knowledge_graph_relations WHERE source_knowledge_id = ? OR target_knowledge_id = ?", (item_id, item_id))
            conn.execute("DELETE FROM knowledge_graph_nodes WHERE knowledge_id = ?", (item_id,))
            conn.execute("DELETE FROM knowledge_items WHERE id = ?", (item_id,))
            conn.commit()
            return True

    def find_duplicates(self) -> list[list[dict]]:
        """查找重复条目组，三层策略：
        1. content_hash 相同 → 内容完全一致（最可靠）
        2. 标准化标题相同 → 对 content_hash 为空的旧记录兜底
           （标题末尾的 --<hex> 后缀被剥掉后再比较）
        3. 同内容不同 hash → 对标准化标题相同 + content 相同的记录兜底
           （捕获旧版 sync_page 用 sha256(MD全文) 存 hash 的遗留问题）
        每组按 created_at 降序（最新在前），调用方保留首条、删除其余。
        """
        import hashlib
        import re
        conn = self.get_conn()
        rows = conn.execute(
            "SELECT id, title, content, source_path, content_hash, "
            "file_size, created_at, updated_at FROM knowledge_items "
            "WHERE deleted_at IS NULL"
        ).fetchall()

        # ---- 策略 1: content_hash 匹配 ----
        hash_groups: dict[str, list[dict]] = {}
        no_hash_rows: list[dict] = []
        has_hash_rows: list[dict] = []
        for row in rows:
            ch = (row["content_hash"] or "").strip()
            d = dict(row)
            if ch:
                hash_groups.setdefault(ch, []).append(d)
                has_hash_rows.append(d)
            else:
                no_hash_rows.append(d)

        # ---- 策略 2: 标准化标题匹配（仅对无 hash 的旧记录兜底） ----
        _suffix_re = re.compile(r"--[0-9a-fA-F]{6,16}$")
        title_groups: dict[str, list[dict]] = {}
        for d in no_hash_rows:
            raw_title = (d.get("title") or "").strip()
            norm = _suffix_re.sub("", raw_title).strip()
            if not norm:
                continue
            title_groups.setdefault(norm, []).append(d)

        # ---- 策略 3: 同内容不同 hash 兜底 ----
        # 收集策略 1 中未命中重复的"孤立"记录（hash 组内只有 1 条）
        orphan_by_hash: dict[str, dict] = {}
        repeated_hashes: set[str] = set()
        for ch, g in hash_groups.items():
            if len(g) > 1:
                repeated_hashes.add(ch)
            else:
                orphan_by_hash[ch] = g[0]

        # 对孤立的 hash 记录，按 (标准化标题, sha256(content)) 分组
        # 如果两条孤立的 hash 记录标准化标题相同且 content 相同，则判定为重复
        content_dedup_groups: dict[str, list[dict]] = {}
        for d in has_hash_rows:
            ch = (d.get("content_hash") or "").strip()
            if ch in repeated_hashes:
                continue  # 已在策略 1 中处理
            raw_title = (d.get("title") or "").strip()
            norm = _suffix_re.sub("", raw_title).strip()
            if not norm:
                continue
            c = d.get("content") or ""
            if not c:
                continue
            content_h = hashlib.sha256(
                c.encode("utf-8", errors="surrogatepass")
            ).hexdigest()
            key = f"{norm}|{content_h}"
            content_dedup_groups.setdefault(key, []).append(d)

        # ---- 合并三组结果 ----
        result: list[list[dict]] = []
        for g in hash_groups.values():
            if len(g) > 1:
                result.append(sorted(g, key=lambda x: x.get("created_at", ""), reverse=True))
        for g in title_groups.values():
            if len(g) > 1:
                result.append(sorted(g, key=lambda x: x.get("created_at", ""), reverse=True))
        for g in content_dedup_groups.values():
            if len(g) > 1:
                result.append(sorted(g, key=lambda x: x.get("created_at", ""), reverse=True))
        return result

    def backfill_content_hash(self, force: bool = False) -> int:
        """为历史记录补算/修复 content_hash。

        force=False（默认）：仅回填 content_hash 为空的记录。
        force=True：强制覆盖所有未删除记录的 content_hash（用 sha256(content) 重算），
        用于修复旧版 sync_page 用 sha256(MD全文) 存 hash 导致互不相同的遗留问题。
        返回回填条数。
        """
        import hashlib
        conn = self.get_conn()
        if force:
            rows = conn.execute(
                "SELECT id, content FROM knowledge_items WHERE deleted_at IS NULL"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, content FROM knowledge_items "
                "WHERE (content_hash IS NULL OR content_hash = '') AND deleted_at IS NULL"
            ).fetchall()
        if not rows:
            return 0
        count = 0
        for row in rows:
            content = row["content"] or ""
            if not content:
                continue
            h = hashlib.sha256(content.encode("utf-8", errors="surrogatepass")).hexdigest()
            conn.execute(
                "UPDATE knowledge_items SET content_hash = ? WHERE id = ?",
                (h, row["id"]),
            )
            count += 1
        conn.commit()
        return count

    def count_knowledge(
        self,
        tag: str | None = None,
        file_type: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        conditions = []
        params = []
        if not include_deleted:
            conditions.append("deleted_at IS NULL")
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        if file_type:
            conditions.append("file_type = ?")
            params.append(file_type)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        row = self.get_conn().execute(
            f"SELECT COUNT(*) as cnt FROM knowledge_items{where}",
            params,
        ).fetchone()
        return int(row["cnt"])

    def get_stats(self) -> dict:
        """返回知识库统计汇总：文件数、存储占用、类型分布、分类覆盖（默认排除软删）。"""
        conn = self.get_conn()
        total_files = conn.execute(
            "SELECT COUNT(*) as cnt FROM knowledge_items WHERE deleted_at IS NULL"
        ).fetchone()["cnt"]
        total_size = conn.execute(
            "SELECT COALESCE(SUM(file_size), 0) as sz FROM knowledge_items WHERE deleted_at IS NULL"
        ).fetchone()["sz"]

        # 文件类型分布
        type_rows = conn.execute(
            "SELECT file_type, COUNT(*) as cnt FROM knowledge_items "
            "WHERE deleted_at IS NULL GROUP BY file_type ORDER BY cnt DESC"
        ).fetchall()
        file_type_dist = {r["file_type"] or "other": r["cnt"] for r in type_rows}

        # 分类覆盖数（有至少一个条目的分类）
        cat_count = 0
        try:
            cat_rows = conn.execute(
                "SELECT DISTINCT category_id FROM knowledge_categories"
            ).fetchall()
            cat_count = len(cat_rows)
        except Exception:
            pass

        return {
            "total_files": total_files,
            "total_size": total_size,
            "file_type_dist": file_type_dist,
            "category_coverage": cat_count,
        }

    # ---- Version Control ----

    def _save_version(self, knowledge_id: str, snapshot: dict):
        version = snapshot.get("version", 1)
        self.get_conn().execute(
            """INSERT INTO knowledge_versions (id, knowledge_id, version, title, content, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), knowledge_id, version, snapshot["title"],
             snapshot.get("content", ""), snapshot.get("tags", "[]"), datetime.now().isoformat()),
        )
        self.get_conn().commit()

    def list_versions(self, knowledge_id: str) -> list[dict]:
        rows = self.get_conn().execute(
            "SELECT * FROM knowledge_versions WHERE knowledge_id = ? ORDER BY version DESC",
            (knowledge_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_version(self, knowledge_id: str, version: int) -> Optional[dict]:
        row = self.get_conn().execute(
            "SELECT * FROM knowledge_versions WHERE knowledge_id = ? AND version = ?",
            (knowledge_id, version),
        ).fetchone()
        return dict(row) if row else None

    def restore_version(self, knowledge_id: str, version: int):
        with self._write_lock:
            ver = self.get_version(knowledge_id, version)
            if not ver:
                raise ValueError(f"版本 {version} 不存在")
            old = self.get_knowledge(knowledge_id)
            if old:
                # 使用 MAX(version)+1 确保版本号严格递增，避免重复
                row = self.get_conn().execute(
                    "SELECT MAX(version) as max_ver FROM knowledge_versions WHERE knowledge_id = ?",
                    (knowledge_id,),
                ).fetchone()
                next_ver = (row["max_ver"] or 0) + 1
                old["version"] = next_ver
                self._save_version(knowledge_id, old)
            self.get_conn().execute(
                "UPDATE knowledge_items SET title = ?, content = ?, tags = ?, version = version + 1, updated_at = ? WHERE id = ?",
                (ver["title"], ver["content"], ver["tags"], datetime.now().isoformat(), knowledge_id),
            )
            self.get_conn().commit()

    # ---- Knowledge Chunks ----

    def insert_chunks(self, chunks: list[dict]):
        conn = self.get_conn()
        conn.executemany(
            """INSERT INTO knowledge_chunks (id, knowledge_id, chunk_index, chunk_text, created_at)
               VALUES (:id, :knowledge_id, :chunk_index, :chunk_text, :created_at)""",
            chunks,
        )
        self._upsert_blocks_from_chunks_unlocked(chunks)
        conn.commit()

    def _upsert_blocks_from_chunks_unlocked(self, chunks: list[dict]):
        if not chunks:
            return
        now = datetime.now().isoformat()
        block_rows = []
        prop_rows = []
        for chunk in chunks:
            created_at = chunk.get("created_at") or now
            block_rows.append({
                "id": chunk["id"],
                "parent_id": None,
                "page_id": chunk["knowledge_id"],
                "content": chunk.get("chunk_text", ""),
                "block_type": "text",
                "properties": json.dumps({
                    "knowledge_id": chunk["knowledge_id"],
                    "chunk_index": chunk.get("chunk_index", 0),
                }, ensure_ascii=False),
                "order_idx": chunk.get("chunk_index", 0),
                "created_at": created_at,
                "updated_at": created_at,
            })
            prop_rows.append({
                "block_id": chunk["id"],
                "prop_key": "knowledge_id",
                "prop_value": chunk["knowledge_id"],
                "value_type": "ref",
            })
        conn = self.get_conn()
        conn.executemany(
            """INSERT OR REPLACE INTO blocks
               (id, parent_id, page_id, content, block_type, properties, order_idx, created_at, updated_at)
               VALUES (:id, :parent_id, :page_id, :content, :block_type, :properties, :order_idx, :created_at, :updated_at)""",
            block_rows,
        )
        conn.executemany(
            """INSERT OR REPLACE INTO block_property_index
               (block_id, prop_key, prop_value, value_type)
               VALUES (:block_id, :prop_key, :prop_value, :value_type)""",
            prop_rows,
        )

    def delete_chunks(self, knowledge_id: str):
        """删除指定知识的所有 chunk 行（knowledge_chunks 表）。

        仅删除 knowledge_chunks 表的行，不涉及 chunk_fts 和向量存储。
        调用方需自行负责 VectorStore 和 chunk_fts 的清理。
        """
        with self._write_lock:
            conn = self.get_conn()
            conn.execute(
                "DELETE FROM block_property_index WHERE block_id IN (SELECT id FROM blocks WHERE page_id = ?)",
                (knowledge_id,),
            )
            conn.execute(
                "DELETE FROM block_refs WHERE source_id IN (SELECT id FROM blocks WHERE page_id = ?) OR target_id IN (SELECT id FROM blocks WHERE page_id = ?)",
                (knowledge_id, knowledge_id),
            )
            conn.execute("DELETE FROM blocks WHERE page_id = ?", (knowledge_id,))
            conn.execute("DELETE FROM knowledge_chunks WHERE knowledge_id = ?", (knowledge_id,))
            conn.commit()

    def get_chunks_by_knowledge(self, knowledge_id: str) -> list[dict]:
        rows = self.get_conn().execute(
            "SELECT * FROM knowledge_chunks WHERE knowledge_id = ? ORDER BY chunk_index",
            (knowledge_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_chunks_by_knowledge_batch(
        self, knowledge_ids: list[str]
    ) -> dict[str, list[dict]]:
        """批量查询多个 knowledge_id 的 chunks — 单次 SQL 替代 N+1。

        返回 ``{knowledge_id: [chunk, ...]}``，按 ``chunk_index`` 升序排列。
        """
        if not knowledge_ids:
            return {}
        placeholders = ",".join("?" for _ in knowledge_ids)
        rows = self.get_conn().execute(
            f"""SELECT * FROM knowledge_chunks
                WHERE knowledge_id IN ({placeholders})
                ORDER BY knowledge_id, chunk_index""",
            list(knowledge_ids),
        ).fetchall()
        result: dict[str, list[dict]] = {kid: [] for kid in knowledge_ids}
        for r in rows:
            result.setdefault(r["knowledge_id"], []).append(dict(r))
        return result

    def get_chunk(self, chunk_id: str) -> Optional[dict]:
        row = self.get_conn().execute("SELECT * FROM knowledge_chunks WHERE id = ?", (chunk_id,)).fetchone()
        return dict(row) if row else None

    # ---- Block-level methods (Block-First architecture) ----

    def insert_blocks(self, blocks: list[dict]):
        """写入 blocks 表 + block_property_index（原子事务）"""
        with self._write_lock:
            conn = self.get_conn()
            conn.executemany(
                """INSERT OR REPLACE INTO blocks
                   (id, parent_id, page_id, content, block_type, properties, order_idx, created_at, updated_at)
                   VALUES (:id, :parent_id, :page_id, :content, :block_type, :properties, :order_idx, :created_at, :updated_at)""",
                blocks,
            )
            prop_rows = []
            for block in blocks:
                try:
                    props = json.loads(block.get("properties", "{}"))
                except (json.JSONDecodeError, TypeError):
                    props = {}
                for key, value in props.items():
                    prop_rows.append({
                        "block_id": block["id"],
                        "prop_key": key,
                        "prop_value": str(value),
                        "value_type": "ref" if key == "knowledge_id" else "str",
                    })
            if prop_rows:
                conn.executemany(
                    """INSERT OR REPLACE INTO block_property_index
                       (block_id, prop_key, prop_value, value_type)
                       VALUES (:block_id, :prop_key, :prop_value, :value_type)""",
                    prop_rows,
                )
            conn.commit()

    def insert_blocks_fts(self, blocks: list[dict]):
        """将 block 文本用 jieba 全模式分词后写入 block_fts"""
        from src.utils.chinese_tokenizer import tokenize_chinese_full
        conn = self.get_conn()
        for b in blocks:
            segmented = tokenize_chinese_full(b.get("content", ""))
            conn.execute(
                "INSERT INTO block_fts(fts_segmented, page_id, block_id) VALUES (?, ?, ?)",
                (segmented, b["page_id"], b["id"]),
            )
        conn.commit()

    def search_blocks_fts(self, query: str, limit: int = 10) -> list[dict]:
        """Block 级 FTS 搜索"""
        from src.utils.chinese_tokenizer import (
            sanitize_fts_query,
            tokenize_chinese_full,
            tokenize_mixed_query_terms,
        )
        sanitized = tokenize_chinese_full(query)
        if not sanitized.strip():
            return []
        safe_query = sanitize_fts_query(sanitized, is_tokenized=True)
        if not safe_query:
            return []
        conn = self.get_conn()
        # BUG#13 修复：过滤软删条目的 block，避免"未知"孤儿泄漏。
        # LEFT JOIN：无父级记录的 block（历史孤儿）仍可搜，仅排除父级已软删的。
        rows = conn.execute(
            """SELECT b.id, b.page_id, b.content, b.block_type, b.properties,
                      bf.rank
               FROM block_fts bf
               JOIN blocks b ON b.id = bf.block_id
               LEFT JOIN knowledge_items ki ON ki.id = b.page_id
               WHERE block_fts MATCH ?
                 AND (ki.id IS NULL OR ki.deleted_at IS NULL)
               ORDER BY bf.rank
               LIMIT ?""",
            (safe_query, limit),
        ).fetchall()
        results = []
        seen_ids = set()
        for r in rows:
            try:
                properties = json.loads(r[4]) if r[4] else {}
            except (json.JSONDecodeError, TypeError):
                properties = {}
            results.append({
                "id": r[0],
                "page_id": r[1],
                "content": r[2],
                "block_type": r[3],
                "properties": properties,
                "fts_rank": r[5],
            })
            seen_ids.add(r[0])

        # BUG-6 fix: 对 CJK+ASCII 混合查询补充 mixed-terms 搜索
        if len(results) < limit:
            mixed_terms = tokenize_mixed_query_terms(query)
            if mixed_terms:
                mixed_query = sanitize_fts_query(" ".join(mixed_terms), is_tokenized=True)
                if mixed_query and mixed_query != safe_query:
                    try:
                        extra_rows = conn.execute(
                            """SELECT b.id, b.page_id, b.content, b.block_type, b.properties,
                                      bf.rank
                               FROM block_fts bf
                               JOIN blocks b ON b.id = bf.block_id
                               LEFT JOIN knowledge_items ki ON ki.id = b.page_id
                               WHERE block_fts MATCH ?
                                 AND (ki.id IS NULL OR ki.deleted_at IS NULL)
                               ORDER BY bf.rank
                               LIMIT ?""",
                            (mixed_query, limit),
                        ).fetchall()
                        for r in extra_rows:
                            if r[0] in seen_ids:
                                continue
                            seen_ids.add(r[0])
                            try:
                                properties = json.loads(r[4]) if r[4] else {}
                            except (json.JSONDecodeError, TypeError):
                                properties = {}
                            results.append({
                                "id": r[0],
                                "page_id": r[1],
                                "content": r[2],
                                "block_type": r[3],
                                "properties": properties,
                                "fts_rank": r[5],
                            })
                            if len(results) >= limit:
                                break
                    except Exception:
                        pass

        return results[:limit]

    def delete_blocks_fts(self, page_id: str):
        """删除指定 page 的 block FTS 记录"""
        with self._write_lock:
            self.get_conn().execute(
                "DELETE FROM block_fts WHERE page_id = ?", (page_id,)
            )
            self.get_conn().commit()

    def get_block(self, block_id: str) -> dict | None:
        """按 ID 查询单个 block，返回 dict 或 None"""
        conn = self.get_conn()
        row = conn.execute(
            "SELECT id, parent_id, page_id, content, block_type, properties, order_idx FROM blocks WHERE id = ?",
            (block_id,),
        ).fetchone()
        if not row:
            return None
        cols = ["id", "parent_id", "page_id", "content", "block_type", "properties", "order_idx"]
        return dict(zip(cols, row))

    def get_block_ancestors(self, block_id: str, max_depth: int = 3) -> list[dict]:
        """回溯 Block 的父链，返回从父到祖先的有序列表（不含自身）

        用于 RAG 检索时补充上下文。例如命中 Excel 某行的属性子 Block 时，
        回溯到行 Block 和表头信息。
        """
        ancestors = []
        current_id = block_id
        for _ in range(max_depth):
            block = self.get_block(current_id)
            if not block or not block.get("parent_id"):
                break
            parent = self.get_block(block["parent_id"])
            if parent:
                ancestors.append(parent)
                current_id = parent["id"]
            else:
                break
        return ancestors

    def get_block_ancestors_batch(
        self, block_ids: list[str], max_depth: int = 3
    ) -> dict[str, list[dict]]:
        """批量回溯多个 Block 的父链 — 单次递归 CTE，避免 N+1 查询。

        返回 ``{block_id: [ancestor1, ancestor2, ...]}``，每个 list 从直接父到
        最远祖先排序。block_ids 中不存在的 ID 不会出现在返回字典中。
        """
        if not block_ids:
            return {}
        conn = self.get_conn()
        depth = max(1, int(max_depth or 3))
        # 用 UNION ALL 的递归 CTE 一次性遍历所有节点的父链。``path`` 字段用 ','
        # 连接沿途 id 避免循环引用导致无限递归。``root_id`` 标记每个 block
        # 所属的查询起点，多起点共享一次遍历。
        placeholders = ",".join("?" for _ in block_ids)
        rows = conn.execute(
            f"""
            WITH RECURSIVE chain(root_id, id, parent_id, page_id, content,
                                 block_type, properties, order_idx, depth, path) AS (
                SELECT b.id, b.id, b.parent_id, b.page_id, b.content,
                       b.block_type, b.properties, b.order_idx, 0, ',' || b.id || ','
                FROM blocks b
                WHERE b.id IN ({placeholders})
                UNION ALL
                SELECT c.root_id, p.id, p.parent_id, p.page_id, p.content,
                       p.block_type, p.properties, p.order_idx, c.depth + 1,
                       c.path || p.id || ','
                FROM blocks p
                JOIN chain c ON p.id = c.parent_id
                WHERE c.depth < ? AND instr(c.path, ',' || p.id || ',') = 0
            )
            SELECT root_id, id, parent_id, page_id, content,
                   block_type, properties, order_idx, depth
            FROM chain
            WHERE depth > 0
            ORDER BY root_id, depth
            """,
            [*block_ids, depth],
        ).fetchall()

        result: dict[str, list[dict]] = {bid: [] for bid in block_ids}
        cols = ["id", "parent_id", "page_id", "content",
                "block_type", "properties", "order_idx"]
        for r in rows:
            result.setdefault(r["root_id"], []).append(dict(zip(cols, [
                r["id"], r["parent_id"], r["page_id"], r["content"],
                r["block_type"], r["properties"], r["order_idx"],
            ])))
        return result

    def delete_blocks_by_page(self, page_id: str):
        """删除指定 page 的所有 block 数据（blocks + block_fts + block_property_index + block_refs）"""
        with self._write_lock:
            conn = self.get_conn()
            conn.execute(
                "DELETE FROM block_property_index WHERE block_id IN (SELECT id FROM blocks WHERE page_id = ?)",
                (page_id,),
            )
            conn.execute(
                "DELETE FROM block_refs WHERE source_id IN (SELECT id FROM blocks WHERE page_id = ?) OR target_id IN (SELECT id FROM blocks WHERE page_id = ?)",
                (page_id, page_id),
            )
            conn.execute("DELETE FROM block_fts WHERE page_id = ?", (page_id,))
            conn.execute("DELETE FROM blocks WHERE page_id = ?", (page_id,))
            conn.commit()

    # ---- Chunk FTS (jieba 分词) ----

    def insert_chunks_fts(self, chunks: list[dict]):
        """将 chunk 文本用 jieba 全模式分词后写入 chunk_fts（独立表）"""
        from src.utils.chinese_tokenizer import tokenize_chinese_full
        conn = self.get_conn()
        for c in chunks:
            segmented = tokenize_chinese_full(c["chunk_text"])
            conn.execute(
                "INSERT INTO chunk_fts(fts_segmented, knowledge_id, chunk_id) VALUES (?, ?, ?)",
                (segmented, c["knowledge_id"], c["id"]),
            )
        conn.commit()

    def delete_chunks_fts(self, knowledge_id: str):
        """删除指定知识的 chunk FTS 记录"""
        with self._write_lock:
            self._delete_chunks_fts_unlocked(knowledge_id)

    def _delete_chunks_fts_unlocked(self, knowledge_id: str):
        """内部方法：删除 chunk FTS 记录（调用方需持锁）"""
        self.get_conn().execute(
            "DELETE FROM chunk_fts WHERE knowledge_id = ?", (knowledge_id,)
        )
        self.get_conn().commit()

    def search_chunks_fts(self, query: str, limit: int = 20) -> list[dict]:
        """使用 jieba 全模式分词后的 chunk 级 FTS 搜索"""
        from src.utils.chinese_tokenizer import (
            sanitize_fts_query,
            tokenize_chinese_full,
            tokenize_mixed_query_terms,
        )
        tokenized_query = tokenize_chinese_full(query)
        if not tokenized_query.strip():
            return []
        safe_query = sanitize_fts_query(tokenized_query, is_tokenized=True)
        if not safe_query:
            return []
        conn = self.get_conn()

        def _hydrate_chunk_rows(rows) -> list[dict]:
            hydrated = []
            for r in rows:
                chunk = conn.execute(
                    "SELECT id, knowledge_id, chunk_index, chunk_text FROM knowledge_chunks WHERE id = ?",
                    (r["chunk_id"],),
                ).fetchone()
                if chunk:
                    hydrated.append(dict(chunk) | {"fts_rank": r["fts_rank"]})
            return hydrated

        try:
            # BUG#13 修复：LEFT JOIN knowledge_items 过滤软删条目（保留历史孤儿 chunk）
            rows = conn.execute(
                """SELECT cf.chunk_id, cf.knowledge_id, rank as fts_rank
                   FROM chunk_fts cf
                   JOIN knowledge_chunks kc ON kc.id = cf.chunk_id
                   LEFT JOIN knowledge_items ki ON ki.id = kc.knowledge_id
                   WHERE chunk_fts MATCH ?
                     AND (ki.id IS NULL OR ki.deleted_at IS NULL)
                   ORDER BY fts_rank LIMIT ?""",
                (safe_query, limit),
            ).fetchall()
            results = _hydrate_chunk_rows(rows)
            seen_ids = {r["id"] for r in results}

            if len(results) < limit:
                mixed_terms = tokenize_mixed_query_terms(query)
                mixed_query = sanitize_fts_query(" ".join(mixed_terms), is_tokenized=True)
                if mixed_query and mixed_query != safe_query:
                    extra_rows = conn.execute(
                        """SELECT cf.chunk_id, cf.knowledge_id, rank as fts_rank
                           FROM chunk_fts cf
                           JOIN knowledge_chunks kc ON kc.id = cf.chunk_id
                           LEFT JOIN knowledge_items ki ON ki.id = kc.knowledge_id
                           WHERE chunk_fts MATCH ?
                             AND (ki.id IS NULL OR ki.deleted_at IS NULL)
                           ORDER BY fts_rank LIMIT ?""",
                        (mixed_query, limit),
                    ).fetchall()
                    for chunk in _hydrate_chunk_rows(extra_rows):
                        if chunk["id"] in seen_ids:
                            continue
                        seen_ids.add(chunk["id"])
                        results.append(chunk)
                        if len(results) >= limit:
                            break
            return results[:limit]
        except Exception:
            return []

    # ---- Conversations ----

    def insert_conversation(self, conv: dict) -> str:
        self.get_conn().execute(
            "INSERT INTO conversations (id, title, created_at) VALUES (:id, :title, :created_at)",
            conv,
        )
        self.get_conn().commit()
        return str(conv["id"])

    def list_conversations(self, limit: int = 50) -> list[dict]:
        rows = self.get_conn().execute(
            "SELECT * FROM conversations ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, conv_id: str):
        conn = self.get_conn()
        conn.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        conn.commit()

    # ---- Chat Messages ----

    def insert_message(self, msg: dict) -> str:
        msg = {**msg}
        msg.setdefault("source_graph", json.dumps({"nodes": [], "edges": []}, ensure_ascii=False))
        self.get_conn().execute(
            """INSERT INTO chat_messages (id, conversation_id, role, content, sources, source_graph, created_at)
               VALUES (:id, :conversation_id, :role, :content, :sources, :source_graph, :created_at)""",
            msg,
        )
        self.get_conn().commit()
        return str(msg["id"])

    def get_messages(self, conversation_id: str) -> list[dict]:
        rows = self.get_conn().execute(
            "SELECT * FROM chat_messages WHERE conversation_id = ? ORDER BY created_at",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Tags ----

    def get_all_tags(self) -> list[str]:
        rows = self.get_conn().execute("SELECT tags FROM knowledge_items WHERE tags IS NOT NULL").fetchall()
        tags_set = set()
        for row in rows:
            try:
                tags = json.loads(row["tags"])
                tags_set.update(tags)
            except (json.JSONDecodeError, TypeError):
                pass
        return sorted(tags_set)

    def get_all_file_types(self) -> list[str]:
        """返回知识库中所有已使用的文件类型"""
        rows = self.get_conn().execute(
            "SELECT DISTINCT file_type FROM knowledge_items WHERE file_type IS NOT NULL AND file_type != '' ORDER BY file_type"
        ).fetchall()
        return [row["file_type"] for row in rows]

    # ---- Categories ----

    def insert_category(self, cat_id: str, name: str, description: str = "", parent_id: str | None = None) -> str:
        self.get_conn().execute(
            "INSERT INTO categories (id, name, description, parent_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (cat_id, name, description, parent_id, datetime.now().isoformat()),
        )
        self.get_conn().commit()
        return cat_id

    def get_all_categories(self) -> list[dict]:
        rows = self.get_conn().execute("SELECT * FROM categories ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def delete_category(self, cat_id: str):
        conn = self.get_conn()
        conn.execute("DELETE FROM knowledge_categories WHERE category_id = ?", (cat_id,))
        conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        conn.commit()

    def clear_categories(self, keep_dynamic=False):
        conn = self.get_conn()
        if keep_dynamic:
            # 只删除预设分类（名称以 schema code 开头的），保留动态分类及其关联
            from src.data.classification_schema import get_all_codes
            schema_codes = get_all_codes()
            for code in schema_codes:
                # 找到要删除的预设分类 ID，同时清除其关联
                rows = conn.execute(
                    "SELECT id FROM categories WHERE name LIKE ? OR name = ?",
                    (f"{code} %", f"{code}"),
                ).fetchall()
                for row in rows:
                    conn.execute("DELETE FROM knowledge_categories WHERE category_id = ?", (row["id"],))
                conn.execute("DELETE FROM categories WHERE name LIKE ?", (f"{code} %",))
                conn.execute("DELETE FROM categories WHERE name = ?", (f"{code}",))
        else:
            conn.execute("DELETE FROM knowledge_categories")
            conn.execute("DELETE FROM categories")
        conn.commit()

    def assign_category(self, knowledge_id: str, category_id: str):
        self.get_conn().execute(
            "INSERT OR IGNORE INTO knowledge_categories (knowledge_id, category_id) VALUES (?, ?)",
            (knowledge_id, category_id),
        )
        self.get_conn().commit()

    def unassign_category(self, knowledge_id: str, category_id: str):
        self.get_conn().execute(
            "DELETE FROM knowledge_categories WHERE knowledge_id = ? AND category_id = ?",
            (knowledge_id, category_id),
        )
        self.get_conn().commit()

    def get_knowledge_by_category(self, category_id: str) -> list[dict]:
        rows = self.get_conn().execute(
            """SELECT ki.* FROM knowledge_items ki
               JOIN knowledge_categories kc ON kc.knowledge_id = ki.id
               WHERE kc.category_id = ? ORDER BY ki.title""",
            (category_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_categories_for_knowledge(self, knowledge_id: str) -> list[dict]:
        rows = self.get_conn().execute(
            """SELECT c.* FROM categories c
               JOIN knowledge_categories kc ON kc.category_id = c.id
               WHERE kc.knowledge_id = ?""",
            (knowledge_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Wiki Pages ----

    def insert_wiki_page(self, page: dict) -> str:
        # 自动补齐 complex_anomaly 默认值，避免旧调用路径缺参报错
        page.setdefault("complex_anomaly", "")
        conn = self.get_conn()
        conn.execute(
            """INSERT INTO wiki_pages
               (id, title, content, source_ids, tags, concept_summary, status, lint_score, complex_anomaly, created_at, updated_at)
               VALUES (:id, :title, :content, :source_ids, :tags, :concept_summary, :status, :lint_score, :complex_anomaly, :created_at, :updated_at)""",
            page,
        )
        conn.commit()
        return str(page["id"])

    def get_wiki_page(self, page_id: str) -> Optional[dict]:
        row = self.get_conn().execute("SELECT * FROM wiki_pages WHERE id = ?", (page_id,)).fetchone()
        return dict(row) if row else None

    def get_wiki_page_by_title(self, title: str) -> Optional[dict]:
        row = self.get_conn().execute("SELECT * FROM wiki_pages WHERE title = ?", (title,)).fetchone()
        return dict(row) if row else None

    def update_wiki_page(self, page_id: str, **fields):
        if not fields:
            return
        allowed = {"title", "content", "source_ids", "tags", "concept_summary", "status", "lint_score", "complex_anomaly"}
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Invalid fields: {invalid}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [datetime.now().isoformat(), page_id]
        self.get_conn().execute(
            f"UPDATE wiki_pages SET {sets}, updated_at = ? WHERE id = ?",
            values,
        )
        self.get_conn().commit()

    def delete_wiki_page(self, page_id: str):
        conn = self.get_conn()
        conn.execute(
            "UPDATE wiki_pages SET status = 'deleted', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), page_id),
        )
        conn.commit()

    def purge_wiki_page(self, page_id: str):
        conn = self.get_conn()
        conn.execute("DELETE FROM wiki_links WHERE source_page_id = ? OR target_page_id = ?", (page_id, page_id))
        conn.execute("DELETE FROM wiki_pages WHERE id = ?", (page_id,))
        conn.commit()

    def restore_wiki_page(self, page_id: str, status: str = "draft"):
        conn = self.get_conn()
        conn.execute(
            "UPDATE wiki_pages SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.now().isoformat(), page_id),
        )
        conn.commit()

    def list_wiki_pages(self, status: str | None = None, search: str | None = None,
                        sort_by: str = "updated_at", sort_order: str = "DESC",
                        limit: int = 100, offset: int = 0) -> list[dict]:
        conn = self.get_conn()
        conditions = []
        params = []
        if status:
            if status == "active":
                status = "published"
            conditions.append("status = ?")
            params.append(status)
        else:
            conditions.append("status != ?")
            params.append("deleted")
        if search:
            conditions.append("title LIKE ? ESCAPE '\\'")
            escaped = search.replace('%', '\\%').replace('_', '\\_')
            params.append(f"%{escaped}%")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        valid_sorts = {"updated_at", "created_at", "title", "lint_score"}
        sort_by = sort_by if sort_by in valid_sorts else "updated_at"
        sort_order = "DESC" if sort_order.upper() == "DESC" else "ASC"
        rows = conn.execute(
            f"SELECT * FROM wiki_pages{where} ORDER BY {sort_by} {sort_order} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    def count_wiki_pages(self, status: str | None = None) -> int:
        if status:
            row = self.get_conn().execute("SELECT COUNT(*) as cnt FROM wiki_pages WHERE status = ?", (status,)).fetchone()
        else:
            row = self.get_conn().execute("SELECT COUNT(*) as cnt FROM wiki_pages").fetchone()
        return int(row["cnt"])

    def search_wiki_fts(self, query: str, limit: int = 10) -> list[dict]:
        from src.utils.chinese_tokenizer import sanitize_fts_query
        try:
            safe_query = sanitize_fts_query(query)
            if not safe_query:
                return []
            rows = self.get_conn().execute(
                """SELECT wp.*, rank as fts_rank FROM wiki_fts wf
                   JOIN wiki_pages wp ON wp.rowid = wf.rowid
                   WHERE wiki_fts MATCH ? AND wp.status = 'published'
                   ORDER BY fts_rank LIMIT ?""",
                (safe_query, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    # ---- Wiki Links ----

    def add_wiki_link(self, source_page_id: str, target_page_id: str,
                      link_type: str = "related", weight: float = 1.0):
        self.get_conn().execute(
            "INSERT OR REPLACE INTO wiki_links (source_page_id, target_page_id, link_type, weight) VALUES (?, ?, ?, ?)",
            (source_page_id, target_page_id, link_type, weight),
        )
        self.get_conn().commit()

    def remove_wiki_link(self, source_page_id: str, target_page_id: str):
        self.get_conn().execute(
            "DELETE FROM wiki_links WHERE source_page_id = ? AND target_page_id = ?",
            (source_page_id, target_page_id),
        )
        self.get_conn().commit()

    def get_links_for_page(self, page_id: str) -> list[dict]:
        rows = self.get_conn().execute(
            """SELECT wl.*, wp.title as target_title FROM wiki_links wl
               JOIN wiki_pages wp ON wp.id = wl.target_page_id
               WHERE wl.source_page_id = ?""",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_backlinks(self, page_id: str) -> list[dict]:
        rows = self.get_conn().execute(
            """SELECT wl.*, wp.title as source_title FROM wiki_links wl
               JOIN wiki_pages wp ON wp.id = wl.source_page_id
               WHERE wl.target_page_id = ?""",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_wiki_links(self) -> list[dict]:
        rows = self.get_conn().execute(
            """SELECT wl.*, sp.title as source_title, tp.title as target_title
               FROM wiki_links wl
               JOIN wiki_pages sp ON sp.id = wl.source_page_id
               JOIN wiki_pages tp ON tp.id = wl.target_page_id""",
        ).fetchall()
        return [dict(r) for r in rows]

    def get_dangling_wiki_links(self) -> list[dict]:
        """返回 wiki_links 中 source/target 物理上已不在 wiki_pages 的悬空记录。

        用于 broken_link 检查:只有真正物理悬空的链接才算损坏。
        status=deleted 的软删页面因物理仍存在,不被计为悬空。
        """
        rows = self.get_conn().execute(
            """SELECT wl.source_page_id, wl.target_page_id, wl.link_type, wl.weight,
                      sp.title as source_title, tp.title as target_title
               FROM wiki_links wl
               LEFT JOIN wiki_pages sp ON sp.id = wl.source_page_id
               LEFT JOIN wiki_pages tp ON tp.id = wl.target_page_id
               WHERE sp.id IS NULL OR tp.id IS NULL""",
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Wiki Ops Log ----

    def insert_wiki_op(self, op_type: str, target_id: str, detail: dict | None = None) -> str:
        op_id = str(uuid.uuid4())
        self.get_conn().execute(
            "INSERT INTO wiki_ops_log (id, op_type, target_id, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (op_id, op_type, target_id, json.dumps(detail or {}, ensure_ascii=False), datetime.now().isoformat()),
        )
        self.get_conn().commit()
        return op_id

    def list_wiki_ops(self, limit: int = 50) -> list[dict]:
        rows = self.get_conn().execute(
            "SELECT * FROM wiki_ops_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Async Jobs ----

    def create_job(self, job_type: str, params: dict | None = None, priority: int = 1, max_retries: int = 3) -> str:
        """创建新任务"""
        import uuid as _uuid
        job_id = str(_uuid.uuid4())
        now = datetime.now().isoformat()
        conn = self.get_conn()
        conn.execute(
            """INSERT INTO async_jobs
               (id, job_type, status, params, priority, max_retries, created_at)
               VALUES (?, ?, 'pending', ?, ?, ?, ?)""",
            (job_id, job_type, json.dumps(params or {}), priority, max_retries, now),
        )
        conn.commit()
        return job_id

    def get_job(self, job_id: str) -> Optional[dict]:
        """获取任务详情"""
        row = self.get_conn().execute("SELECT * FROM async_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["params"] = json.loads(result.get("params", "{}"))
        result["result"] = json.loads(result["result"]) if result.get("result") else None
        return result

    def list_jobs(self, status: str | None = None, job_type: str | None = None,
                  limit: int = 50, offset: int = 0) -> list[dict]:
        """列出任务"""
        conn = self.get_conn()
        conditions = []
        params = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if job_type:
            conditions.append("job_type = ?")
            params.append(job_type)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM async_jobs{where} ORDER BY priority DESC, created_at ASC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    def update_job_progress(self, job_id: str, progress: int, message: str = ""):
        """更新任务进度"""
        self.get_conn().execute(
            "UPDATE async_jobs SET progress = ?, progress_message = ? WHERE id = ?",
            (progress, message, job_id),
        )
        self.get_conn().commit()

    def update_job_status(self, job_id: str, status: str, result: dict | None = None, error: str = ""):
        """更新任务状态"""
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        now = datetime.now().isoformat()
        conn = self.get_conn()
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

    def claim_next_pending_job(self) -> Optional[dict]:
        """认领下一个待处理任务（原子操作）"""
        conn = self.get_conn()
        row = conn.execute(
            """UPDATE async_jobs
               SET status = 'running', started_at = ?
               WHERE id = (
                   SELECT id FROM async_jobs
                   WHERE status = 'pending'
                   ORDER BY priority DESC, created_at ASC
                   LIMIT 1
               )
               RETURNING *""",
            (datetime.now().isoformat(),),
        ).fetchone()
        conn.commit()
        return dict(row) if row else None

    def reclaim_stuck_jobs(self, timeout_hours: int = 6) -> int:
        """回收僵尸任务：status='running'/'processing' 且超时的任务回退为 pending。

        BUG#8 修复：claim_next_pending_job 只认 'pending'，进程崩溃后遗留在
        'running'（或历史非法 'processing'）状态的任务永不被回收。Worker 启动时
        调用本方法清理上次进程遗留的僵尸任务，使其可被重新认领执行。

        v1.6.0 稳定性报告补充：
        - 旧 reindex_checkpoint 写 status='processing' 且 started_at=NULL，
          仅判断 started_at < cutoff 会漏掉；改为同时用 created_at 兜底。
        - id='reindex_checkpoint' 是断点标记而非可执行任务，超时后应 DELETE，
          不能回退为 pending（否则会被 worker 当 reindex_all 认领）。

        Args:
            timeout_hours: 认定僵尸的超时阈值（小时）

        Returns:
            被回退为 pending 或删除的任务数
        """
        cutoff = (datetime.now() - timedelta(hours=timeout_hours)).isoformat()
        conn = self.get_conn()
        total = 0

        # 1) 僵尸 reindex_checkpoint：删除（非回退 pending）
        cursor = conn.execute(
            """DELETE FROM async_jobs
               WHERE id = 'reindex_checkpoint'
                 AND status IN ('running', 'processing')
                 AND (
                     (started_at IS NOT NULL AND started_at < ?)
                     OR (started_at IS NULL AND created_at < ?)
                 )""",
            (cutoff, cutoff),
        )
        total += cursor.rowcount

        # 2) 普通任务：回退 pending（含 started_at IS NULL 的历史僵尸）
        cursor = conn.execute(
            """UPDATE async_jobs
               SET status = 'pending', started_at = NULL
               WHERE id != 'reindex_checkpoint'
                 AND status IN ('running', 'processing')
                 AND (
                     (started_at IS NOT NULL AND started_at < ?)
                     OR (started_at IS NULL AND created_at < ?)
                 )""",
            (cutoff, cutoff),
        )
        total += cursor.rowcount
        conn.commit()
        return total

    def cancel_job(self, job_id: str) -> bool:
        """取消任务"""
        job = self.get_job(job_id)
        if not job:
            return False
        if job["status"] in ("pending", "running"):
            self.update_job_status(job_id, "cancelled")
            return True
        return False

    def delete_job(self, job_id: str) -> bool:
        """删除已完成/失败的任务"""
        job = self.get_job(job_id)
        if not job or job["status"] not in ("completed", "failed", "cancelled"):
            return False
        self.get_conn().execute("DELETE FROM async_jobs WHERE id = ?", (job_id,))
        self.get_conn().commit()
        return True

    def cleanup_old_jobs(self, retention_days: int = 7):
        """清理超过指定天数的已完成/失败任务"""
        conn = self.get_conn()
        conn.execute(
            """DELETE FROM async_jobs
               WHERE status IN ('completed', 'failed', 'cancelled')
               AND completed_at < datetime('now', '-' || ? || ' days')""",
            (retention_days,),
        )
        conn.commit()

    def get_job_stats(self) -> dict:
        """获取任务统计"""
        conn = self.get_conn()
        rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM async_jobs GROUP BY status"
        ).fetchall()
        return {row["status"]: row["count"] for row in rows}

    # ---- Wiki Workflow ----

    def insert_workflow(self, page_id: str, from_status: str, to_status: str,
                        operator: str = "system", comment: str = "") -> str:
        """记录工作流状态转换"""
        import uuid as _uuid
        wf_id = str(_uuid.uuid4())
        now = datetime.now().isoformat()
        self.get_conn().execute(
            """INSERT INTO wiki_workflow (id, page_id, from_status, to_status, operator, comment, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (wf_id, page_id, from_status, to_status, operator, comment, now),
        )
        self.get_conn().commit()
        return wf_id

    def get_workflow_history(self, page_id: str) -> list[dict]:
        """获取页面的工作流历史"""
        rows = self.get_conn().execute(
            "SELECT * FROM wiki_workflow WHERE page_id = ? ORDER BY created_at DESC",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Wiki Page Versions ----

    def save_wiki_version(self, page_id: str, page_data: dict) -> str:
        """保存 Wiki 页面版本快照"""
        import uuid as _uuid
        version_id = str(_uuid.uuid4())
        now = datetime.now().isoformat()
        # 获取当前最大版本号
        row = self.get_conn().execute(
            "SELECT MAX(version) as max_ver FROM wiki_page_versions WHERE page_id = ?",
            (page_id,),
        ).fetchone()
        next_version = (row["max_ver"] or 0) + 1
        self.get_conn().execute(
            """INSERT INTO wiki_page_versions
               (id, page_id, version, title, content, concept_summary, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, page_id, next_version, page_data.get("title", ""),
             page_data.get("content", ""), page_data.get("concept_summary", ""),
             page_data.get("tags", "[]"), now),
        )
        self.get_conn().commit()
        return version_id

    def list_wiki_versions(self, page_id: str) -> list[dict]:
        """列出页面所有版本"""
        rows = self.get_conn().execute(
            "SELECT * FROM wiki_page_versions WHERE page_id = ? ORDER BY version DESC",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_wiki_version(self, page_id: str, version: int) -> Optional[dict]:
        """获取指定版本"""
        row = self.get_conn().execute(
            "SELECT * FROM wiki_page_versions WHERE page_id = ? AND version = ?",
            (page_id, version),
        ).fetchone()
        return dict(row) if row else None

    def get_latest_wiki_version(self, page_id: str) -> Optional[dict]:
        """获取最新版本"""
        row = self.get_conn().execute(
            "SELECT * FROM wiki_page_versions WHERE page_id = ? ORDER BY version DESC LIMIT 1",
            (page_id,),
        ).fetchone()
        return dict(row) if row else None

    # ---- Knowledge Graphs ----

    def insert_graph(self, name: str, description: str = "", source_type: str = "manual") -> str:
        graph_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        self.get_conn().execute(
            "INSERT INTO knowledge_graphs (id, name, description, source_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (graph_id, name, description, source_type, now, now),
        )
        self.get_conn().commit()
        return graph_id

    def get_graph(self, graph_id: str) -> Optional[dict]:
        row = self.get_conn().execute("SELECT * FROM knowledge_graphs WHERE id = ?", (graph_id,)).fetchone()
        return dict(row) if row else None

    def list_graphs(self, source_type: str | None = None) -> list[dict]:
        conn = self.get_conn()
        if source_type:
            rows = conn.execute(
                "SELECT * FROM knowledge_graphs WHERE source_type = ? ORDER BY updated_at DESC",
                (source_type,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM knowledge_graphs ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    def delete_graph(self, graph_id: str):
        # 级联删除由外键约束自动处理
        conn = self.get_conn()
        conn.execute("DELETE FROM knowledge_graphs WHERE id = ?", (graph_id,))
        conn.commit()

    def update_graph(self, graph_id: str, **fields):
        allowed = {"name", "description"}
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Invalid fields: {invalid}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [datetime.now().isoformat(), graph_id]
        self.get_conn().execute(
            f"UPDATE knowledge_graphs SET {sets}, updated_at = ? WHERE id = ?",
            values,
        )
        self.get_conn().commit()

    # ---- Knowledge Graph Nodes ----

    def insert_graph_nodes(self, graph_id: str, knowledge_ids: list[str]):
        conn = self.get_conn()
        for knowledge_id in knowledge_ids:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_graph_nodes (id, graph_id, knowledge_id, x, y, is_pinned) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), graph_id, knowledge_id, 0, 0, 0),
            )
        conn.commit()

    def get_graph_nodes(self, graph_id: str) -> list[dict]:
        rows = self.get_conn().execute(
            """SELECT n.*, ki.title as knowledge_title, ki.file_type, ki.tags
               FROM knowledge_graph_nodes n
               JOIN knowledge_items ki ON ki.id = n.knowledge_id
               WHERE n.graph_id = ? AND ki.deleted_at IS NULL""",
            (graph_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_node_position(self, node_id: str, x: float, y: float):
        self.get_conn().execute(
            "UPDATE knowledge_graph_nodes SET x = ?, y = ? WHERE id = ?",
            (x, y, node_id),
        )
        self.get_conn().commit()

    def batch_update_node_positions(self, positions: list[tuple[float, float, str]]):
        """Batch-update node positions in a single transaction.

        Args:
            positions: list of (x, y, node_id) tuples.
        """
        if not positions:
            return
        conn = self.get_conn()
        conn.executemany(
            "UPDATE knowledge_graph_nodes SET x = ?, y = ? WHERE id = ?",
            positions,
        )
        conn.commit()

    def delete_graph_nodes(self, graph_id: str, knowledge_ids: list[str]):
        if not knowledge_ids:
            return
        placeholders = ",".join("?" for _ in knowledge_ids)
        self.get_conn().execute(
            f"DELETE FROM knowledge_graph_nodes WHERE graph_id = ? AND knowledge_id IN ({placeholders})",
            (graph_id, *knowledge_ids),
        )
        self.get_conn().commit()

    # ---- Knowledge Graph Relations ----

    def insert_graph_relations(self, graph_id: str, relations: list[dict]):
        conn = self.get_conn()
        for rel in relations:
            conn.execute(
                """INSERT INTO knowledge_graph_relations
                   (id, graph_id, source_knowledge_id, target_knowledge_id, relation_type, description, weight)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(graph_id, source_knowledge_id, target_knowledge_id)
                   DO UPDATE SET relation_type=excluded.relation_type,
                                 description=excluded.description,
                                 weight=excluded.weight""",
                (str(uuid.uuid4()), graph_id,
                 rel["source_knowledge_id"], rel["target_knowledge_id"],
                 rel.get("relation_type", "related"),
                 rel.get("description", ""),
                 rel.get("weight", 1.0)),
            )
        conn.commit()

    def get_graph_relations(self, graph_id: str) -> list[dict]:
        rows = self.get_conn().execute(
            """SELECT r.* FROM knowledge_graph_relations r
               JOIN knowledge_items ks ON ks.id = r.source_knowledge_id
               JOIN knowledge_items kt ON kt.id = r.target_knowledge_id
               WHERE r.graph_id = ? AND ks.deleted_at IS NULL AND kt.deleted_at IS NULL""",
            (graph_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_graph_relations(self, graph_id: str):
        self.get_conn().execute(
            "DELETE FROM knowledge_graph_relations WHERE graph_id = ?", (graph_id,)
        )
        self.get_conn().commit()

    def get_graph_for_knowledge(self, knowledge_id: str) -> list[dict]:
        """获取包含指定知识的所有图谱"""
        rows = self.get_conn().execute(
            """SELECT g.* FROM knowledge_graphs g
               JOIN knowledge_graph_nodes n ON n.graph_id = g.id
               WHERE n.knowledge_id = ?""",
            (knowledge_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---- 向后兼容层 ----
# 旧代码通过 Database.xxx() 调用时，_bind_to_instance 描述符自动委托到
# Database._instance（由 connect() 或 __init__ 设置）。
# 新代码应通过 Container 注入的 db 实例调用：container.db.list_knowledge()
# 或者构造 Database 实例：db = Database(db_path); db.list_knowledge()
