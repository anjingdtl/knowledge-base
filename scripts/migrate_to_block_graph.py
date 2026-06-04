"""Migrate existing knowledge data into the block graph tables.

This script is intentionally additive: it keeps knowledge_items,
knowledge_chunks, chunk_fts, and vec_chunks intact, then mirrors chunks into
blocks and materializes existing wiki/graph relationships into entity_refs.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sqlite3
import struct
from datetime import datetime
from pathlib import Path


BLOCK_GRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    id TEXT PRIMARY KEY,
    parent_id TEXT REFERENCES blocks(id) ON DELETE CASCADE,
    page_id TEXT,
    content TEXT,
    block_type TEXT DEFAULT 'text',
    properties TEXT DEFAULT '{}',
    order_idx INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_blocks_page ON blocks(page_id);
CREATE INDEX IF NOT EXISTS idx_blocks_parent ON blocks(parent_id);

CREATE TABLE IF NOT EXISTS block_refs (
    source_id TEXT REFERENCES blocks(id) ON DELETE CASCADE,
    target_id TEXT REFERENCES blocks(id) ON DELETE CASCADE,
    ref_type TEXT DEFAULT 'link',
    PRIMARY KEY (source_id, target_id, ref_type)
);

CREATE TABLE IF NOT EXISTS entity_refs (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    ref_type TEXT DEFAULT 'mention',
    weight REAL DEFAULT 1.0,
    auto_discovered INTEGER DEFAULT 0,
    created_at TEXT,
    UNIQUE(source_type, source_id, target_type, target_id, ref_type)
);
CREATE INDEX IF NOT EXISTS idx_entity_refs_source ON entity_refs(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_entity_refs_target ON entity_refs(target_type, target_id);

CREATE TABLE IF NOT EXISTS block_property_index (
    block_id TEXT REFERENCES blocks(id) ON DELETE CASCADE,
    prop_key TEXT,
    prop_value TEXT,
    value_type TEXT DEFAULT 'string',
    PRIMARY KEY (block_id, prop_key)
);
CREATE INDEX IF NOT EXISTS idx_prop_key_val ON block_property_index(prop_key, prop_value);

-- Phase 2: Tag DAG, Property Schema, Effective Properties
CREATE TABLE IF NOT EXISTS tag_relations (
    parent_tag TEXT NOT NULL,
    child_tag TEXT NOT NULL,
    created_at TEXT,
    PRIMARY KEY (parent_tag, child_tag),
    CHECK(parent_tag <> child_tag)
);
CREATE INDEX IF NOT EXISTS idx_tag_relations_parent ON tag_relations(parent_tag);
CREATE INDEX IF NOT EXISTS idx_tag_relations_child ON tag_relations(child_tag);

CREATE TABLE IF NOT EXISTS property_schemas (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT DEFAULT '',
    property_name TEXT NOT NULL,
    property_type TEXT NOT NULL,
    required INTEGER DEFAULT 0,
    default_value TEXT,
    choices TEXT,
    constraints TEXT,
    created_at TEXT,
    UNIQUE(scope_type, scope_id, property_name)
);
CREATE INDEX IF NOT EXISTS idx_property_schemas_scope ON property_schemas(scope_type, scope_id);

CREATE TABLE IF NOT EXISTS effective_property_index (
    block_id TEXT REFERENCES blocks(id) ON DELETE CASCADE,
    prop_key TEXT NOT NULL,
    prop_value TEXT,
    value_type TEXT DEFAULT 'string',
    source_type TEXT NOT NULL,
    source_id TEXT DEFAULT '',
    inherited INTEGER DEFAULT 0,
    updated_at TEXT,
    PRIMARY KEY (block_id, prop_key)
);
CREATE INDEX IF NOT EXISTS idx_effective_prop_key_val ON effective_property_index(prop_key, prop_value);

CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    embedding BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (content_hash, model)
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    hashed TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def migrate_database(
    db_path: str | Path,
    *,
    apply: bool = False,
    backfill_missing_vectors: bool = False,
    backup: bool = True,
) -> dict:
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    backup_path = None
    if apply and backup:
        backup_path = _backup_database(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        summary = _analyze(conn)
        if not apply:
            return summary

        conn.executescript(BLOCK_GRAPH_SCHEMA)
        _ensure_block_graph_columns(conn)
        conn.commit()

        created_blocks = _migrate_blocks(conn)
        created_entity_refs = _migrate_entity_refs(conn)
        backfilled_vectors = (
            _backfill_missing_vectors(conn) if backfill_missing_vectors else 0
        )
        conn.commit()

        after = _analyze(conn)
        return {
            **after,
            "created_blocks": created_blocks,
            "created_entity_refs": created_entity_refs,
            "backfilled_vectors": backfilled_vectors,
            "backup_path": str(backup_path) if backup_path else None,
        }
    finally:
        conn.close()


def _analyze(conn: sqlite3.Connection) -> dict:
    chunk_count = _count(conn, "knowledge_chunks")
    block_count = _count(conn, "blocks")
    wiki_link_count = _count(conn, "wiki_links")
    graph_rel_count = _count(conn, "knowledge_graph_relations")
    entity_ref_count = _count(conn, "entity_refs")
    missing_vectors = _count_missing_vectors(conn)
    return {
        "chunks": chunk_count,
        "blocks": block_count,
        "entity_refs": entity_ref_count,
        "would_create_blocks": _missing_block_count(conn),
        "would_create_entity_refs": _missing_entity_ref_count(conn),
        "wiki_links": wiki_link_count,
        "graph_relations": graph_rel_count,
        "missing_vectors": missing_vectors,
    }


def _backup_database(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"kb-pre-migration-{datetime.now().strftime('%Y%m%d%H%M%S')}.db"
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(backup_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return backup_path


def _migrate_blocks(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """SELECT id, knowledge_id, chunk_index, chunk_text, created_at
           FROM knowledge_chunks
           WHERE id NOT IN (SELECT id FROM blocks)
           ORDER BY knowledge_id, chunk_index"""
    ).fetchall()
    created = 0
    for row in rows:
        created_at = row["created_at"] or datetime.now().isoformat()
        conn.execute(
            """INSERT OR IGNORE INTO blocks
               (id, parent_id, page_id, content, block_type, properties, order_idx, created_at, updated_at)
               VALUES (?, NULL, ?, ?, 'text', ?, ?, ?, ?)""",
            (
                row["id"],
                row["knowledge_id"],
                row["chunk_text"],
                _chunk_properties(row["knowledge_id"], row["chunk_index"]),
                row["chunk_index"] or 0,
                created_at,
                created_at,
            ),
        )
        if conn.execute("SELECT changes()").fetchone()[0]:
            created += 1
        conn.execute(
            """INSERT OR REPLACE INTO block_property_index
               (block_id, prop_key, prop_value, value_type)
               VALUES (?, 'knowledge_id', ?, 'ref')""",
            (row["id"], row["knowledge_id"]),
        )
    return created


def _migrate_entity_refs(conn: sqlite3.Connection) -> int:
    created = 0
    if _table_exists(conn, "wiki_links"):
        rows = conn.execute(
            "SELECT source_page_id, target_page_id, link_type, weight FROM wiki_links"
        ).fetchall()
        for row in rows:
            created += _insert_entity_ref(
                conn,
                source_type="wiki",
                source_id=row["source_page_id"],
                target_type="wiki",
                target_id=row["target_page_id"],
                ref_type=row["link_type"] or "related",
                weight=row["weight"] if row["weight"] is not None else 1.0,
            )
    if _table_exists(conn, "knowledge_graph_relations"):
        rows = conn.execute(
            """SELECT graph_id, source_knowledge_id, target_knowledge_id, relation_type, weight
               FROM knowledge_graph_relations"""
        ).fetchall()
        for row in rows:
            created += _insert_entity_ref(
                conn,
                source_type="knowledge",
                source_id=row["source_knowledge_id"],
                target_type="knowledge",
                target_id=row["target_knowledge_id"],
                ref_type=row["relation_type"] or "related",
                weight=row["weight"] if row["weight"] is not None else 1.0,
                namespace=row["graph_id"],
            )
    return created


def _insert_entity_ref(
    conn: sqlite3.Connection,
    *,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    ref_type: str,
    weight: float,
    namespace: str = "",
) -> int:
    ref_id = _stable_ref_id(source_type, source_id, target_type, target_id, ref_type, namespace)
    conn.execute(
        """INSERT OR IGNORE INTO entity_refs
           (id, source_type, source_id, target_type, target_id, ref_type, weight, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ref_id,
            source_type,
            source_id,
            target_type,
            target_id,
            ref_type,
            weight,
            datetime.now().isoformat(),
        ),
    )
    return int(conn.execute("SELECT changes()").fetchone()[0])


def _ensure_block_graph_columns(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "entity_refs"):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(entity_refs)").fetchall()}
        if "auto_discovered" not in cols:
            conn.execute("ALTER TABLE entity_refs ADD COLUMN auto_discovered INTEGER DEFAULT 0")


def _backfill_missing_vectors(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "vec_chunks"):
        return 0
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except Exception:
        return 0
    finally:
        try:
            conn.enable_load_extension(False)
        except Exception:
            pass

    missing = conn.execute(
        """SELECT kc.rowid, kc.chunk_text
           FROM knowledge_chunks kc
           LEFT JOIN vec_chunks vc ON vc.rowid = kc.rowid
           WHERE vc.rowid IS NULL
           ORDER BY kc.rowid"""
    ).fetchall()
    if not missing:
        return 0

    from src.services.embedding import EmbeddingService
    embedder = EmbeddingService()
    inserted = 0
    logger = logging.getLogger(__name__)
    for row in missing:
        text = (row["chunk_text"] or "")[:8000]
        try:
            embeddings = embedder.embed_batch([text], batch_size=1)
        except Exception as exc:
            logger.warning("Vector backfill failed for rowid %s: %s", row["rowid"], exc)
            continue
        if not embeddings:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
            (row["rowid"], _pack_embedding(embeddings[0])),
        )
        inserted += 1
    return inserted


def _missing_block_count(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "knowledge_chunks"):
        return 0
    if not _table_exists(conn, "blocks"):
        return _count(conn, "knowledge_chunks")
    row = conn.execute(
        "SELECT COUNT(*) FROM knowledge_chunks WHERE id NOT IN (SELECT id FROM blocks)"
    ).fetchone()
    return row[0] if row else 0


def _missing_entity_ref_count(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "entity_refs"):
        return _count(conn, "wiki_links") + _count(conn, "knowledge_graph_relations")
    missing = 0
    if _table_exists(conn, "wiki_links"):
        rows = conn.execute("SELECT source_page_id, target_page_id, link_type FROM wiki_links").fetchall()
        for row in rows:
            ref_id = _stable_ref_id("wiki", row["source_page_id"], "wiki", row["target_page_id"], row["link_type"] or "related", "")
            missing += 0 if _exists(conn, "entity_refs", ref_id) else 1
    if _table_exists(conn, "knowledge_graph_relations"):
        rows = conn.execute(
            "SELECT graph_id, source_knowledge_id, target_knowledge_id, relation_type FROM knowledge_graph_relations"
        ).fetchall()
        for row in rows:
            ref_id = _stable_ref_id(
                "knowledge",
                row["source_knowledge_id"],
                "knowledge",
                row["target_knowledge_id"],
                row["relation_type"] or "related",
                row["graph_id"],
            )
            missing += 0 if _exists(conn, "entity_refs", ref_id) else 1
    return missing


def _count_missing_vectors(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "knowledge_chunks") or not _table_exists(conn, "vec_chunks"):
        return 0
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        row = conn.execute(
            """SELECT COUNT(*)
               FROM knowledge_chunks kc
               LEFT JOIN vec_chunks vc ON vc.rowid = kc.rowid
               WHERE vc.rowid IS NULL"""
        ).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
    finally:
        try:
            conn.enable_load_extension(False)
        except Exception:
            pass


def _chunk_properties(knowledge_id: str, chunk_index: int | None) -> str:
    import json
    return json.dumps({
        "knowledge_id": knowledge_id,
        "chunk_index": chunk_index or 0,
    }, ensure_ascii=False)


def _pack_embedding(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


def _stable_ref_id(*parts: str) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return "ref-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _exists(conn: sqlite3.Connection, table: str, item_id: str) -> bool:
    row = conn.execute(f"SELECT 1 FROM {table} WHERE id = ? LIMIT 1", (item_id,)).fetchone()
    return row is not None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return row[0] if row else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate knowledge chunks into block graph tables.")
    parser.add_argument("db_path", type=Path, help="Path to kb.db")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply migration. Omit for dry-run.")
    mode.add_argument("--dry-run", action="store_true", help="Analyze migration without changing data. This is the default.")
    parser.add_argument("--no-backup", action="store_true", help="Skip SQLite backup before apply.")
    parser.add_argument("--backfill-missing-vectors", action="store_true", help="Call embedding API for missing vec_chunks only.")
    args = parser.parse_args()
    result = migrate_database(
        args.db_path,
        apply=args.apply,
        backfill_missing_vectors=args.backfill_missing_vectors,
        backup=not args.no_backup,
    )
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
