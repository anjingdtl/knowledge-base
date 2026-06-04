"""Check which tables are corrupted in the database."""
import sqlite3

conn = sqlite3.connect("data/kb.db")
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
for t in tables:
    try:
        c = conn.execute(f"SELECT count(*) FROM [{t}]").fetchone()[0]
        print(f"  OK   {t}: {c}")
    except Exception as e:
        print(f"  ERR  {t}: {str(e)[:80]}")
conn.close()
