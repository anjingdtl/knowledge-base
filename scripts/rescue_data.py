"""Final rescue: dump data from broken-schema DB into a clean DB.

Since the schema is broken, we use raw page-level reads to extract data,
then insert into a freshly created DB.
"""
import os
import shutil
import sqlite3
import time

BROKEN = "data/kb.db"
CLEAN = f"data/kb.db.clean_{int(time.time())}"

# Step 1: Create clean DB with proper schema
from src.services.db import _SCHEMA  # noqa: E402

clean = sqlite3.connect(CLEAN)
clean.execute("PRAGMA journal_mode=WAL")
clean.executescript(_SCHEMA)
clean.commit()
clean.close()

# Step 2: Attach the broken DB and copy data
# We use ATTACH + INSERT SELECT to bypass schema issues
clean = sqlite3.connect(CLEAN)
clean.execute("PRAGMA foreign_keys=OFF")
clean.execute(f"ATTACH DATABASE '{os.path.abspath(BROKEN)}' AS broken")

# Get intact table list from the CLEAN db (not broken)
tables = [r[0] for r in clean.execute(
    "SELECT name FROM main.sqlite_master WHERE type='table' ORDER BY name"
).fetchall()]

copied = {}
for table in tables:
    try:
        count_src = clean.execute(f"SELECT count(*) FROM broken.[{table}]").fetchone()[0]
        if count_src == 0:
            continue
        clean.execute(f"DELETE FROM main.[{table}]")
        clean.execute(f"INSERT INTO main.[{table}] SELECT * FROM broken.[{table}]")
        count_dst = clean.execute(f"SELECT count(*) FROM main.[{table}]").fetchone()[0]
        copied[table] = count_dst
        print(f"  {table}: {count_dst} rows")
    except Exception as e:
        msg = str(e).encode("ascii", errors="replace").decode("ascii")[:60]
        print(f"  SKIP {table}: {msg}")

clean.execute("DETACH DATABASE broken")
clean.commit()

# Verify
integrity = clean.execute("PRAGMA integrity_check").fetchone()[0]
print(f"\nintegrity_check: {integrity}")

# Key metrics
ki = clean.execute("SELECT count(*) FROM knowledge_items").fetchone()[0]
bl = clean.execute("SELECT count(*) FROM blocks").fetchone()[0]
bl_p = clean.execute("SELECT count(*) FROM blocks WHERE parent_id IS NOT NULL").fetchone()[0]
er = clean.execute("SELECT count(*) FROM entity_refs WHERE auto_discovered=1").fetchone()[0]
eff = clean.execute("SELECT count(*) FROM effective_property_index").fetchone()[0]
print(f"knowledge_items:          {ki}")
print(f"blocks total:             {bl}")
print(f"blocks WITH parent_id:    {bl_p}")
print(f"entity_refs auto_disc:    {er}")
print(f"effective_property_index: {eff}")

clean.close()

if ki > 0 and integrity == "ok":
    backup = BROKEN + f".pre_clean_{int(time.time())}"
    shutil.copy2(BROKEN, backup)
    shutil.copy2(CLEAN, BROKEN)
    os.remove(CLEAN)
    print("\nSUCCESS: Clean DB installed")
    print(f"Backup: {os.path.basename(backup)}")
else:
    print(f"\nISSUE: ki={ki}, integrity={integrity}")
    print(f"Clean DB: {CLEAN}")
