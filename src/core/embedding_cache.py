"""
ShineHeKnowledge Embedding Cache — SQLite-backed cache for computed embeddings.

Avoids redundant API calls by storing (content_hash, model) -> embedding pairs
as packed BLOBs in a dedicated SQLite table.

Phase 3: Added TTL support — entries older than ttl_hours are automatically
cleaned on read/write and via explicit cleanup_expired() calls.
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.db import Database

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash TEXT NOT NULL,
    model        TEXT NOT NULL,
    embedding    BLOB NOT NULL,
    created_at   TEXT NOT NULL,
    ttl_hours    INTEGER NOT NULL DEFAULT 168,
    PRIMARY KEY (content_hash, model)
)
"""

_CREATE_TTL_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_embedding_cache_ttl
ON embedding_cache(created_at, ttl_hours)
"""


class EmbeddingCache:
    """SQLite-backed embedding vector cache with TTL support."""

    def __init__(self, db: Database | None = None, ttl_hours: int = 168):
        """Initialise the cache.

        Args:
            db: Database instance.  Falls back to the global singleton.
            ttl_hours: Default TTL in hours for new entries (default 168 = 7 days).
        """
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
        self._ttl_hours = ttl_hours
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
            # Phase 3: try to add ttl_hours column (migration for existing DBs)
            try:
                conn.execute("ALTER TABLE embedding_cache ADD COLUMN ttl_hours INTEGER NOT NULL DEFAULT 168")
            except Exception:
                pass  # Column already exists
            conn.execute(_CREATE_TTL_INDEX_SQL)
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

    def _is_expired(self, created_at: str, ttl_hours: int | None = None) -> bool:
        """Check if a cache entry has expired based on its TTL."""
        if ttl_hours is None:
            ttl_hours = self._ttl_hours
        try:
            created = datetime.fromisoformat(created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            expiry = created + timedelta(hours=ttl_hours)
            return datetime.now(timezone.utc) > expiry
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get(self, content_hash: str, model: str) -> list[float] | None:
        """Return cached embedding or ``None`` if not found or expired."""
        with self._db.get_conn() as conn:
            row = conn.execute(
                "SELECT embedding, created_at, ttl_hours FROM embedding_cache "
                "WHERE content_hash = ? AND model = ?",
                (content_hash, model),
            ).fetchone()
        if row is None:
            return None
        # Phase 3: check TTL
        if self._is_expired(row["created_at"], row["ttl_hours"]):
            # Expired: delete and return miss
            try:
                with self._db.get_conn() as conn:
                    conn.execute(
                        "DELETE FROM embedding_cache WHERE content_hash = ? AND model = ?",
                        (content_hash, model),
                    )
                    conn.commit()
            except Exception:
                pass
            return None
        try:
            return self._unpack(row["embedding"])
        except Exception:
            # Corrupted data: silently delete
            try:
                with self._db.get_conn() as conn:
                    conn.execute(
                        "DELETE FROM embedding_cache WHERE content_hash = ? AND model = ?",
                        (content_hash, model),
                    )
                    conn.commit()
            except Exception:
                pass
            return None

    def put(self, content_hash: str, model: str, embedding: list[float]) -> None:
        """Store an embedding in the cache with TTL."""
        blob = self._pack(embedding)
        now = datetime.now(timezone.utc).isoformat()
        ttl = self._ttl_hours
        with self._db.get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO embedding_cache "
                "(content_hash, model, embedding, created_at, ttl_hours) "
                "VALUES (?, ?, ?, ?, ?)",
                (content_hash, model, blob, now, ttl),
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

    def cleanup_expired(self) -> int:
        """Phase 3: Delete all expired cache entries. Returns deleted row count."""
        deleted = 0
        now = datetime.now(timezone.utc)
        try:
            with self._db.get_conn() as conn:
                rows = conn.execute(
                    "SELECT content_hash, model, created_at, ttl_hours FROM embedding_cache"
                ).fetchall()
                for row in rows:
                    if self._is_expired(row["created_at"], row["ttl_hours"]):
                        conn.execute(
                            "DELETE FROM embedding_cache WHERE content_hash = ? AND model = ?",
                            (row["content_hash"], row["model"]),
                        )
                        deleted += 1
                if deleted > 0:
                    conn.commit()
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("Embedding cache cleanup failed: %s", e)
        return deleted
