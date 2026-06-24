"""向量存储 — 基于 sqlite-vec（LEGACY，逐步弃用）

Phase 2 / search-optimize: 此模块对应旧的 vec_chunks 索引路径。
新路径使用 BlockStore + vec_blocks。设置 rag.legacy_chunk_vector=False
可完全停止写入 vec_chunks。
"""
import logging
import struct
import threading
import warnings

import sqlite_vec

_lock = threading.Lock()
_VEC_DIM = 1024  # bge-m3 输出维度


class VectorStore:
    """向量存储服务

    支持两种模式:
    1. DI 注入模式: VectorStore.__new__ + 手动设置 _db
    2. 单例模式（兼容）: VectorStore() 自动获取 Database 单例
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db=None):
        if db is not None:
            inst = super().__new__(cls)
            inst._initialized = False
            inst._db = db
            return inst
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
                cls._instance._db = None
            return cls._instance

    def _check_db_changed(self):
        """检测数据库连接是否发生变化，若变化则重置初始化状态"""
        if self._db is not None:
            return
        from src.services.db import Database
        current_db = Database._instance if hasattr(Database, '_instance') else None
        if current_db is not None and current_db is not getattr(self, '_last_db_instance', None):
            self._last_db_instance = current_db
            self._initialized = False

    def _get_conn(self):
        """获取数据库连接，优先使用注入的 db，回退到 Database 单例"""
        if self._db is not None:
            return self._db.get_conn()
        from src.services.db import Database
        return Database.get_conn()

    def _ensure_table(self):
        self._check_db_changed()
        if self._initialized:
            return
        with _lock:
            if self._initialized:
                return
            conn = self._get_conn()
            try:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
            finally:
                conn.enable_load_extension(False)
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0("
                f"embedding float[{_VEC_DIM}] distance_metric=cosine)"
            )
            conn.commit()
            self._initialized = True

    def _pack_embedding(self, embedding: list[float]) -> bytes:
        return struct.pack(f"{len(embedding)}f", *embedding)

    def add_chunk_embedding(self, chunk_id: str, knowledge_id: str,
                            embedding: list[float], metadata: dict | None = None):
        self._ensure_table()
        conn = self._get_conn()
        row = conn.execute(
            "SELECT rowid FROM knowledge_chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
        if not row:
            logging.warning(f"Chunk {chunk_id} not found, skip vec insert")
            return
        rowid = row[0]
        conn.execute(
            "INSERT OR REPLACE INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
            (rowid, self._pack_embedding(embedding)),
        )
        conn.commit()

    def add_chunks(self, chunks: list[dict]):
        """兼容旧接口：批量写入（由 indexer 调用）。
        chunks 中每个元素需包含 id, knowledge_id, embedding"""
        for c in chunks:
            emb = c.get("embedding")
            if emb:
                self.add_chunk_embedding(
                    c["id"], c.get("knowledge_id", ""), emb, c.get("metadata")
                )

    def search(self, query: str, top_k: int = 5, tags: list[str] | None = None,
               query_embedding: list[float] | None = None) -> list[dict]:
        self._ensure_table()
        if query_embedding is None:
            from src.services.embedding import EmbeddingService
            query_embedding = EmbeddingService().embed(query)

        conn = self._get_conn()
        packed = self._pack_embedding(query_embedding)

        sql = """SELECT kc.id, kc.knowledge_id, kc.chunk_text,
                        vc.distance
                 FROM vec_chunks vc
                 JOIN knowledge_chunks kc ON kc.rowid = vc.rowid"""
        params: list = [packed, top_k]

        if tags:
            sql += """ JOIN knowledge_items ki ON ki.id = kc.knowledge_id"""
            tag_conditions = []
            for tag in tags:
                tag_conditions.append("ki.tags LIKE ?")
                params.append(f'%"{tag}"%')
            sql += " AND (" + " OR ".join(tag_conditions) + ")"

        sql += " WHERE vc.embedding MATCH ? AND k = ? ORDER BY vc.distance"
        rows = conn.execute(sql, params).fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r[0],
                "text": r[2],
                "metadata": {"knowledge_id": r[1], "chunk_index": 0},
                "distance": r[3],
            })
        return results

    def delete_by_knowledge(self, knowledge_id: str):
        self._ensure_table()
        conn = self._get_conn()
        conn.execute("BEGIN")
        try:
            rows = conn.execute(
                "SELECT rowid FROM knowledge_chunks WHERE knowledge_id = ?",
                (knowledge_id,),
            ).fetchall()
            if rows:
                placeholders = ",".join("?" for _ in rows)
                conn.execute(
                    f"DELETE FROM vec_chunks WHERE rowid IN ({placeholders})",
                    [r[0] for r in rows],
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def count(self) -> int:
        self._ensure_table()
        conn = self._get_conn()
        row = conn.execute("SELECT count(*) FROM vec_chunks").fetchone()
        return row[0] if row else 0

    def count_by_knowledge(self, knowledge_id: str) -> int:
        self._ensure_table()
        conn = self._get_conn()
        row = conn.execute(
            """SELECT COUNT(*)
               FROM vec_chunks vc
               JOIN knowledge_chunks kc ON kc.rowid = vc.rowid
               WHERE kc.knowledge_id = ?""",
            (knowledge_id,),
        ).fetchone()
        return row[0] if row else 0
