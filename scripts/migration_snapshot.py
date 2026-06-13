"""迁移前数据快照"""
import os
import sqlite3

conn = sqlite3.connect("data/kb.db")
conn.row_factory = sqlite3.Row

tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
for t in tables:
    try:
        count = conn.execute(f"SELECT count(*) as c FROM [{t}]").fetchone()["c"]
        print(f"  {t}: {count}")
    except Exception:
        print(f"  {t}: ERROR")

print()
print("=== Block 层级 ===")
print(f"  blocks WITH parent_id:    {conn.execute('SELECT count(*) as c FROM blocks WHERE parent_id IS NOT NULL').fetchone()['c']}")
print(f"  blocks WITHOUT parent_id: {conn.execute('SELECT count(*) as c FROM blocks WHERE parent_id IS NULL').fetchone()['c']}")

print("\n=== Entity Refs ===")
print(f"  auto_discovered=1: {conn.execute('SELECT count(*) as c FROM entity_refs WHERE auto_discovered=1').fetchone()['c']}")
print(f"  total:             {conn.execute('SELECT count(*) as c FROM entity_refs').fetchone()['c']}")

print("\n=== Phase 2 表 ===")
print(f"  effective_property_index: {conn.execute('SELECT count(*) as c FROM effective_property_index').fetchone()['c']}")
print(f"  tag_relations:            {conn.execute('SELECT count(*) as c FROM tag_relations').fetchone()['c']}")
print(f"  property_schemas:         {conn.execute('SELECT count(*) as c FROM property_schemas').fetchone()['c']}")

print("\n=== Knowledge Items ===")
for r in conn.execute("SELECT source_type, count(*) as c FROM knowledge_items GROUP BY source_type").fetchall():
    print(f"  source_type={r['source_type']}: {r['c']}")
for r in conn.execute("SELECT file_type, count(*) as c FROM knowledge_items GROUP BY file_type").fetchall():
    print(f"  file_type={r['file_type']}: {r['c']}")

print("\n=== Graph Pages ===")
pages_dir = "data/graph/pages"
if os.path.isdir(pages_dir):
    md_files = [f for f in os.listdir(pages_dir) if f.endswith(".md")]
    print(f"  MD files: {len(md_files)}")

conn.close()
