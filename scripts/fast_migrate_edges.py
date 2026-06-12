"""快速迁移边（索引已创建）"""
import json
import time

from src.utils.config import Config
from src.services.db import Database

config = Config()
config.load()
Database.connect(str(config.get_db_path()))

from neo4j import GraphDatabase as Neo4jDriver

# 从配置读取 Neo4j 连接信息，不再硬编码凭据
graph_cfg = config.get("graph_backend", {})
driver = Neo4jDriver.driver(
    graph_cfg.get("uri", "bolt://localhost:7687"),
    auth=(graph_cfg.get("user", "neo4j"), graph_cfg.get("password", ""))
)
DB = graph_cfg.get("database", "neo4j")

conn = Database.get_conn()
batch_size = 10000

def load_json_list(val):
    if isinstance(val, list): return val
    if isinstance(val, str):
        try:
            p = json.loads(val)
            if isinstance(p, list): return p
        except: pass
    return []

total_start = time.time()

# 1. contains edges (page → block)
print("1. Contains edges (page→block)...", flush=True)
t0 = time.time()
rows = conn.execute("SELECT id, page_id FROM blocks WHERE page_id IS NOT NULL").fetchall()
edges = [{"src": str(r["page_id"]), "tgt": str(r["id"])} for r in rows if r["page_id"]]
print(f"   {len(edges)} edges to create...", flush=True)
for i in range(0, len(edges), batch_size):
    batch = edges[i:i+batch_size]
    with driver.session(database=DB) as session:
        session.run(
            "UNWIND $edges AS e "
            "MATCH (a:Page {source_id: e.src}) "
            "MATCH (b:Block {source_id: e.tgt}) "
            "CREATE (a)-[:CONTAINS]->(b)",
            edges=batch,
        )
    pct = min(i + batch_size, len(edges)) / len(edges) * 100
    print(f"   [{pct:.0f}%] {min(i+batch_size, len(edges))}/{len(edges)}", flush=True)
print(f"   Done in {time.time()-t0:.1f}s", flush=True)

# 2. parent edges (block → child block)
print("\n2. Parent edges (block→block)...", flush=True)
t0 = time.time()
rows = conn.execute("SELECT id, parent_id FROM blocks WHERE parent_id IS NOT NULL").fetchall()
edges = [{"src": str(r["parent_id"]), "tgt": str(r["id"])} for r in rows if r["parent_id"]]
print(f"   {len(edges)} edges to create...", flush=True)
for i in range(0, len(edges), batch_size):
    batch = edges[i:i+batch_size]
    with driver.session(database=DB) as session:
        session.run(
            "UNWIND $edges AS e "
            "MATCH (a:Block {source_id: e.src}) "
            "MATCH (b:Block {source_id: e.tgt}) "
            "CREATE (a)-[:PARENT]->(b)",
            edges=batch,
        )
    pct = min(i + batch_size, len(edges)) / len(edges) * 100
    print(f"   [{pct:.0f}%] {min(i+batch_size, len(edges))}/{len(edges)}", flush=True)
print(f"   Done in {time.time()-t0:.1f}s", flush=True)

# 3. tagged_with edges (page → tag)
print("\n3. Tagged_with edges (page→tag)...", flush=True)
t0 = time.time()
rows = conn.execute("SELECT id, tags FROM knowledge_items").fetchall()
tag_edges = []
for r in rows:
    tags = load_json_list(r["tags"] if "tags" in r.keys() else "[]")
    for tag in tags:
        tag_edges.append({"src": str(r["id"]), "tgt": tag})
print(f"   {len(tag_edges)} edges to create...", flush=True)
for i in range(0, len(tag_edges), batch_size):
    batch = tag_edges[i:i+batch_size]
    with driver.session(database=DB) as session:
        session.run(
            "UNWIND $edges AS e "
            "MATCH (a:Page {source_id: e.src}) "
            "MATCH (b:Tag {source_id: e.tgt}) "
            "CREATE (a)-[:TAGGED_WITH]->(b)",
            edges=batch,
        )
print(f"   Done in {time.time()-t0:.1f}s", flush=True)

# 4. tag_relations
print("\n4. Tag relations...", flush=True)
try:
    rows = conn.execute("SELECT parent_tag, child_tag FROM tag_relations").fetchall()
    if rows:
        edges = [{"src": r["parent_tag"], "tgt": r["child_tag"]} for r in rows]
        with driver.session(database=DB) as session:
            session.run(
                "UNWIND $edges AS e "
                "MATCH (a:Tag {source_id: e.src}) "
                "MATCH (b:Tag {source_id: e.tgt}) "
                "CREATE (a)-[:TAG_PARENT]->(b)",
                edges=edges,
            )
        print(f"   {len(edges)} tag relations", flush=True)
    else:
        print("   None.", flush=True)
except:
    print("   No tag_relations table.", flush=True)

# Stats
print(f"\nTotal: {time.time()-total_start:.1f}s", flush=True)
print("\nFinal stats:", flush=True)
with driver.session(database=DB) as session:
    r = session.run("MATCH (n) RETURN labels(n) AS lbl, count(n) AS cnt")
    for row in r:
        print(f"  {row['lbl']}: {row['cnt']}")
    r = session.run("MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS cnt")
    for row in r:
        print(f"  Edge {row['t']}: {row['cnt']}")

driver.close()
print("\nDone!", flush=True)
