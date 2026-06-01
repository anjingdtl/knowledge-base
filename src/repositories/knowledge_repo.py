"""知识条目仓库 — knowledge_items / knowledge_versions / knowledge_chunks / FTS"""
import json
import uuid
from datetime import datetime
from typing import Optional

from src.services.db import Database


class KnowledgeRepository:
    """知识条目、版本控制、分块、全文索引"""

    def __init__(self, db=None):
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    # ---- Knowledge Items CRUD ----

    def insert(self, item: dict) -> str:
        self._conn().execute(
            """INSERT INTO knowledge_items
               (id, title, content, source_type, source_path, file_type, file_size, content_hash,
                file_created_at, file_modified_at, tags, version, created_at, updated_at)
               VALUES (:id, :title, :content, :source_type, :source_path, :file_type, :file_size,
                :content_hash, :file_created_at, :file_modified_at, :tags, :version, :created_at, :updated_at)""",
            item,
        )
        self._conn().commit()
        return item["id"]

    def get(self, item_id: str) -> Optional[dict]:
        row = self._conn().execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def get_by_hash(self, content_hash: str) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM knowledge_items WHERE content_hash = ? LIMIT 1", (content_hash,)
        ).fetchone()
        return dict(row) if row else None

    def get_batch(self, ids: list[str]) -> dict[str, dict]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self._conn().execute(
            f"SELECT * FROM knowledge_items WHERE id IN ({placeholders})", ids
        ).fetchall()
        return {row["id"]: dict(row) for row in rows}

    def list(self, tag: str | None = None, file_type: str | None = None,
             quality: str | None = None,
             sort_by: str = "updated_at", sort_order: str = "DESC",
             limit: int = 100, offset: int = 0) -> list[dict]:
        conditions, params = [], []
        if tag:
            # NOTE: LIKE matching on JSON-encoded tags. Pattern '%"tag_name"%'
            # prevents partial matches on unquoted text but can still produce
            # false positives if one tag value is a substring of another
            # (e.g. tag "my-tag" matches stored "my-tag-extra").
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
        rows = self._conn().execute(
            f"SELECT * FROM knowledge_items{where} ORDER BY {sort_by} {sort_order} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    def update(self, item_id: str, **fields):
        if not fields:
            return
        allowed = {"title", "content", "source_type", "source_path", "file_type",
                    "file_size", "file_created_at", "file_modified_at", "tags", "quality"}
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Invalid fields: {invalid}")
        old = self.get(item_id)
        if old and old.get("content") != fields.get("content"):
            self._save_version(item_id, old)
        sets = ", ".join(f'"{k}" = ?' for k in fields)
        values = list(fields.values()) + [datetime.now().isoformat(), item_id]
        cursor = self._conn().execute(
            f'UPDATE knowledge_items SET {sets}, version = version + 1, updated_at = ? WHERE id = ?',
            values,
        )
        self._conn().commit()
        if cursor.rowcount == 0:
            raise ValueError(f"Knowledge item {item_id} not found")

    def delete(self, item_id: str):
        # VectorStore deletion is best-effort: DB cleanup must succeed even if
        # ChromaDB is unavailable or VectorStore initialization fails.
        try:
            from src.services.vectorstore import VectorStore
            VectorStore().delete_by_knowledge(item_id)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "VectorStore deletion failed for item %s; proceeding with DB cleanup", item_id, exc_info=True
            )
        self.delete_chunks_fts(item_id)
        conn = self._conn()
        conn.execute("DELETE FROM knowledge_chunks WHERE knowledge_id = ?", (item_id,))
        conn.execute("DELETE FROM knowledge_versions WHERE knowledge_id = ?", (item_id,))
        conn.execute("DELETE FROM knowledge_items WHERE id = ?", (item_id,))
        conn.commit()

    def count(self, tag: str | None = None) -> int:
        if tag:
            row = self._conn().execute(
                "SELECT COUNT(*) as cnt FROM knowledge_items WHERE tags LIKE ?",
                (f'%"{tag}"%',),
            ).fetchone()
        else:
            row = self._conn().execute("SELECT COUNT(*) as cnt FROM knowledge_items").fetchone()
        return row["cnt"]

    def get_stats(self) -> dict:
        conn = self._conn()
        total_files = conn.execute("SELECT COUNT(*) as cnt FROM knowledge_items").fetchone()["cnt"]
        total_size = conn.execute(
            "SELECT COALESCE(SUM(file_size), 0) as sz FROM knowledge_items"
        ).fetchone()["sz"]
        type_rows = conn.execute(
            "SELECT file_type, COUNT(*) as cnt FROM knowledge_items GROUP BY file_type ORDER BY cnt DESC"
        ).fetchall()
        file_type_dist = {r["file_type"] or "other": r["cnt"] for r in type_rows}
        cat_count = 0
        try:
            cat_rows = conn.execute("SELECT DISTINCT category_id FROM knowledge_categories").fetchall()
            cat_count = len(cat_rows)
        except Exception:
            pass
        return {"total_files": total_files, "total_size": total_size,
                "file_type_dist": file_type_dist, "category_coverage": cat_count}

    def find_duplicates(self) -> list[list[dict]]:
        rows = self._conn().execute(
            "SELECT id, title, source_path, file_size, file_created_at, file_modified_at, created_at FROM knowledge_items"
        ).fetchall()
        groups = {}
        for row in rows:
            src = (row["source_path"] or "").strip()
            size = row["file_size"] or 0
            fcat = (row["file_created_at"] or "").strip()
            fmat = (row["file_modified_at"] or "").strip()
            if src and size > 0:
                key = (src, size, fcat, fmat)
                groups.setdefault(key, []).append(dict(row))
        return [sorted(g, key=lambda x: x.get("created_at", ""), reverse=True)
                for g in groups.values() if len(g) > 1]

    def get_all_tags(self) -> list[str]:
        rows = self._conn().execute("SELECT tags FROM knowledge_items WHERE tags IS NOT NULL").fetchall()
        tags_set = set()
        for row in rows:
            try:
                tags_set.update(json.loads(row["tags"]))
            except (json.JSONDecodeError, TypeError):
                pass
        return sorted(tags_set)

    def get_all_file_types(self) -> list[str]:
        rows = self._conn().execute(
            "SELECT DISTINCT file_type FROM knowledge_items WHERE file_type IS NOT NULL AND file_type != '' ORDER BY file_type"
        ).fetchall()
        return [row["file_type"] for row in rows]

    def get_all_classified_ids(self) -> set[str]:
        rows = self._conn().execute("SELECT DISTINCT knowledge_id FROM knowledge_categories").fetchall()
        return {row[0] for row in rows}

    # ---- Version Control ----

    def _save_version(self, knowledge_id: str, snapshot: dict):
        version = snapshot.get("version", 1)
        self._conn().execute(
            """INSERT INTO knowledge_versions (id, knowledge_id, version, title, content, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), knowledge_id, version, snapshot["title"],
             snapshot.get("content", ""), snapshot.get("tags", "[]"), datetime.now().isoformat()),
        )
        self._conn().commit()

    def list_versions(self, knowledge_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM knowledge_versions WHERE knowledge_id = ? ORDER BY version DESC",
            (knowledge_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_version(self, knowledge_id: str, version: int) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM knowledge_versions WHERE knowledge_id = ? AND version = ?",
            (knowledge_id, version),
        ).fetchone()
        return dict(row) if row else None

    def restore_version(self, knowledge_id: str, version: int):
        ver = self.get_version(knowledge_id, version)
        if not ver:
            raise ValueError(f"版本 {version} 不存在")
        old = self.get(knowledge_id)
        if old:
            self._save_version(knowledge_id, old)
        self._conn().execute(
            "UPDATE knowledge_items SET title = ?, content = ?, tags = ?, version = version + 1, updated_at = ? WHERE id = ?",
            (ver["title"], ver["content"], ver["tags"], datetime.now().isoformat(), knowledge_id),
        )
        self._conn().commit()

    # ---- Chunks ----

    def insert_chunks(self, chunks: list[dict]):
        _required_chunk_keys = {"id", "knowledge_id", "chunk_index", "chunk_text", "created_at"}
        for i, c in enumerate(chunks):
            missing = _required_chunk_keys - set(c.keys())
            if missing:
                raise ValueError(f"Chunk at index {i} is missing required keys: {missing}")
        conn = self._conn()
        conn.executemany(
            """INSERT INTO knowledge_chunks (id, knowledge_id, chunk_index, chunk_text, created_at)
               VALUES (:id, :knowledge_id, :chunk_index, :chunk_text, :created_at)""",
            chunks,
        )
        conn.commit()

    def get_chunks_by_knowledge(self, knowledge_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM knowledge_chunks WHERE knowledge_id = ? ORDER BY chunk_index",
            (knowledge_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_chunk(self, chunk_id: str) -> Optional[dict]:
        row = self._conn().execute("SELECT * FROM knowledge_chunks WHERE id = ?", (chunk_id,)).fetchone()
        return dict(row) if row else None

    # ---- Chunk FTS ----

    def insert_chunks_fts(self, chunks: list[dict]):
        from src.utils.chinese_tokenizer import tokenize_chinese_full
        import logging
        logger = logging.getLogger(__name__)
        _required_fts_keys = {"id", "knowledge_id", "chunk_text"}
        conn = self._conn()
        for i, c in enumerate(chunks):
            missing = _required_fts_keys - set(c.keys())
            if missing:
                logger.error("FTS chunk at index %d is missing required keys %s; skipping", i, missing)
                continue
            try:
                segmented = tokenize_chinese_full(c["chunk_text"])
            except Exception:
                logger.error("Tokenization failed for chunk %s; skipping", c.get("id", f"index-{i}"), exc_info=True)
                continue
            conn.execute(
                "INSERT INTO chunk_fts(fts_segmented, knowledge_id, chunk_id) VALUES (?, ?, ?)",
                (segmented, c["knowledge_id"], c["id"]),
            )
        conn.commit()

    def delete_chunks_fts(self, knowledge_id: str):
        self._conn().execute("DELETE FROM chunk_fts WHERE knowledge_id = ?", (knowledge_id,))
        self._conn().commit()

    def search_chunks_fts(self, query: str, limit: int = 20) -> list[dict]:
        from src.utils.chinese_tokenizer import tokenize_chinese_full, sanitize_fts_query
        tokenized_query = tokenize_chinese_full(query)
        if not tokenized_query.strip():
            return []
        safe_query = sanitize_fts_query(tokenized_query, is_tokenized=True)
        if not safe_query:
            return []
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT cf.chunk_id, cf.knowledge_id, rank as fts_rank
                   FROM chunk_fts cf WHERE chunk_fts MATCH ? ORDER BY fts_rank LIMIT ?""",
                (safe_query, limit),
            ).fetchall()
            results = []
            for r in rows:
                chunk = conn.execute(
                    "SELECT id, knowledge_id, chunk_index, chunk_text FROM knowledge_chunks WHERE id = ?",
                    (r["chunk_id"],),
                ).fetchone()
                if chunk:
                    results.append(dict(chunk) | {"fts_rank": r["fts_rank"]})
            return results
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "FTS search failed for query %r", query, exc_info=True
            )
            return []

    # ---- Knowledge FTS ----

    def search(self, query: str, limit: int = 20, offset: int = 0) -> list[dict]:
        from src.utils.chinese_tokenizer import sanitize_fts_query
        conn = self._conn()
        try:
            safe_query = sanitize_fts_query(query)
            if safe_query:
                fts_rows = conn.execute(
                    """SELECT ki.*, rank as fts_rank FROM knowledge_fts kf
                       JOIN knowledge_items ki ON ki.rowid = kf.rowid
                       WHERE knowledge_fts MATCH ? ORDER BY fts_rank LIMIT ? OFFSET ?""",
                    (safe_query, limit, offset),
                ).fetchall()
                if fts_rows:
                    return [dict(r) for r in fts_rows]
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "FTS knowledge search failed for query %r; falling back to LIKE", query, exc_info=True
            )
        # Escape LIKE wildcards in user input to prevent wildcard injection
        escaped = query.replace('%', '\\%').replace('_', '\\_')
        rows = conn.execute(
            "SELECT * FROM knowledge_items WHERE title LIKE ? OR content LIKE ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (f"%{escaped}%", f"%{escaped}%", limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
