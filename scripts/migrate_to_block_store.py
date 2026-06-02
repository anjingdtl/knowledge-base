"""Block-First 向量存储迁移脚本

将现有 vec_chunks + chunk_fts 数据迁移到 vec_blocks + block_fts。

用法:
    python scripts/migrate_to_block_store.py <db_path>              # dry-run（默认）
    python scripts/migrate_to_block_store.py <db_path> --apply      # 执行迁移
    python scripts/migrate_to_block_store.py <db_path> --apply --backfill-vectors  # 迁移+向量回填
"""
import argparse
import json
import logging
import shutil
import sqlite3
import struct
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _load_sqlite_vec(conn: sqlite3.Connection):
    import sqlite_vec
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def _pack_embedding(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


def _analyze(conn: sqlite3.Connection) -> dict:
    stats = {}
    for table in ["knowledge_chunks", "blocks", "vec_chunks", "chunk_fts"]:
        try:
            row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            stats[table] = row[0] if row else 0
        except Exception:
            stats[table] = -1
    for table in ["vec_blocks", "block_fts"]:
        try:
            row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            stats[table] = row[0] if row else 0
        except Exception:
            stats[table] = -1
    return stats


def _backup_database(db_path: str) -> str:
    backup_dir = Path(db_path).parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"kb_{timestamp}.db"
    shutil.copy2(db_path, backup_path)
    logger.info(f"Backup created: {backup_path}")
    return str(backup_path)


def _create_schema(conn: sqlite3.Connection, dimension: int = 1024):
    _load_sqlite_vec(conn)
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_blocks USING vec0("
        f"embedding float[{dimension}] distance_metric=cosine)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS block_fts USING fts5("
        "fts_segmented, page_id UNINDEXED, block_id UNINDEXED, tokenize='unicode61')"
    )
    conn.commit()
    logger.info("Schema created: vec_blocks, block_fts")


def _migrate_blocks(conn: sqlite3.Connection):
    rows = conn.execute(
        """SELECT kc.id, kc.knowledge_id, kc.chunk_index, kc.chunk_text, kc.created_at
           FROM knowledge_chunks kc
           WHERE kc.id NOT IN (SELECT id FROM blocks)"""
    ).fetchall()
    if not rows:
        logger.info("No missing blocks to migrate")
        return 0

    now = datetime.now().isoformat()
    count = 0
    for row in rows:
        chunk_id, knowledge_id, chunk_index, chunk_text, created_at = row
        created_at = created_at or now
        conn.execute(
            """INSERT OR IGNORE INTO blocks
               (id, parent_id, page_id, content, block_type, properties, order_idx, created_at, updated_at)
               VALUES (?, NULL, ?, ?, 'text', ?, ?, ?, ?)""",
            (chunk_id, knowledge_id, chunk_text,
             json.dumps({"knowledge_id": knowledge_id, "chunk_index": chunk_index or 0}, ensure_ascii=False),
             chunk_index or 0, created_at, created_at),
        )
        conn.execute(
            """INSERT OR IGNORE INTO block_property_index
               (block_id, prop_key, prop_value, value_type)
               VALUES (?, 'knowledge_id', ?, 'ref')""",
            (chunk_id, knowledge_id),
        )
        count += 1
    conn.commit()
    logger.info(f"Migrated {count} blocks")
    return count


def _migrate_vectors(conn: sqlite3.Connection):
    _load_sqlite_vec(conn)
    rows = conn.execute(
        """SELECT kc.id, vc.embedding
           FROM vec_chunks vc
           JOIN knowledge_chunks kc ON kc.rowid = vc.rowid
           WHERE kc.id NOT IN (
               SELECT b.id FROM blocks b
               JOIN vec_blocks vb ON vb.rowid = b.rowid
           )"""
    ).fetchall()
    if not rows:
        logger.info("No missing vectors to migrate")
        return 0

    count = 0
    for chunk_id, embedding_blob in rows:
        block_row = conn.execute(
            "SELECT rowid FROM blocks WHERE id = ?", (chunk_id,)
        ).fetchone()
        if block_row:
            conn.execute(
                "INSERT OR REPLACE INTO vec_blocks(rowid, embedding) VALUES (?, ?)",
                (block_row[0], embedding_blob),
            )
            count += 1
    conn.commit()
    logger.info(f"Migrated {count} vectors")
    return count


def _migrate_fts(conn: sqlite3.Connection):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.utils.chinese_tokenizer import tokenize_chinese_full

    rows = conn.execute(
        """SELECT b.id, b.page_id, b.content FROM blocks b
           WHERE b.id NOT IN (SELECT block_id FROM block_fts)"""
    ).fetchall()
    if not rows:
        logger.info("No missing FTS entries to migrate")
        return 0

    count = 0
    for block_id, page_id, content in rows:
        segmented = tokenize_chinese_full(content or "")
        conn.execute(
            "INSERT INTO block_fts(fts_segmented, page_id, block_id) VALUES (?, ?, ?)",
            (segmented, page_id, block_id),
        )
        count += 1
    conn.commit()
    logger.info(f"Migrated {count} FTS entries")
    return count


def _backfill_vectors(conn: sqlite3.Connection):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.services.embedding import EmbeddingService

    rows = conn.execute(
        """SELECT b.id, b.content FROM blocks b
           WHERE b.rowid NOT IN (SELECT rowid FROM vec_blocks)"""
    ).fetchall()
    if not rows:
        logger.info("No missing vectors to backfill")
        return 0

    _load_sqlite_vec(conn)
    svc = EmbeddingService()
    count = 0
    batch_size = 20
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        texts = [r[1] for r in batch]
        try:
            embeddings = svc.embed_batch(texts)
            for (block_id, _), emb in zip(batch, embeddings):
                block_row = conn.execute(
                    "SELECT rowid FROM blocks WHERE id = ?", (block_id,)
                ).fetchone()
                if block_row:
                    conn.execute(
                        "INSERT OR REPLACE INTO vec_blocks(rowid, embedding) VALUES (?, ?)",
                        (block_row[0], _pack_embedding(emb)),
                    )
                    count += 1
        except Exception as e:
            logger.error(f"Backfill failed for batch {i}: {e}")
    conn.commit()
    logger.info(f"Backfilled {count} vectors via API")
    return count


def _verify(conn: sqlite3.Connection) -> bool:
    stats = _analyze(conn)
    ok = True
    blocks = stats.get("blocks", 0)
    vec_blocks = stats.get("vec_blocks", 0)
    block_fts = stats.get("block_fts", 0)

    if blocks > 0 and vec_blocks == 0:
        logger.warning(f"MISMATCH: {blocks} blocks but 0 vec_blocks")
        ok = False
    elif blocks > 0 and abs(blocks - vec_blocks) > blocks * 0.1:
        logger.warning(f"MISMATCH: {blocks} blocks vs {vec_blocks} vec_blocks (>10% diff)")
        ok = False
    else:
        logger.info(f"OK: {blocks} blocks, {vec_blocks} vec_blocks, {block_fts} block_fts")

    return ok


def main():
    parser = argparse.ArgumentParser(description="Block-First vector store migration")
    parser.add_argument("db_path", help="Path to SQLite database file")
    parser.add_argument("--apply", action="store_true", help="Execute migration (default: dry-run)")
    parser.add_argument("--backfill-vectors", action="store_true", help="Backfill missing vectors via API")
    parser.add_argument("--no-backup", action="store_true", help="Skip database backup")
    parser.add_argument("--dimension", type=int, default=1024, help="Vector dimension (default: 1024)")
    args = parser.parse_args()

    db_path = args.db_path
    if not Path(db_path).exists():
        logger.error(f"Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    stats = _analyze(conn)
    logger.info("Current state:")
    for table, count in stats.items():
        logger.info(f"  {table}: {count}")

    if not args.apply:
        logger.info("Dry-run complete. Use --apply to execute migration.")
        conn.close()
        return

    if not args.no_backup:
        _backup_database(db_path)

    _create_schema(conn, args.dimension)
    _migrate_blocks(conn)
    _migrate_vectors(conn)
    _migrate_fts(conn)

    if args.backfill_vectors:
        _backfill_vectors(conn)

    ok = _verify(conn)
    conn.close()

    if ok:
        logger.info("Migration completed successfully!")
    else:
        logger.warning("Migration completed with warnings. Check the output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
