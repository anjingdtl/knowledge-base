"""Repair corrupted database by dumping intact tables into a fresh DB.

Strategy:
  1. Create a new clean database
  2. Copy intact tables (knowledge_items, knowledge_chunks, entity_refs, etc.)
  3. Skip corrupted tables (blocks, block_fts, vec_blocks, etc.) - they'll be rebuilt by sync_all
  4. Re-create the schema for all tables
"""
import os
import sys
import sqlite3
import shutil
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

INTACT_TABLES = [
    "knowledge_items", "knowledge_versions", "knowledge_chunks",
    "knowledge_categories", "categories",
    "entity_refs", "knowledge_graphs", "knowledge_graph_nodes", "knowledge_graph_relations",
    "wiki_pages", "wiki_links", "wiki_ops_log", "wiki_page_versions",
    "users", "conversations", "chat_messages",
    "async_jobs", "embedding_cache",
    "tag_relations", "property_schemas", "effective_property_index",
]

SKIP_TABLES = [
    "blocks", "block_fts", "block_fts_config", "block_fts_content",
    "block_fts_data", "block_fts_docsize", "block_fts_idx",
    "block_property_index", "block_refs",
    "chunk_fts", "chunk_fts_config", "chunk_fts_content",
    "chunk_fts_data", "chunk_fts_docsize", "chunk_fts_idx",
    "fts_vocab",
    "knowledge_fts", "knowledge_fts_config", "knowledge_fts_data",
    "knowledge_fts_docsize", "knowledge_fts_idx",
    "vec_blocks", "vec_blocks_chunks", "vec_blocks_info",
    "vec_blocks_rowids", "vec_blocks_vector_chunks00",
    "vec_chunks", "vec_chunks_chunks", "vec_chunks_info",
    "vec_chunks_rowids", "vec_chunks_vector_chunks00",
    "sqlite_sequence",
]


def main():
    src_path = "data/kb.db"
    repaired_path = f"data/kb.db.repaired_{int(time.time())}"

    print(f"Source:      {src_path}")
    print(f"Repaired:    {repaired_path}")
    print()

    src = sqlite3.connect(src_path)
    src.row_factory = sqlite3.Row

    # Get the full schema
    schema_sqls = []
    for row in src.execute(
        "SELECT sql FROM sqlite_master WHERE type IN ('table','index','trigger','view') AND sql IS NOT NULL ORDER BY type"
    ).fetchall():
        schema_sqls.append(row["sql"])

    # Create repaired DB with full schema from Database module
    dst = sqlite3.connect(repaired_path)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA foreign_keys=ON")

    from src.services.db import Database, _SCHEMA
    Database._conn = dst
    Database._instance = None
    Database._shutdown = False
    dst.executescript(_SCHEMA)
    Database._migrate()
    dst.commit()

    # Copy intact tables
    total_rows = 0
    for table in INTACT_TABLES:
        try:
            rows = src.execute(f"SELECT * FROM [{table}]").fetchall()
            if not rows:
                continue
            cols = [d[0] for d in src.execute(f"SELECT * FROM [{table}] LIMIT 1").description]
            placeholders = ", ".join("?" for _ in cols)
            col_list = ", ".join(f"[{c}]" for c in cols)
            dst.executemany(
                f"INSERT OR IGNORE INTO [{table}] ({col_list}) VALUES ({placeholders})",
                [tuple(r) for r in rows],
            )
            total_rows += len(rows)
            print(f"  Copied {table}: {len(rows)} rows")
        except Exception as e:
            msg = str(e).encode("ascii", errors="replace").decode("ascii")[:60]
            print(f"  SKIP {table}: {msg}")

    dst.commit()
    dst.close()
    src.close()

    # Verify
    dst = sqlite3.connect(repaired_path)
    ki = dst.execute("SELECT count(*) FROM knowledge_items").fetchone()[0]
    kc = dst.execute("SELECT count(*) FROM knowledge_chunks").fetchone()[0]
    integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
    dst.close()

    print(f"\n  Total rows copied: {total_rows}")
    print(f"  knowledge_items:   {ki}")
    print(f"  knowledge_chunks:  {kc}")
    print(f"  integrity_check:   {integrity}")

    if integrity == "ok":
        backup_path = src_path + f".pre_repair_{int(time.time())}"
        shutil.copy2(src_path, backup_path)
        shutil.copy2(repaired_path, src_path)
        os.remove(repaired_path)
        print(f"\n  SUCCESS: repaired DB installed at {src_path}")
        print(f"  Backup of corrupt DB: {backup_path}")
    else:
        print(f"\n  WARNING: repaired DB still has issues: {integrity}")
        print(f"  Repaired file kept at: {repaired_path}")


if __name__ == "__main__":
    main()
