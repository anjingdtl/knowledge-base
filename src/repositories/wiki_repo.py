"""Wiki 仓库 — wiki_pages / wiki_links / wiki_ops_log / wiki_workflow / wiki_page_versions / wiki FTS"""
import json
import logging
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class WikiRepository:
    """Wiki 页面、链接、操作日志、工作流、版本"""

    def __init__(self, db=None):
        from src.services.db import Database
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    # ---- Wiki Pages ----

    def insert_page(self, page: dict) -> str:
        self._conn().execute(
            """INSERT INTO wiki_pages
               (id, title, content, source_ids, tags, concept_summary, status, lint_score, created_at, updated_at)
               VALUES (:id, :title, :content, :source_ids, :tags, :concept_summary, :status, :lint_score, :created_at, :updated_at)""",
            page,
        )
        self._conn().commit()
        return page["id"]

    def get_page(self, page_id: str) -> Optional[dict]:
        row = self._conn().execute("SELECT * FROM wiki_pages WHERE id = ?", (page_id,)).fetchone()
        return dict(row) if row else None

    def get_page_by_title(self, title: str) -> Optional[dict]:
        row = self._conn().execute("SELECT * FROM wiki_pages WHERE title = ?", (title,)).fetchone()
        return dict(row) if row else None

    def update_page(self, page_id: str, **fields) -> bool:
        if not fields:
            return False
        allowed = {"title", "content", "source_ids", "tags", "concept_summary", "status", "lint_score"}
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Invalid fields: {invalid}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [datetime.now().isoformat(), page_id]
        cursor = self._conn().execute(
            f"UPDATE wiki_pages SET {sets}, updated_at = ? WHERE id = ?",
            values,
        )
        self._conn().commit()
        return cursor.rowcount > 0

    def delete_page(self, page_id: str):
        conn = self._conn()
        conn.execute("DELETE FROM wiki_links WHERE source_page_id = ? OR target_page_id = ?", (page_id, page_id))
        conn.execute("DELETE FROM wiki_ops_log WHERE target_id = ?", (page_id,))
        conn.execute("DELETE FROM wiki_pages WHERE id = ?", (page_id,))
        conn.commit()

    def list_pages(self, status: str | None = None, search: str | None = None,
                   sort_by: str = "updated_at", sort_order: str = "DESC",
                   limit: int = 100, offset: int = 0) -> list[dict]:
        conditions, params = [], []
        if status:
            if status == "active":
                status = "published"
            conditions.append("status = ?")
            params.append(status)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append("title LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        valid_sorts = {"updated_at", "created_at", "title", "lint_score"}
        sort_by = sort_by if sort_by in valid_sorts else "updated_at"
        sort_order = "DESC" if sort_order.upper() == "DESC" else "ASC"
        rows = self._conn().execute(
            f"SELECT * FROM wiki_pages{where} ORDER BY {sort_by} {sort_order} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    def count_pages(self, status: str | None = None) -> int:
        if status:
            row = self._conn().execute("SELECT COUNT(*) as cnt FROM wiki_pages WHERE status = ?", (status,)).fetchone()
        else:
            row = self._conn().execute("SELECT COUNT(*) as cnt FROM wiki_pages").fetchone()
        return row["cnt"]

    def search_fts(self, query: str, limit: int = 10) -> list[dict]:
        from src.utils.chinese_tokenizer import sanitize_fts_query
        try:
            safe_query = sanitize_fts_query(query)
            if not safe_query:
                return []
            rows = self._conn().execute(
                """SELECT wp.*, rank as fts_rank FROM wiki_fts wf
                   JOIN wiki_pages wp ON wp.rowid = wf.rowid
                   WHERE wiki_fts MATCH ? AND wp.status = 'published'
                   ORDER BY fts_rank LIMIT ?""",
                (safe_query, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning('Wiki FTS search failed: %s', e)
            return []

    # ---- Wiki Links ----

    def add_link(self, source_page_id: str, target_page_id: str,
                 link_type: str = "related", weight: float = 1.0):
        if source_page_id == target_page_id:
            raise ValueError('Cannot link page to itself')
        self._conn().execute(
            "INSERT OR REPLACE INTO wiki_links (source_page_id, target_page_id, link_type, weight) VALUES (?, ?, ?, ?)",
            (source_page_id, target_page_id, link_type, weight),
        )
        self._conn().commit()

    def remove_link(self, source_page_id: str, target_page_id: str):
        self._conn().execute(
            "DELETE FROM wiki_links WHERE source_page_id = ? AND target_page_id = ?",
            (source_page_id, target_page_id),
        )
        self._conn().commit()

    def get_links_for_page(self, page_id: str) -> list[dict]:
        rows = self._conn().execute(
            """SELECT wl.*, wp.title as target_title FROM wiki_links wl
               JOIN wiki_pages wp ON wp.id = wl.target_page_id WHERE wl.source_page_id = ?""",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_backlinks(self, page_id: str) -> list[dict]:
        rows = self._conn().execute(
            """SELECT wl.*, wp.title as source_title FROM wiki_links wl
               JOIN wiki_pages wp ON wp.id = wl.source_page_id WHERE wl.target_page_id = ?""",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_links(self) -> list[dict]:
        rows = self._conn().execute(
            """SELECT wl.*, sp.title as source_title, tp.title as target_title
               FROM wiki_links wl
               JOIN wiki_pages sp ON sp.id = wl.source_page_id
               JOIN wiki_pages tp ON tp.id = wl.target_page_id""",
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Wiki Ops Log ----

    def insert_op(self, op_type: str, target_id: str, detail: dict | None = None) -> str:
        op_id = str(uuid.uuid4())
        self._conn().execute(
            "INSERT INTO wiki_ops_log (id, op_type, target_id, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (op_id, op_type, target_id, json.dumps(detail or {}, ensure_ascii=False), datetime.now().isoformat()),
        )
        self._conn().commit()
        return op_id

    def list_ops(self, limit: int = 50) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM wiki_ops_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Wiki Workflow ----

    def insert_workflow(self, page_id: str, from_status: str, to_status: str,
                        operator: str = "system", comment: str = "") -> str:
        wf_id = str(uuid.uuid4())
        self._conn().execute(
            """INSERT INTO wiki_workflow (id, page_id, from_status, to_status, operator, comment, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (wf_id, page_id, from_status, to_status, operator, comment, datetime.now().isoformat()),
        )
        self._conn().commit()
        return wf_id

    def get_workflow_history(self, page_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM wiki_workflow WHERE page_id = ? ORDER BY created_at DESC",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Wiki Page Versions ----

    def save_version(self, page_id: str, page_data: dict) -> str:
        version_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        self._conn().execute(
            """INSERT INTO wiki_page_versions
               (id, page_id, version, title, content, concept_summary, tags, created_at)
               SELECT ?, ?, COALESCE(MAX(version), 0) + 1, ?, ?, ?, ?, ?
               FROM wiki_page_versions WHERE page_id = ?""",
            (version_id, page_id, page_data.get("title", ""),
             page_data.get("content", ""), page_data.get("concept_summary", ""),
             page_data.get("tags", "[]"), now, page_id),
        )
        self._conn().commit()
        return version_id

    def list_versions(self, page_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM wiki_page_versions WHERE page_id = ? ORDER BY version DESC",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_version(self, page_id: str, version: int) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM wiki_page_versions WHERE page_id = ? AND version = ?",
            (page_id, version),
        ).fetchone()
        return dict(row) if row else None

    def get_latest_version(self, page_id: str) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM wiki_page_versions WHERE page_id = ? ORDER BY version DESC LIMIT 1",
            (page_id,),
        ).fetchone()
        return dict(row) if row else None
