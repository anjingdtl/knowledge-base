"""
ShineHeKnowledge Embedding Cache — SQLite-backed cache for computed embeddings.

Avoids redundant API calls by storing (content_hash, model) -> embedding pairs
as packed BLOBs in a dedicated SQLite table.

Usage:
    cache = EmbeddingCache()
    vec = cache.get(sha256_hex, "text-embedding-3-small")
    if vec is None:
        vec = call_embedding_api(text)
        cache.put(sha256_hex, "text-embedding-3-small", vec)
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.db import Database

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash TEXT NOT NULL,
    model        TEXT NOT NULL,
    embedding    BLOB NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (content_hash, model)
)
"""


class EmbeddingCache:
    """SQLite-backed embedding vector cache."""

    def __init__(self, db: Database | None = None):
        # Lazy import to avoid circular dependencies at module level.
        if db is None:
            from src.services.db import Database
            db = Database._instance
            if db is None or db._shutdown:
                raise RuntimeError(
                    "EmbeddingCache requires a connected Database, but the "
                    "Database singleton has been shut down. Pass an active "
                    "db instance or reconnect before creating EmbeddingCache."
                )

        self._db = db
        try:
            self._ensure_table()
        except Exception as exc:
            raise RuntimeError(
                f"EmbeddingCache failed to initialise its database table: {exc}. "
                "Ensure Database.connect() has been called before creating EmbeddingCache, "
                "or pass an already-connected db instance."
            ) from exc

    # ------------------------------------------------------------------
    # Table bootstrap
    # ------------------------------------------------------------------
    def _ensure_table(self) -> None:
        with self._db.get_conn() as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.commit()

    # ------------------------------------------------------------------
    # Pack / unpack helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _pack(embedding: list[float]) -> bytes:
        """Pack a float32 vector into a BLOB."""
        return struct.pack(f"<{len(embedding)}f", *embedding)

    @staticmethod
    def _unpack(blob: bytes) -> list[float]:
        """Unpack a BLOB back into a float32 list."""
        count = len(blob) // struct.calcsize("<f")
        return list(struct.unpack(f"<{count}f", blob))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get(self, content_hash: str, model: str) -> list[float] | None:
        """Return cached embedding or ``None`` if not found."""
        with self._db.get_conn() as conn:
            row = conn.execute(
                "SELECT embedding FROM embedding_cache "
                "WHERE content_hash = ? AND model = ?",
                (content_hash, model),
            ).fetchone()
        if row is None:
            return None
        return self._unpack(row["embedding"])

    def put(self, content_hash: str, model: str, embedding: list[float]) -> None:
        """Store an embedding in the cache."""
        blob = self._pack(embedding)
        now = datetime.now(timezone.utc).isoformat()
        with self._db.get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO embedding_cache "
                "(content_hash, model, embedding, created_at) "
                "VALUES (?, ?, ?, ?)",
                (content_hash, model, blob, now),
            )
            conn.commit()

    def invalidate_model(self, model: str) -> int:
        """Delete all cached embeddings for *model*. Returns deleted row count."""
        with self._db.get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM embedding_cache WHERE model = ?",
                (model,),
            )
            conn.commit()
            return cursor.rowcount
