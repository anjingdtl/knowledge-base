"""知识条目仓库 — knowledge_items / knowledge_versions / knowledge_chunks / FTS"""
import json
import threading
import uuid
from datetime import datetime
from typing import Optional

from src.services.db import Database


class KnowledgeRepository:
    """知识条目、版本控制、分块、全文索引"""

    def __init__(self, db=None):
        self._db = db or Database
        self._write_lock = threading.Lock()

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

    def get(self, item_id: str, include_deleted: bool = False) -> Optional[dict]:
        """按 ID 查询；默认过滤已软删除条目（Phase 4 / Sprint 3）。"""
        conn = self._conn()
        if include_deleted:
            row = conn.execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM knowledge_items WHERE id = ? AND deleted_at IS NULL",
                (item_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_by_hash(self, content_hash: str, include_deleted: bool = False) -> Optional[dict]:
        conn = self._conn()
        clause = "AND deleted_at IS NULL" if not include_deleted else ""
        row = conn.execute(
            f"SELECT * FROM knowledge_items WHERE content_hash = ? {clause} LIMIT 1",
            (content_hash,),
        ).fetchone()
        return dict(row) if row else None

    def get_batch(self, ids: list[str], include_deleted: bool = False) -> dict[str, dict]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        clause = "AND deleted_at IS NULL" if not include_deleted else ""
        rows = self._conn().execute(
            f"SELECT * FROM knowledge_items WHERE id IN ({placeholders}) {clause}", ids
        ).fetchall()
        return {row["id"]: dict(row) for row in rows}

    def list(self, tag: str | None = None, file_type: str | None = None,
             quality: str | None = None,
             sort_by: str = "updated_at", sort_order: str = "DESC",
             limit: int = 100, offset: int = 0,
             include_deleted: bool = False) -> list[dict]:
        """列出知识条目；默认过滤已软删除条目。"""
        conditions, params = [], []
        if not include_deleted:
            conditions.append("deleted_at IS NULL")
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
        with self._write_lock:
            # Phase 4: 默认过滤已软删除条目
            old = self.get(item_id, include_deleted=False)
            if not old:
                raise ValueError(f"Knowledge item {item_id} not found or has been deleted")
            _version_fields = {"title", "content", "tags"}
            if _version_fields & set(fields):
                self._save_version(item_id, old)
            sets = ", ".join(f'"{k}" = ?' for k in fields)
            values = list(fields.values()) + [datetime.now().isoformat(), item_id]
            cursor = self._conn().execute(
                f'UPDATE knowledge_items SET {sets}, version = version + 1, updated_at = ? '
                f'WHERE id = ? AND deleted_at IS NULL',
                values,
            )
            self._conn().commit()
            if cursor.rowcount == 0:
                raise ValueError(f"Knowledge item {item_id} not found or has been deleted")

    def delete(self, item_id: str, hard: bool = False) -> None:
        """Phase 4 / Sprint 3: 软删除（默认）。

        Args:
            item_id: 知识条目 ID
            hard: True=硬删（彻底删除所有关联数据），False=软删（设置 deleted_at）
        """
        with self._write_lock:
            if hard:
                self._hard_delete_unlocked(item_id)
            else:
                self._conn().execute(
                    "UPDATE knowledge_items SET deleted_at = ? "
                    "WHERE id = ? AND deleted_at IS NULL",
                    (datetime.now().isoformat(), item_id),
                )
                self._conn().commit()

    def _hard_delete_unlocked(self, item_id: str) -> None:
        """硬删 — 彻底清理 knowledge_items + 关联表。"""
        conn = self._conn()
        conn.execute("DELETE FROM chunk_fts WHERE knowledge_id = ?", (item_id,))
        conn.execute("DELETE FROM knowledge_chunks WHERE knowledge_id = ?", (item_id,))
        conn.execute("DELETE FROM knowledge_versions WHERE knowledge_id = ?", (item_id,))
        conn.execute(
            "DELETE FROM block_property_index WHERE block_id IN "
            "(SELECT id FROM blocks WHERE page_id = ?)",
            (item_id,),
        )
        conn.execute(
            "DELETE FROM entity_refs WHERE (source_type = 'knowledge' AND source_id = ?) "
            "OR (target_type = 'knowledge' AND target_id = ?)",
            (item_id, item_id),
        )
        conn.execute("DELETE FROM blocks WHERE page_id = ?", (item_id,))
        conn.execute("DELETE FROM knowledge_items WHERE id = ?", (item_id,))
        conn.commit()
        try:
            from src.services.vectorstore import VectorStore
            VectorStore().delete_by_knowledge(item_id)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "VectorStore deletion failed for item %s; proceeding with DB cleanup", item_id, exc_info=True
            )

    def restore(self, item_id: str) -> bool:
        """Phase 4: 恢复软删除条目（清除 deleted_at）。"""
        with self._write_lock:
            cursor = self._conn().execute(
                "UPDATE knowledge_items SET deleted_at = NULL "
                "WHERE id = ? AND deleted_at IS NOT NULL",
                (item_id,),
            )
            self._conn().commit()
            return cursor.rowcount > 0

    def purge(self, item_id: str) -> bool:
        """Phase 4: 硬删（彻底清理所有关联数据）。

        Returns:
            True 如果条目存在并被删除；False 如果条目不存在
        """
        with self._write_lock:
            existing = self._conn().execute(
                "SELECT id FROM knowledge_items WHERE id = ?", (item_id,),
            ).fetchone()
            if not existing:
                return False
            self._hard_delete_unlocked(item_id)
            return True

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
        self._conn().execute(
            """INSERT INTO knowledge_versions (id, knowledge_id, version, title, content, tags, created_at)
               VALUES (?, ?, (SELECT COALESCE(MAX(version), 0) + 1 FROM knowledge_versions WHERE knowledge_id = ?), ?, ?, ?, ?)""",
            (str(uuid.uuid4()), knowledge_id, knowledge_id, snapshot["title"],
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
        self._upsert_blocks_from_chunks(chunks)
        conn.commit()

    def _upsert_blocks_from_chunks(self, chunks: list[dict]):
        if not chunks:
            return
        block_rows = []
        prop_rows = []
        for chunk in chunks:
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
                "created_at": chunk.get("created_at", ""),
                "updated_at": chunk.get("created_at", ""),
            })
            prop_rows.append({
                "block_id": chunk["id"],
                "prop_key": "knowledge_id",
                "prop_value": chunk["knowledge_id"],
                "value_type": "ref",
            })
        conn = self._conn()
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
