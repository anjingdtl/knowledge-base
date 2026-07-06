"""存量去重清理脚本 — 扫描content相同的知识条目，保留最早版本，其余标记deleted_at

用法: python scripts/dedup_cleanup.py [--dry-run]
"""
import hashlib
import os
import sys

# 添加项目根目录到 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.db import Database


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def scan_and_mark_duplicates(dry_run: bool = False):
    """扫描存量重复内容，保留最早版本"""
    from src.utils.config import Config
    Database.connect(Config.get_db_path())
    conn = Database.get_conn()

    # 查找所有非删除条目（仅加载hash必要字段，content按需加载）
    rows = conn.execute(
        "SELECT id, title, content_hash, created_at FROM knowledge_items "
        "WHERE deleted_at IS NULL AND content IS NOT NULL AND content != '' "
        "ORDER BY created_at ASC"
    ).fetchall()

    print(f"扫描 {len(rows)} 条知识条目...")

    # 按 content_hash 分组
    hash_groups: dict[str, list[dict]] = {}
    for row in rows:
        if not row["content_hash"]:
            # 缺少 content_hash 的条目才加载 content 计算hash
            content_row = conn.execute(
                "SELECT content FROM knowledge_items WHERE id = ?", (row["id"],)
            ).fetchone()
            content = content_row["content"] if content_row else ""
            content_hash = compute_content_hash(content) if content else ""
        else:
            content_hash = row["content_hash"]
        if content_hash not in hash_groups:
            hash_groups[content_hash] = []
        hash_groups[content_hash].append(dict(row))

    # 找出重复组
    duplicates_found = 0
    items_to_delete = []
    for content_hash, items in hash_groups.items():
        if len(items) > 1:
            duplicates_found += 1
            # 保留最早的一条（按 created_at 排序，已排序）
            keeper = items[0]
            for item in items[1:]:
                print(f"  重复: [{item['id'][:8]}] {item['title'][:40]} "
                      f"(与 [{keeper['id'][:8]}] {keeper['title'][:40]} 内容相同)")
                items_to_delete.append(item["id"])

    print(f"\n发现 {duplicates_found} 组重复，共 {len(items_to_delete)} 条待删除")

    if dry_run:
        print("[dry-run] 未执行删除操作")
        return

    if not items_to_delete:
        print("无重复内容需要清理")
        return

    # 执行软删除
    from datetime import datetime
    now = datetime.now().isoformat()
    for item_id in items_to_delete:
        conn.execute(
            "UPDATE knowledge_items SET deleted_at = ? WHERE id = ?",
            (now, item_id),
        )
    conn.commit()
    print(f"已软删除 {len(items_to_delete)} 条重复条目")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    scan_and_mark_duplicates(dry_run=dry_run)
