import sqlite3
c = sqlite3.connect("data/kb.db")
print("integrity:", c.execute("PRAGMA integrity_check").fetchone()[0])
print()
tables = c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
for (t,) in tables:
    try:
        cnt = c.execute(f"SELECT count(*) FROM [{t}]").fetchone()[0]
        print(f"  OK  {t}: {cnt}")
    except Exception as e:
        print(f"  ERR {t}: {e}")
c.close()
