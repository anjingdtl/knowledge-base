"""标签父子关系数据访问层"""
from src.models.tag_relation import TagRelation
from src.services.db import Database


class TagRelationRepository:
    def __init__(self, db=None):
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    def upsert(self, relation: TagRelation) -> None:
        row = relation.to_row()
        self._conn().execute(
            "INSERT OR REPLACE INTO tag_relations (parent_tag, child_tag, created_at) "
            "VALUES (:parent_tag, :child_tag, :created_at)",
            row,
        )
        self._conn().commit()

    def delete(self, parent_tag: str, child_tag: str) -> int:
        cursor = self._conn().execute(
            "DELETE FROM tag_relations WHERE parent_tag = ? AND child_tag = ?",
            (parent_tag, child_tag),
        )
        return int(cursor.rowcount)

    def list_all(self) -> list[TagRelation]:
        rows = self._conn().execute(
            "SELECT parent_tag, child_tag, created_at "
            "FROM tag_relations ORDER BY parent_tag, child_tag"
        ).fetchall()
        return [TagRelation.from_row(dict(row)) for row in rows]
