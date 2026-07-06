"""版本冲突检测 DAO — conflict_sessions / conflict_pairs / conflict_ignores"""
from datetime import datetime
from typing import Optional

from src.models.version_conflict import (
    ConflictIgnore,
    ConflictPair,
    ConflictSession,
    _make_pair_key,
)
from src.services.db import Database


class ConflictRepository:
    """三张表的 CRUD"""

    def __init__(self, db=None):
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    # ── Sessions ──

    def create_session(self, session: ConflictSession) -> None:
        self._conn().execute(
            """INSERT INTO conflict_sessions
               (id, status, total_items_scanned, candidates_found, pairs_judged,
                pairs_deleted, pairs_ignored, error, started_at, completed_at)
               VALUES (:id, :status, :total_items_scanned, :candidates_found,
                :pairs_judged, :pairs_deleted, :pairs_ignored, :error,
                :started_at, :completed_at)""",
            session.to_row(),
        )
        self._conn().commit()

    def get_session(self, session_id: str) -> Optional[ConflictSession]:
        row = self._conn().execute(
            "SELECT * FROM conflict_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return ConflictSession.from_row(dict(row)) if row else None

    def list_sessions(self, status: str | None = None,
                      limit: int = 50, offset: int = 0) -> list[ConflictSession]:
        sql = "SELECT * FROM conflict_sessions"
        params = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn().execute(sql, params).fetchall()
        return [ConflictSession.from_row(dict(r)) for r in rows]

    def update_session_status(self, session_id: str, status: str,
                              error: str | None = None,
                              completed_at: str | None = None) -> None:
        sets = ["status = ?"]
        params = [status]
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if completed_at is not None:
            sets.append("completed_at = ?")
            params.append(completed_at)
        params.append(session_id)
        self._conn().execute(
            f"UPDATE conflict_sessions SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        self._conn().commit()

    def increment_session_counter(self, session_id: str, field: str, delta: int = 1) -> None:
        """自增会话计数器。field 必须是合法字段名。"""
        allowed = {"total_items_scanned", "candidates_found",
                   "pairs_judged", "pairs_deleted", "pairs_ignored"}
        if field not in allowed:
            raise ValueError(f"Invalid counter field: {field}")
        self._conn().execute(
            f"UPDATE conflict_sessions SET {field} = {field} + ? WHERE id = ?",
            (delta, session_id),
        )
        self._conn().commit()

    # ── Pairs ──

    def create_pair(self, pair: ConflictPair) -> None:
        self._conn().execute(
            """INSERT INTO conflict_pairs
               (id, session_id, item_a_id, item_b_id, candidate_source,
                similarity_score, relation_type, newer_item_id, confidence,
                reason, status, created_at, judged_at, resolved_at)
               VALUES (:id, :session_id, :item_a_id, :item_b_id, :candidate_source,
                :similarity_score, :relation_type, :newer_item_id, :confidence,
                :reason, :status, :created_at, :judged_at, :resolved_at)""",
            pair.to_row(),
        )
        self._conn().commit()

    def create_pairs_batch(self, pairs: list[ConflictPair]) -> None:
        if not pairs:
            return
        rows = [p.to_row() for p in pairs]
        self._conn().executemany(
            """INSERT INTO conflict_pairs
               (id, session_id, item_a_id, item_b_id, candidate_source,
                similarity_score, relation_type, newer_item_id, confidence,
                reason, status, created_at, judged_at, resolved_at)
               VALUES (:id, :session_id, :item_a_id, :item_b_id, :candidate_source,
                :similarity_score, :relation_type, :newer_item_id, :confidence,
                :reason, :status, :created_at, :judged_at, :resolved_at)""",
            rows,
        )
        self._conn().commit()

    def get_pair(self, pair_id: str) -> Optional[ConflictPair]:
        row = self._conn().execute(
            "SELECT * FROM conflict_pairs WHERE id = ?", (pair_id,)
        ).fetchone()
        return ConflictPair.from_row(dict(row)) if row else None

    def list_pairs(self, session_id: str, status: str | None = None,
                   relation_type: str | None = None,
                   limit: int = 50, offset: int = 0) -> list[dict]:
        """分页查询候选对，LEFT JOIN knowledge_items 返回标题。"""
        sql = """
            SELECT cp.*,
                   ka.title AS item_a_title, ka.created_at AS item_a_created,
                   kb.title AS item_b_title, kb.created_at AS item_b_created
            FROM conflict_pairs cp
            LEFT JOIN knowledge_items ka ON ka.id = cp.item_a_id
            LEFT JOIN knowledge_items kb ON kb.id = cp.item_b_id
            WHERE cp.session_id = ?
        """
        params = [session_id]
        if status:
            sql += " AND cp.status = ?"
            params.append(status)
        if relation_type:
            sql += " AND cp.relation_type = ?"
            params.append(relation_type)
        sql += " ORDER BY cp.created_at ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn().execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def list_pending_pairs(self, session_id: str, limit: int = 20) -> list[ConflictPair]:
        rows = self._conn().execute(
            """SELECT * FROM conflict_pairs
               WHERE session_id = ? AND status = 'pending'
               ORDER BY created_at ASC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [ConflictPair.from_row(dict(r)) for r in rows]

    def update_pair_judgment(self, pair_id: str, relation_type: str,
                             newer_item_id: str | None, confidence: float,
                             reason: str) -> None:
        self._conn().execute(
            """UPDATE conflict_pairs
               SET relation_type = ?, newer_item_id = ?, confidence = ?,
                   reason = ?, judged_at = ?
               WHERE id = ?""",
            (relation_type, newer_item_id, confidence, reason,
             datetime.now().isoformat(), pair_id),
        )
        self._conn().commit()

    def update_pair_status(self, pair_id: str, status: str,
                           resolved_at: str | None = None) -> None:
        if resolved_at is None:
            resolved_at = datetime.now().isoformat()
        self._conn().execute(
            "UPDATE conflict_pairs SET status = ?, resolved_at = ? WHERE id = ?",
            (status, resolved_at, pair_id),
        )
        self._conn().commit()

    def count_pairs_by_status(self, session_id: str) -> dict[str, int]:
        rows = self._conn().execute(
            """SELECT status, COUNT(*) as cnt
               FROM conflict_pairs WHERE session_id = ?
               GROUP BY status""",
            (session_id,),
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    # ── Ignores ──

    def add_ignore(self, ignore: ConflictIgnore) -> bool:
        """添加忽略记录。同 pair_key 已存在时静默忽略（INSERT OR IGNORE）。"""
        cursor = self._conn().execute(
            """INSERT OR IGNORE INTO conflict_ignores
               (id, item_a_id, item_b_id, pair_key, ignored_at, source_pair_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ignore.id, ignore.item_a_id, ignore.item_b_id,
             ignore.pair_key or _make_pair_key(ignore.item_a_id, ignore.item_b_id),
             ignore.ignored_at, ignore.source_pair_id),
        )
        self._conn().commit()
        return cursor.rowcount > 0

    def is_ignored(self, item_a_id: str, item_b_id: str) -> bool:
        pair_key = _make_pair_key(item_a_id, item_b_id)
        row = self._conn().execute(
            "SELECT 1 FROM conflict_ignores WHERE pair_key = ? LIMIT 1",
            (pair_key,),
        ).fetchone()
        return row is not None

    def list_ignores(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """LEFT JOIN knowledge_items 返回标题。"""
        rows = self._conn().execute(
            """SELECT ci.*,
                      ka.title AS item_a_title,
                      kb.title AS item_b_title
               FROM conflict_ignores ci
               LEFT JOIN knowledge_items ka ON ka.id = ci.item_a_id
               LEFT JOIN knowledge_items kb ON kb.id = ci.item_b_id
               ORDER BY ci.ignored_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_ignore(self, ignore_id: str) -> bool:
        cursor = self._conn().execute(
            "DELETE FROM conflict_ignores WHERE id = ?", (ignore_id,)
        )
        self._conn().commit()
        return cursor.rowcount > 0
