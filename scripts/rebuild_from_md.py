"""Clean rebuild from graph/pages/*.md files.

Since the original DB has corruption in knowledge_items (null bytes in content),
we rebuild everything from the MD files — the single source of truth in file-first architecture.

Steps:
  1. Create a fresh DB with full schema
  2. Copy auxiliary tables from the repaired DB (users, wiki_pages, categories, etc.)
  3. Run FileGraphService.sync_all() to rebuild knowledge_items + blocks + chunks + FTS + links
  4. Run EffectivePropertyService.refresh_page() for all pages
  5. Verify
"""
import os
import sys
import time
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

REPAIRED_PATH = None
for f in os.listdir("data"):
    if f.startswith("kb.db.repaired_"):
        REPAIRED_PATH = os.path.join("data", f)
        break

FRESH_PATH = f"data/kb.db.fresh_{int(time.time())}"


def main():
    print("=" * 60)
    print("  Clean Rebuild from graph/pages/*.md")
    print("=" * 60)

    # Step 1: Create fresh DB with full schema
    print("\n[Step 1] Creating fresh database...")

    from src.utils.config import Config
    Config.load()

    from src.services.db import Database, _SCHEMA
    import sqlite3

    fresh = sqlite3.connect(FRESH_PATH)
    fresh.execute("PRAGMA journal_mode=WAL")
    fresh.execute("PRAGMA foreign_keys=ON")
    fresh.executescript(_SCHEMA)
    fresh.commit()
    fresh.close()

    print("  Fresh DB created with full schema")

    # Step 2: Copy auxiliary tables from repaired DB
    print("\n[Step 2] Copying auxiliary data...")

    if REPAIRED_PATH and os.path.exists(REPAIRED_PATH):
        src = sqlite3.connect(REPAIRED_PATH)
        dst = sqlite3.connect(FRESH_PATH)
        dst.execute("PRAGMA foreign_keys=OFF")

        aux_tables = [
            "users",
            "categories", "knowledge_categories",
            "wiki_pages", "wiki_links", "wiki_ops_log", "wiki_page_versions", "wiki_workflow",
            "knowledge_graphs", "knowledge_graph_nodes", "knowledge_graph_relations",
            "embedding_cache",
            "tag_relations", "property_schemas",
        ]
        for table in aux_tables:
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
                print(f"  {table}: {len(rows)} rows")
            except Exception as e:
                msg = str(e).encode("ascii", errors="replace").decode("ascii")[:50]
                print(f"  SKIP {table}: {msg}")

        dst.commit()
        dst.close()
        src.close()
    else:
        print("  No repaired DB found, skipping auxiliary data copy")

    # Step 3: Run sync_all to rebuild from MD files
    print("\n[Step 3] Rebuilding from MD files via sync_all...")

    # Point Database to the fresh DB
    Database._conn = None
    Database._instance = None
    Database._shutdown = True
    Database.connect(FRESH_PATH)

    from src.core.container import create_container
    container = create_container()

    fg = container.file_graph_service
    fg._embedding = None  # Skip embedding during rebuild

    root = fg.ensure_graph()
    files = sorted((root / "pages").glob("*.md"))
    print(f"  Found {len(files)} MD files")

    t0 = time.time()
    synced = 0
    errors = 0

    # Process in batches for progress tracking
    batch_size = 50
    for batch_start in range(0, len(files), batch_size):
        batch = files[batch_start:batch_start + batch_size]
        for path in batch:
            try:
                fg.sync_page(str(path))
                synced += 1
            except Exception as e:
                errors += 1
                if errors <= 3:
                    msg = str(e).encode("ascii", errors="replace").decode("ascii")[:60]
                    print(f"    ERROR {path.name}: {msg}")
        elapsed = time.time() - t0
        done = min(batch_start + batch_size, len(files))
        pct = done / len(files) * 100
        rate = synced / elapsed if elapsed > 0 else 0
        eta = (len(files) - done) / rate if rate > 0 else 0
        print(f"    [{done}/{len(files)}] ({pct:.0f}%) {synced} ok, {errors} err, {elapsed:.0f}s, ETA {eta:.0f}s")

    elapsed = time.time() - t0
    print(f"  sync_all done: {synced} synced, {errors} errors, {elapsed:.1f}s")

    # Step 4: Effective properties
    print("\n[Step 4] Computing effective properties...")

    conn = Database.get_conn()
    pages = conn.execute("SELECT id FROM knowledge_items").fetchall()

    from src.services.effective_properties import EffectivePropertyService
    eff_svc = EffectivePropertyService(db=Database)

    total_blocks = 0
    for row in pages:
        try:
            total_blocks += eff_svc.refresh_page(row["id"])
        except Exception:
            pass

    print(f"  {len(pages)} pages, {total_blocks} effective property rows")

    # Step 5: Verify
    print("\n[Step 5] Verification...")

    ki = conn.execute("SELECT count(*) FROM knowledge_items").fetchone()[0]
    kc = conn.execute("SELECT count(*) FROM knowledge_chunks").fetchone()[0]
    bl = conn.execute("SELECT count(*) FROM blocks").fetchone()[0]
    bl_parent = conn.execute("SELECT count(*) FROM blocks WHERE parent_id IS NOT NULL").fetchone()[0]
    er_auto = conn.execute("SELECT count(*) FROM entity_refs WHERE auto_discovered=1").fetchone()[0]
    eff = conn.execute("SELECT count(*) FROM effective_property_index").fetchone()[0]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]

    print(f"  knowledge_items:          {ki}")
    print(f"  knowledge_chunks:         {kc}")
    print(f"  blocks total:             {bl}")
    print(f"  blocks WITH parent_id:    {bl_parent}")
    print(f"  entity_refs auto_disc:    {er_auto}")
    print(f"  effective_property_index: {eff}")
    print(f"  integrity_check:          {integrity}")

    Database.close()

    if ki > 0 and integrity == "ok":
        # Backup old DB and install fresh one
        final_backup = f"data/kb.db.pre_rebuild_{int(time.time())}"
        shutil.copy2("data/kb.db", final_backup)
        shutil.copy2(FRESH_PATH, "data/kb.db")
        os.remove(FRESH_PATH)

        # Cleanup
        for f in os.listdir("data"):
            if f.startswith("kb.db.pre_migration") or f.startswith("kb.db.pre_repair"):
                os.remove(os.path.join("data", f))
                print(f"  Cleaned up: {f}")

        print(f"\n  SUCCESS: Fresh DB installed at data/kb.db")
        print(f"  Backup: {os.path.basename(final_backup)}")
    else:
        print(f"\n  ISSUE: ki={ki}, integrity={integrity}")
        print(f"  Fresh DB kept at: {FRESH_PATH}")


if __name__ == "__main__":
    main()
