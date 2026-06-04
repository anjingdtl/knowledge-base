"""Fix malformed schema by reading raw sqlite_master and repairing it."""
import sqlite3

c = sqlite3.connect("data/kb.db")

# Enable writing to sqlite_master
c.execute("PRAGMA writable_schema=ON")

# Read raw schema entries
rows = c.execute("SELECT rowid, type, name, sql FROM sqlite_master").fetchall()
print(f"Found {len(rows)} schema entries")
for row in rows:
    rid, typ, name, sql = row
    if sql and "?" in sql:
        print(f"  BROKEN rowid={rid} {typ} {name}: sql has '?' placeholder")
        c.execute("DELETE FROM sqlite_master WHERE rowid=?", (rid,))
    elif not sql and typ in ("table", "index", "trigger"):
        print(f"  EMPTY rowid={rid} {typ} {name}: no SQL")
        c.execute("DELETE FROM sqlite_master WHERE rowid=?", (rid,))

c.commit()
c.execute("PRAGMA writable_schema=OFF")
c.execute("PRAGMA integrity_check")
c.close()

# Now verify
c2 = sqlite3.connect("data/kb.db")
print("\nintegrity:", c2.execute("PRAGMA integrity_check").fetchone()[0])
ki = c2.execute("SELECT count(*) FROM knowledge_items").fetchone()[0]
bl = c2.execute("SELECT count(*) FROM blocks").fetchone()[0]
print(f"knowledge_items: {ki}")
print(f"blocks: {bl}")
c2.close()
