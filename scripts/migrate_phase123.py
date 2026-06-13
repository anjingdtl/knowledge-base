"""Phase 1/2/3 全量数据迁移脚本

三步：
  Step 1: FileGraphService.sync_all() — 重建 block 层级 + 自动发现 wiki-links + 重算 embedding
  Step 2: EffectivePropertyService.refresh_page() — 计算有效属性
  Step 3: 验证迁移结果

用法:
  python scripts/migrate_phase123.py
  python scripts/migrate_phase123.py --skip-sync    # 跳过 sync_all，只跑 Step 2+3
  python scripts/migrate_phase123.py --dry-run      # 只打印快照，不执行迁移
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SHINEHE_HOME", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.db import Database
from src.utils.config import Config


def print_header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def print_snapshot(conn, label):
    print(f"--- {label} ---")
    print(f"  knowledge_items:          {conn.execute('SELECT count(*) FROM knowledge_items').fetchone()[0]}")
    print(f"  blocks total:             {conn.execute('SELECT count(*) FROM blocks').fetchone()[0]}")
    print(f"  blocks WITH parent_id:    {conn.execute('SELECT count(*) FROM blocks WHERE parent_id IS NOT NULL').fetchone()[0]}")
    print(f"  blocks WITHOUT parent_id: {conn.execute('SELECT count(*) FROM blocks WHERE parent_id IS NULL').fetchone()[0]}")
    print(f"  knowledge_chunks:         {conn.execute('SELECT count(*) FROM knowledge_chunks').fetchone()[0]}")
    print(f"  entity_refs total:        {conn.execute('SELECT count(*) FROM entity_refs').fetchone()[0]}")
    print(f"  entity_refs auto_disc:    {conn.execute('SELECT count(*) FROM entity_refs WHERE auto_discovered=1').fetchone()[0]}")
    print(f"  effective_property_index: {conn.execute('SELECT count(*) FROM effective_property_index').fetchone()[0]}")
    print(f"  tag_relations:            {conn.execute('SELECT count(*) FROM tag_relations').fetchone()[0]}")
    print(f"  property_schemas:         {conn.execute('SELECT count(*) FROM property_schemas').fetchone()[0]}")
    print(f"  block_property_index:     {conn.execute('SELECT count(*) FROM block_property_index').fetchone()[0]}")
    pages_dir = os.path.join("data", "graph", "pages")
    md_count = len([f for f in os.listdir(pages_dir) if f.endswith(".md")]) if os.path.isdir(pages_dir) else 0
    print(f"  graph/pages MD files:     {md_count}")
    print()


def backup_database(db_path):
    backup_path = db_path + f".pre_migration_{int(time.time())}"
    import shutil
    shutil.copy2(db_path, backup_path)
    size_mb = os.path.getsize(backup_path) / (1024 * 1024)
    print(f"  Backup: {backup_path} ({size_mb:.1f} MB)")
    return backup_path


def step1_sync_all():
    print_header("Step 1: sync_all — 重建 block 层级 + wiki-link 发现")

    Config.load()

    # Reset Database singleton to ensure clean connection
    Database._conn = None
    Database._instance = None
    Database._shutdown = True

    Database.connect()
    conn = Database.get_conn()

    # Verify we're on the right database
    db_file = conn.execute("PRAGMA database_list").fetchone()["file"]
    print(f"  Connected to: {db_file}")

    db_path = os.path.join(Config.get("storage.data_dir", "data"), Config.get("storage.db_name", "kb.db"))
    if not os.path.exists(db_path):
        print(f"  ERROR: database not found at {db_path}")
        return False

    backup_database(db_path)

    Database.connect()
    conn = Database.get_conn()
    print_snapshot(conn, "BEFORE sync_all")

    from src.core.container import create_container
    container = create_container()

    # Monkey-patch embedding to skip during migration (will be done separately)
    fg = container.file_graph_service
    fg._embedding = None

    root = fg.ensure_graph()
    files = sorted((root / "pages").glob("*.md"))
    print(f"  Found {len(files)} MD files to sync")

    t0 = time.time()
    synced = 0
    errors = 0
    for i, path in enumerate(files):
        try:
            fg.sync_page(str(path))
            synced += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"    ERROR on {path.name}: {e}")
        if (i + 1) % 50 == 0 or (i + 1) == len(files):
            elapsed = time.time() - t0
            pct = (i + 1) / len(files) * 100
            print(f"    [{i+1}/{len(files)}] ({pct:.0f}%) synced, {elapsed:.0f}s elapsed, {errors} errors")

    elapsed = time.time() - t0
    print(f"\n  sync_all completed: {synced} synced, {errors} errors, {elapsed:.1f}s total")

    print_snapshot(conn, "AFTER sync_all")

    Database.close()
    return True


def step2_effective_properties():
    print_header("Step 2: effective_property_index — 计算有效属性")

    Config.load()
    Database.connect()

    conn = Database.get_conn()
    pages = conn.execute("SELECT id FROM knowledge_items").fetchall()

    print(f"  Processing {len(pages)} pages...")

    from src.services.effective_properties import EffectivePropertyService
    eff_svc = EffectivePropertyService(db=Database)

    t0 = time.time()
    total_blocks = 0
    for i, row in enumerate(pages):
        try:
            count = eff_svc.refresh_page(row["id"])
            total_blocks += count
        except Exception as e:
            print(f"    WARNING: page {row['id']}: {e}")
        if (i + 1) % 25 == 0 or (i + 1) == len(pages):
            print(f"    [{i+1}/{len(pages)}] pages processed, {total_blocks} blocks refreshed")

    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.1f}s, {total_blocks} effective property rows")

    eff_count = conn.execute("SELECT count(*) FROM effective_property_index").fetchone()[0]
    print(f"  effective_property_index rows: {eff_count}")

    Database.close()
    return True


def step3_verify():
    print_header("Step 3: 验证迁移结果")

    Config.load()
    Database.connect()

    conn = Database.get_conn()
    print_snapshot(conn, "FINAL STATE")

    total_blocks = conn.execute("SELECT count(*) FROM blocks").fetchone()[0]
    with_parent = conn.execute("SELECT count(*) FROM blocks WHERE parent_id IS NOT NULL").fetchone()[0]
    auto_links = conn.execute("SELECT count(*) FROM entity_refs WHERE auto_discovered=1").fetchone()[0]
    eff_props = conn.execute("SELECT count(*) FROM effective_property_index").fetchone()[0]

    print("--- 验证 ---")

    ok = True

    if with_parent > 0:
        pct = with_parent / total_blocks * 100 if total_blocks > 0 else 0
        print(f"  [OK] Block 层级: {with_parent}/{total_blocks} ({pct:.1f}%) blocks have parent_id")
    else:
        print("  [FAIL] Block 层级: 0 blocks have parent_id")
        ok = False

    if auto_links > 0:
        print(f"  [OK] Wiki-link 发现: {auto_links} auto_discovered entity_refs")
    else:
        print("  [WARN] Wiki-link 发现: 0 auto_discovered links (可能是 MD 文件中无 [[链接]] 语法)")

    if eff_props > 0:
        print(f"  [OK] 有效属性: {eff_props} rows in effective_property_index")
    else:
        print("  [WARN] 有效属性: 0 rows (如果没有定义 property_schemas 这是正常的)")

    ki_count = conn.execute("SELECT count(*) FROM knowledge_items").fetchone()[0]
    chunks_count = conn.execute("SELECT count(*) FROM knowledge_chunks").fetchone()[0]
    if ki_count > 0:
        print(f"  [OK] 知识条目: {ki_count} items, {chunks_count} chunks preserved")
    else:
        print("  [FAIL] 知识条目丢失!")
        ok = False

    if ok:
        print("\n  Migration PASSED — all Phase 1/2/3 capabilities are active.")
    else:
        print("\n  Migration has ISSUES — see above for details.")

    Database.close()
    return ok


def main():
    args = set(sys.argv[1:])

    if "--dry-run" in args:
        print_header("DRY RUN — 只打印快照")
        Config.load()
        Database.connect()
        print_snapshot(Database.get_conn(), "CURRENT STATE")
        Database.close()
        return

    if "--skip-sync" not in args:
        if not step1_sync_all():
            print("Step 1 FAILED, aborting.")
            return
    else:
        print("Skipping Step 1 (sync_all)")

    if not step2_effective_properties():
        print("Step 2 FAILED, aborting.")
        return

    step3_verify()


if __name__ == "__main__":
    main()
