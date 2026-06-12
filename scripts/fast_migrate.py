"""快速迁移脚本：使用 CREATE 代替 MERGE，大幅提升迁移速度"""
import json
import sys
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

# 1. 清空 Neo4j
print("Step 1: Clearing Neo4j...", flush=True)
with driver.session(database=DB) as session:
    session.run("MATCH (n) DETACH DELETE n")
print("  Done.", flush=True)

conn = Database.get_conn()
batch_size = 10000
total_start = time.time()

# 2. 迁移页面节点 (CREATE)
print("\nStep 2: Migrating pages...", flush=True)
rows = conn.execute(
    "SELECT id, title, file_type, tags, source_type FROM knowledge_items"
).fetchall()
nodes = []
for r in rows:
    nodes.append({
        "source_id": str(r["id"]),
        "label": r["title"] or "" if "title" in r.keys() else "",
        "node_type": "page",
        "properties": json.dumps({
            "file_type": r["file_type"] or "" if "file_type" in r.keys() else "",
        }, ensure_ascii=False),
    })

t0 = time.time()
with driver.session(database=DB) as session:
    session.run(
        "UNWIND $nodes AS n CREATE (:Page {source_id: n.source_id, label: n.label, node_type: n.node_type, properties: n.properties})",
        nodes=nodes,
    )
print(f"  {len(nodes)} pages in {time.time()-t0:.1f}s", flush=True)

# 3. 迁移 Block 节点 (CREATE in batches)
print("\nStep 3: Migrating blocks...", flush=True)
total_blocks = conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
offset = 0
block_count = 0
t0 = time.time()
while offset < total_blocks:
    rows = conn.execute(
        "SELECT id, parent_id, page_id, content, block_type, order_idx "
        "FROM blocks ORDER BY rowid LIMIT ? OFFSET ?",
        (batch_size, offset),
    ).fetchall()
    if not rows:
        break
    nodes = []
    for r in rows:
        nodes.append({
            "source_id": str(r["id"]),
            "label": (r["content"] or "")[:80] if "content" in r.keys() else "",
            "node_type": "block",
            "properties": json.dumps({
                "block_type": r["block_type"] or "text" if "block_type" in r.keys() else "text",
                "page_id": str(r["page_id"]) if "page_id" in r.keys() and r["page_id"] else "",
                "order_idx": r["order_idx"] or 0 if "order_idx" in r.keys() else 0,
            }, ensure_ascii=False),
        })
    with driver.session(database=DB) as session:
        session.run(
            "UNWIND $nodes AS n CREATE (:Block {source_id: n.source_id, label: n.label, node_type: n.node_type, properties: n.properties})",
            nodes=nodes,
        )
    block_count += len(nodes)
    offset += batch_size
    pct = block_count / total_blocks * 100
    elapsed = time.time() - t0
    rate = block_count / elapsed if elapsed > 0 else 0
    eta = (total_blocks - block_count) / rate if rate > 0 else 0
    print(f"  [{pct:.1f}%] {block_count}/{total_blocks} blocks ({rate:.0f}/s, ETA {eta:.0f}s)", flush=True)

print(f"  Total: {block_count} blocks in {time.time()-t0:.1f}s", flush=True)

# 4. 迁移标签节点
print("\nStep 4: Migrating tags...", flush=True)
all_tags = set()
rows = conn.execute("SELECT tags FROM knowledge_items").fetchall()
for r in rows:
    raw = r["tags"] if "tags" in r.keys() else "[]"
    try:
        tags = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(tags, list):
            all_tags.update(tags)
    except:
        pass

if all_tags:
    tag_nodes = [{"source_id": t, "label": t} for t in sorted(all_tags)]
    t0 = time.time()
    with driver.session(database=DB) as session:
        session.run(
            "UNWIND $nodes AS n CREATE (:Tag {source_id: n.source_id, label: n.label, node_type: 'tag'})",
            nodes=tag_nodes,
        )
    print(f"  {len(tag_nodes)} tags in {time.time()-t0:.1f}s", flush=True)
else:
    print("  No tags found.", flush=True)

# 5. 迁移边
print("\nStep 5: Migrating edges...", flush=True)

def load_json_list(val):
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            p = json.loads(val)
            if isinstance(p, list):
                return p
        except:
            pass
    return []

# 5a. contains edges (page → block)
print("  5a. Contains edges (page→block)...", flush=True)
t0 = time.time()
rows = conn.execute("SELECT id, page_id FROM blocks WHERE page_id IS NOT NULL").fetchall()
edges = [{"src": str(r["page_id"]), "tgt": str(r["id"])} for r in rows if r["page_id"]]
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
print(f"  {len(edges)} contains edges in {time.time()-t0:.1f}s", flush=True)

# 5b. parent edges (block → child block)
print("  5b. Parent edges (block→block)...", flush=True)
t0 = time.time()
rows = conn.execute("SELECT id, parent_id FROM blocks WHERE parent_id IS NOT NULL").fetchall()
edges = [{"src": str(r["parent_id"]), "tgt": str(r["id"])} for r in rows if r["parent_id"]]
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
print(f"  {len(edges)} parent edges in {time.time()-t0:.1f}s", flush=True)

# 5c. tagged_with edges (page → tag)
print("  5c. Tagged_with edges (page→tag)...", flush=True)
t0 = time.time()
rows = conn.execute("SELECT id, tags FROM knowledge_items").fetchall()
tag_edges = []
for r in rows:
    tags = load_json_list(r["tags"] if "tags" in r.keys() else "[]")
    for tag in tags:
        tag_edges.append({"src": str(r["id"]), "tgt": tag})
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
print(f"  {len(tag_edges)} tagged_with edges in {time.time()-t0:.1f}s", flush=True)

# 5d. entity_refs
print("  5d. Entity refs...", flush=True)
er_count = conn.execute("SELECT COUNT(*) FROM entity_refs").fetchone()[0]
print(f"  {er_count} entity_refs (skipped - none to migrate)", flush=True)

# 5e. tag_relations
print("  5e. Tag relations...", flush=True)
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
        print(f"  {len(edges)} tag relations", flush=True)
    else:
        print("  No tag relations.", flush=True)
except:
    print("  No tag_relations table.", flush=True)

# 6. 创建索引
print("\nStep 6: Creating indexes...", flush=True)
with driver.session(database=DB) as session:
    for label in ["Page", "Block", "Tag"]:
        try:
            session.run(f"CREATE INDEX idx_{label.lower()}_source_id IF NOT EXISTS FOR (n:{label}) ON (n.source_id)")
            print(f"  Index on {label}.source_id", flush=True)
        except Exception as e:
            print(f"  Index {label}: {e}", flush=True)

# 7. 统计
print("\nStep 7: Final stats...", flush=True)
with driver.session(database=DB) as session:
    r = session.run("MATCH (n) RETURN labels(n) AS lbl, count(n) AS cnt")
    for row in r:
        print(f"  {row['lbl']}: {row['cnt']}")
    r = session.run("MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS cnt")
    for row in r:
        print(f"  Edge {row['t']}: {row['cnt']}")

total_duration = time.time() - total_start
print(f"\nMigration complete in {total_duration:.1f}s ({total_duration/60:.1f} min)", flush=True)
driver.close()
