import json
from datetime import datetime

from src.services.db import Database
from src.services.property_schema import PropertySchemaService


class EffectivePropertyService:
    def __init__(self, db=None, schema_service=None):
        self._db = db or Database
        self._schemas = schema_service or PropertySchemaService(db=self._db)

    def refresh_page(self, page_id: str) -> int:
        """Refresh effective properties for all blocks on a page. Returns block count."""
        rows = self._db.get_conn().execute("SELECT id FROM blocks WHERE page_id = ?", (page_id,)).fetchall()
        all_props = []
        for row in rows:
            effective = self._compute_effective_for_block(row["id"])
            if effective:
                all_props.append((row["id"], effective))
        conn = self._db.get_conn()
        if all_props:
            block_ids = [bid for bid, _ in all_props]
            placeholders = ",".join("?" for _ in block_ids)
            conn.execute(f"DELETE FROM effective_property_index WHERE block_id IN ({placeholders})", block_ids)
        now = datetime.now().isoformat()
        insert_rows = []
        for block_id, effective in all_props:
            for key, data in effective.items():
                insert_rows.append({
                    "block_id": block_id,
                    "prop_key": key,
                    "prop_value": self._string_value(data["value"]),
                    "value_type": data["value_type"],
                    "source_type": data["source_type"],
                    "source_id": data["source_id"],
                    "inherited": data["inherited"],
                    "updated_at": now,
                })
        if insert_rows:
            conn.executemany(
                """INSERT OR REPLACE INTO effective_property_index
                   (block_id, prop_key, prop_value, value_type, source_type, source_id, inherited, updated_at)
                   VALUES (:block_id, :prop_key, :prop_value, :value_type, :source_type, :source_id, :inherited, :updated_at)""",
                insert_rows,
            )
        conn.commit()
        return len(rows)

    def _compute_effective_for_block(self, block_id: str) -> dict:
        block = self._db.get_block(block_id)
        if not block:
            return {}
        page = self._db.get_knowledge(block["page_id"]) if block.get("page_id") else None
        tags = self._load_tags(page.get("tags") if page else "[]")
        explicit = self._load_props(block.get("properties"))

        effective: dict[str, dict] = {}
        self._apply_scope(effective, "global", "", [], block_id)
        for tag in tags:
            self._apply_scope(effective, "tag", tag, tags, block_id)
        if page:
            self._apply_scope(effective, "page", page["id"], tags, block_id)
        for key, value in explicit.items():
            effective[key] = {
                "value": value,
                "value_type": self._value_type(value),
                "source_type": "block",
                "source_id": block_id,
                "inherited": 0,
            }
        return effective

    def refresh_block(self, block_id: str) -> dict:
        """Compute and write effective properties for a single block."""
        block = self._db.get_block(block_id)
        if not block:
            return {}
        page = self._db.get_knowledge(block["page_id"]) if block.get("page_id") else None
        tags = self._load_tags(page.get("tags") if page else "[]")
        explicit = self._load_props(block.get("properties"))

        effective: dict[str, dict] = {}
        # Apply schemas in precedence order: global -> tag -> page -> block(explicit)
        self._apply_scope(effective, "global", "", [], block_id)
        for tag in tags:
            self._apply_scope(effective, "tag", tag, tags, block_id)
        if page:
            self._apply_scope(effective, "page", page["id"], tags, block_id)
        # Block explicit properties always win
        for key, value in explicit.items():
            effective[key] = {
                "value": value,
                "value_type": self._value_type(value),
                "source_type": "block",
                "source_id": block_id,
                "inherited": 0,
            }
        self._write_index(block_id, effective)
        return effective

    def _apply_scope(self, effective, scope_type, scope_id, tags, block_id):
        schemas = self._schemas._repo.list_for_scope(scope_type, scope_id)
        for schema in schemas:
            if schema.default_value is None:
                continue
            effective[schema.property_name] = {
                "value": schema.default_value,
                "value_type": schema.property_type,
                "source_type": scope_type,
                "source_id": scope_id,
                "inherited": 1,
            }

    def _write_index(self, block_id, effective):
        conn = self._db.get_conn()
        conn.execute("DELETE FROM effective_property_index WHERE block_id = ?", (block_id,))
        now = datetime.now().isoformat()
        rows = []
        for key, data in effective.items():
            rows.append({
                "block_id": block_id,
                "prop_key": key,
                "prop_value": self._string_value(data["value"]),
                "value_type": data["value_type"],
                "source_type": data["source_type"],
                "source_id": data["source_id"],
                "inherited": data["inherited"],
                "updated_at": now,
            })
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO effective_property_index
                   (block_id, prop_key, prop_value, value_type, source_type, source_id, inherited, updated_at)
                   VALUES (:block_id, :prop_key, :prop_value, :value_type, :source_type, :source_id, :inherited, :updated_at)""",
                rows,
            )
        conn.commit()

    def _load_props(self, value) -> dict:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}

    def _load_tags(self, value) -> list[str]:
        try:
            parsed = json.loads(value or "[]") if isinstance(value, str) else value
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []

    def _value_type(self, value) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        return "text"

    def _string_value(self, value) -> str:
        return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
