"""Block 级向量存储 — 基于 sqlite-vec，rowid 关联 blocks 表"""
import json
import logging
import struct
import threading

import sqlite_vec

from src.utils.config import Config

_lock = threading.Lock()


class BlockStore:
    """Block 级向量存储服务

    支持两种模式:
    1. DI 注入模式: BlockStore.__new__ + 手动设置 _db
    2. 单例模式（兼容）: BlockStore() 自动获取 Database 单例
    """
    _instance = None
    _initialized = False
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
        if self._db is not None:
            return
        from src.services.db import Database
        current_db = Database._instance if hasattr(Database, '_instance') else None
        if current_db is not None and current_db is not getattr(self, '_last_db_instance', None):
            self._last_db_instance = current_db
            self._initialized = False

    def _get_conn(self):
        if self._db is not None:
            return self._db.get_conn()
        from src.services.db import Database
        return Database.get_conn()

    def _get_dimension(self) -> int:
        return Config.get("embedding.dimension", 1024)

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
            dim = self._get_dimension()
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_blocks USING vec0("
                f"embedding float[{dim}] distance_metric=cosine)"
            )
            conn.commit()
            self._initialized = True

    def _pack_embedding(self, embedding: list[float]) -> bytes:
        return struct.pack(f"{len(embedding)}f", *embedding)

    def add_block_embedding(self, block_id: str, embedding: list[float]):
        expected_dim = self._get_dimension()
        if len(embedding) != expected_dim:
            raise ValueError(f"Embedding dimension {len(embedding)} != expected {expected_dim}")
        self._ensure_table()
        conn = self._get_conn()
        row = conn.execute(
            "SELECT rowid FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        if not row:
            logging.warning(f"Block {block_id} not found, skip vec insert")
            return
        rowid = row[0]
        conn.execute(
            "INSERT OR REPLACE INTO vec_blocks(rowid, embedding) VALUES (?, ?)",
            (rowid, self._pack_embedding(embedding)),
        )
        conn.commit()

    def add_blocks_batch(self, blocks: list[dict]):
        for b in blocks:
            emb = b.get("embedding")
            if emb:
                self.add_block_embedding(b["id"], emb)

    def search(self, query: str, top_k: int = 5, tags: list[str] | None = None,
               query_embedding: list[float] | None = None) -> list[dict]:
        self._ensure_table()
        if query_embedding is None:
            from src.services.embedding import EmbeddingService
            query_embedding = EmbeddingService().embed(query)

        conn = self._get_conn()
        packed = self._pack_embedding(query_embedding)
        rows = conn.execute(
            """SELECT b.id, b.page_id, b.content, b.block_type, b.properties,
                      vc.distance
               FROM vec_blocks vc
               JOIN blocks b ON b.rowid = vc.rowid
               WHERE vc.embedding MATCH ? AND k = ?
               ORDER BY vc.distance""",
            (packed, top_k),
        ).fetchall()

        results = []
        for r in rows:
            try:
                properties = json.loads(r[4]) if r[4] else {}
            except (json.JSONDecodeError, TypeError):
                properties = {}
            results.append({
                "id": r[0],
                "text": r[2],
                "metadata": {
                    "page_id": r[1],
                    "block_type": r[3],
                    "properties": properties,
                },
                "distance": r[5],
            })
        return results

    def delete_by_page(self, page_id: str):
        self._ensure_table()
        conn = self._get_conn()
        conn.execute("BEGIN")
        try:
            rows = conn.execute(
                "SELECT rowid FROM blocks WHERE page_id = ?",
                (page_id,),
            ).fetchall()
            if rows:
                placeholders = ",".join("?" for _ in rows)
                conn.execute(
                    f"DELETE FROM vec_blocks WHERE rowid IN ({placeholders})",
                    [r[0] for r in rows],
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def delete_by_block(self, block_id: str):
        self._ensure_table()
        conn = self._get_conn()
        row = conn.execute(
            "SELECT rowid FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        if row:
            conn.execute(
                "DELETE FROM vec_blocks WHERE rowid = ?", (row[0],)
            )
            conn.commit()

    def count(self) -> int:
        self._ensure_table()
        conn = self._get_conn()
        row = conn.execute("SELECT count(*) FROM vec_blocks").fetchone()
        return row[0] if row else 0

    def count_by_page(self, page_id: str) -> int:
        self._ensure_table()
        conn = self._get_conn()
        row = conn.execute(
            """SELECT COUNT(*)
               FROM vec_blocks vc
               JOIN blocks b ON b.rowid = vc.rowid
               WHERE b.page_id = ?""",
            (page_id,),
        ).fetchone()
        return row[0] if row else 0
