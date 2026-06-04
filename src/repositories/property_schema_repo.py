"""属性 Schema 数据访问层"""
from src.models.property_schema import PropertySchema
from src.services.db import Database


class PropertySchemaRepository:
    def __init__(self, db=None):
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    def upsert(self, schema: PropertySchema) -> PropertySchema:
        row = schema.to_row()
        self._conn().execute(
            """INSERT OR REPLACE INTO property_schemas
               (id, scope_type, scope_id, property_name, property_type,
                required, default_value, choices, constraints, created_at)
               VALUES (:id, :scope_type, :scope_id, :property_name, :property_type,
                       :required, :default_value, :choices, :constraints, :created_at)""",
            row,
        )
        self._conn().commit()
        return schema

    def list_for_scope(self, scope_type: str, scope_id: str = "") -> list[PropertySchema]:
        rows = self._conn().execute(
            "SELECT * FROM property_schemas WHERE scope_type = ? AND scope_id = ?",
            (scope_type, scope_id),
        ).fetchall()
        return [PropertySchema.from_row(dict(row)) for row in rows]

    def find(self, scope_type: str, scope_id: str, property_name: str) -> PropertySchema | None:
        row = self._conn().execute(
            "SELECT * FROM property_schemas "
            "WHERE scope_type = ? AND scope_id = ? AND property_name = ?",
            (scope_type, scope_id, property_name),
        ).fetchone()
        if row is None:
            return None
        return PropertySchema.from_row(dict(row))
