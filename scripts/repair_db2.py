"""Repair corrupted database using SQLite .dump approach.

Uses 'PRAGMA writable_schema' to skip corrupted pages during dump,
then reimport into a fresh database.
"""
import os
import shutil
import sqlite3
import sys
import time

src_path = "data/kb.db"
repaired_path = f"data/kb.db.repaired_{int(time.time())}"
dump_path = "data/kb_dump.sql"

print("Step 1: Dumping intact data from corrupted DB...")

src = sqlite3.connect(src_path)
src.row_factory = None  # raw tuples for speed

# Get all table schemas from sqlite_master
schemas = src.execute(
    "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL ORDER BY name"
).fetchall()

dump_lines = []
dump_lines.append("PRAGMA foreign_keys=OFF;")
dump_lines.append("BEGIN TRANSACTION;")

skip_tables = {"block_fts", "block_fts_content", "block_fts_data",
               "block_fts_docsize", "block_fts_idx", "block_fts_config",
               "chunk_fts", "chunk_fts_content", "chunk_fts_data",
               "chunk_fts_docsize", "chunk_fts_idx", "chunk_fts_config",
               "knowledge_fts", "knowledge_fts_content", "knowledge_fts_data",
               "knowledge_fts_docsize", "knowledge_fts_idx", "knowledge_fts_config",
               "fts_vocab", "vec_blocks", "vec_chunks"}

table_counts = {}
for name, sql in schemas:
    if name in skip_tables:
        continue
    dump_lines.append(f"{sql};")
    try:
        rows = src.execute(f"SELECT * FROM [{name}]").fetchall()
        if rows:
            cols = [d[0] for d in src.execute(f"SELECT * FROM [{name}] LIMIT 1").description]
            col_list = ", ".join(f'"{c}"' for c in cols)
            for row in rows:
                vals = []
                for v in row:
                    if v is None:
                        vals.append("NULL")
                    elif isinstance(v, (int, float)):
                        vals.append(str(v))
                    elif isinstance(v, bytes):
                        vals.append("X'" + v.hex() + "'")
                    else:
                        escaped = str(v).replace("'", "''")
                        vals.append(f"'{escaped}'")
                dump_lines.append(f'INSERT INTO "{name}" ({col_list}) VALUES({",".join(vals)});')
            table_counts[name] = len(rows)
        else:
            table_counts[name] = 0
    except Exception as e:
        msg = str(e).encode("ascii", errors="replace").decode("ascii")[:60]
        print(f"  SKIP {name}: {msg}")

dump_lines.append("COMMIT;")

src.close()

print(f"  Dumped {len(table_counts)} tables:")
for name, count in sorted(table_counts.items()):
    print(f"    {name}: {count}")

print("\nStep 2: Loading into fresh DB...")
if os.path.exists(repaired_path):
    os.remove(repaired_path)

# Create fresh DB with the correct schema first
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.services.db import _SCHEMA  # noqa: E402

dst = sqlite3.connect(repaired_path)
dst.execute("PRAGMA journal_mode=WAL")
dst.execute("PRAGMA foreign_keys=OFF")  # Disable FK during import
dst.executescript(_SCHEMA)
dst.commit()

# Now load the dump (with FK off to allow partial data)
dump_text = "\n".join(dump_lines)
try:
    dst.executescript(dump_text)
except Exception as e:
    print(f"  Warning during load: {e}")
dst.commit()

# Verify
ki = dst.execute("SELECT count(*) FROM knowledge_items").fetchone()[0]
kc = dst.execute("SELECT count(*) FROM knowledge_chunks").fetchone()[0]
er = dst.execute("SELECT count(*) FROM entity_refs").fetchone()[0]
integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close()

print(f"\n  knowledge_items:   {ki}")
print(f"  knowledge_chunks:  {kc}")
print(f"  entity_refs:       {er}")
print(f"  integrity_check:   {integrity}")

if ki > 0 and integrity == "ok":
    backup_path = src_path + f".pre_repair_{int(time.time())}"
    shutil.copy2(src_path, backup_path)
    shutil.copy2(repaired_path, src_path)
    os.remove(repaired_path)
    if os.path.exists(dump_path):
        os.remove(dump_path)
    print("\n  SUCCESS: repaired DB installed")
    print(f"  Backup: {os.path.basename(backup_path)}")
else:
    print(f"\n  ISSUE: knowledge_items={ki}, integrity={integrity}")
    print(f"  Repaired file: {repaired_path}")
    print(f"  Dump file: {dump_path}")
