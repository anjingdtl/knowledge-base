"""Block repository for the Logseq-style block graph model."""
import json
import threading
from datetime import datetime
from typing import Optional

from src.models.block import Block, BlockProperty, BlockRef


class BlockRepository:
    def __init__(self, db=None):
        from src.services.db import Database
        self._db = db or Database
        self._write_lock = threading.Lock()

    def _conn(self):
        return self._db.get_conn()

    def upsert(self, block: Block) -> str:
        row = block.to_row()
        now = datetime.now().isoformat()
        row["created_at"] = row.get("created_at") or now
        row["updated_at"] = now
        self._conn().execute(
            """INSERT OR REPLACE INTO blocks
               (id, parent_id, page_id, content, block_type, properties, order_idx, created_at, updated_at)
               VALUES (:id, :parent_id, :page_id, :content, :block_type, :properties, :order_idx, :created_at, :updated_at)""",
            row,
        )
        self.replace_properties(block.id, block.properties)
        self._conn().commit()
        return block.id

    def get(self, block_id: str) -> Optional[Block]:
        row = self._conn().execute("SELECT * FROM blocks WHERE id = ?", (block_id,)).fetchone()
        return Block.from_row(dict(row)) if row else None

    def list_by_page(self, page_id: str, limit: int = 1000, offset: int = 0) -> list[Block]:
        rows = self._conn().execute(
            "SELECT * FROM blocks WHERE page_id = ? ORDER BY order_idx ASC, created_at ASC LIMIT ? OFFSET ?",
            (page_id, limit, offset),
        ).fetchall()
        return [Block.from_row(dict(row)) for row in rows]

    def count_by_page(self, page_id: str) -> int:
        row = self._conn().execute(
            "SELECT COUNT(*) AS cnt FROM blocks WHERE page_id = ?", (page_id,),
        ).fetchone()
        return row["cnt"] if row else 0

    def delete_by_page(self, page_id: str) -> int:
        with self._write_lock:
            rows = self._conn().execute("SELECT id FROM blocks WHERE page_id = ?", (page_id,)).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            self._conn().execute(
                f"DELETE FROM block_property_index WHERE block_id IN ({placeholders})", ids,
            )
            self._conn().execute(
                f"DELETE FROM block_refs WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
                ids + ids,
            )
            self._conn().execute(f"DELETE FROM blocks WHERE id IN ({placeholders})", ids)
            self._conn().commit()
            return len(ids)

    def replace_properties(self, block_id: str, properties: dict) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM block_property_index WHERE block_id = ?", (block_id,))
        rows = []
        for key, value in (properties or {}).items():
            rows.append(BlockProperty(
                block_id=block_id,
                prop_key=str(key),
                prop_value=json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value),
                value_type=_property_type(value),
            ).__dict__)
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO block_property_index
                   (block_id, prop_key, prop_value, value_type)
                   VALUES (:block_id, :prop_key, :prop_value, :value_type)""",
                rows,
            )

    def upsert_ref(self, ref: BlockRef) -> None:
        self._conn().execute(
            "INSERT OR REPLACE INTO block_refs (source_id, target_id, ref_type) VALUES (:source_id, :target_id, :ref_type)",
            ref.to_row(),
        )
        self._conn().commit()


def _property_type(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, (dict, list)):
        return "json"
    return "string"
