"""Repository for cross-entity references in the block graph."""
from datetime import datetime
from typing import Optional

from src.models.block import EntityRef


class EntityRefRepository:
    def __init__(self, db=None):
        from src.services.db import Database
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    def upsert(self, ref: EntityRef) -> str:
        row = ref.to_row()
        row["created_at"] = row.get("created_at") or datetime.now().isoformat()
        self._conn().execute(
            """INSERT INTO entity_refs
               (id, source_type, source_id, target_type, target_id, ref_type, weight, auto_discovered, created_at)
               VALUES (:id, :source_type, :source_id, :target_type, :target_id, :ref_type, :weight, :auto_discovered, :created_at)
               ON CONFLICT(source_type, source_id, target_type, target_id, ref_type)
               DO UPDATE SET weight=excluded.weight""",
            row,
        )
        self._conn().commit()
        return ref.id

    def list_for_source(self, source_type: str, source_id: str) -> list[EntityRef]:
        return self._list("source_type = ? AND source_id = ?", [source_type, source_id])

    def list_for_target(self, target_type: str, target_id: str) -> list[EntityRef]:
        return self._list("target_type = ? AND target_id = ?", [target_type, target_id])

    def list_refs(
        self,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        limit: int = 200,
    ) -> list[EntityRef]:
        conditions = []
        params = []
        if source_type:
            conditions.append("source_type = ?")
            params.append(source_type)
        if source_id:
            conditions.append("source_id = ?")
            params.append(source_id)
        if target_type:
            conditions.append("target_type = ?")
            params.append(target_type)
        if target_id:
            conditions.append("target_id = ?")
            params.append(target_id)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self._conn().execute(
            f"SELECT * FROM entity_refs{where} ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [EntityRef(**dict(row)) for row in rows]

    def delete_for_entity(self, entity_type: str, entity_id: str) -> int:
        cursor = self._conn().execute(
            """DELETE FROM entity_refs
               WHERE (source_type = ? AND source_id = ?)
                  OR (target_type = ? AND target_id = ?)""",
            (entity_type, entity_id, entity_type, entity_id),
        )
        self._conn().commit()
        return int(cursor.rowcount)

    def delete_auto_discovered_for_source(self, source_type: str, source_id: str) -> int:
        cursor = self._conn().execute(
            """DELETE FROM entity_refs
               WHERE source_type = ? AND source_id = ? AND auto_discovered = 1""",
            (source_type, source_id),
        )
        self._conn().commit()
        return int(cursor.rowcount)

    def _list(self, where: str, params: list) -> list[EntityRef]:
        rows = self._conn().execute(
            f"SELECT * FROM entity_refs WHERE {where} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [EntityRef(**dict(row)) for row in rows]
