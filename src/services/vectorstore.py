"""向量存储 — 基于 sqlite-vec，与 SQLite 数据库共享连接"""
import logging
import struct
import threading

import sqlite_vec

from src.utils.config import Config

_lock = threading.Lock()
_VEC_DIM = 1024  # bge-m3 输出维度


class VectorStore:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def _ensure_table(self):
        if self._initialized:
            return
        with _lock:
            if self._initialized:
                return
            from src.services.db import Database
            conn = Database.get_conn()
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
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
        from src.services.db import Database
        conn = Database.get_conn()
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

        from src.services.db import Database
        conn = Database.get_conn()
        packed = self._pack_embedding(query_embedding)
        rows = conn.execute(
            """SELECT kc.id, kc.knowledge_id, kc.chunk_text,
                      vc.distance
               FROM vec_chunks vc
               JOIN knowledge_chunks kc ON kc.rowid = vc.rowid
               WHERE vc.embedding MATCH ? AND k = ?
               ORDER BY vc.distance""",
            (packed, top_k),
        ).fetchall()

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
        from src.services.db import Database
        conn = Database.get_conn()
        rows = conn.execute(
            "SELECT rowid FROM knowledge_chunks WHERE knowledge_id = ?",
            (knowledge_id,),
        ).fetchall()
        if rows:
            rowids = ",".join(str(r[0]) for r in rows)
            conn.execute(f"DELETE FROM vec_chunks WHERE rowid IN ({rowids})")
            conn.commit()

    def count(self) -> int:
        self._ensure_table()
        from src.services.db import Database
        conn = Database.get_conn()
        row = conn.execute("SELECT count(*) FROM vec_chunks").fetchone()
        return row[0] if row else 0
