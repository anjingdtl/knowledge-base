"""WikiLint 体检引擎测试 — broken_link 检查的正确性。

回归背景:曾因 list_wiki_pages(默认 WHERE status != 'deleted')与 get_all_wiki_links
(INNER JOIN、不带 status 过滤)所用页面集合不一致,导致:
  - 指向 status=deleted 软删页面(但物理仍存在)的合法链接被误报为 broken_link;
  - 而真正物理悬空(target 已不在 wiki_pages)的链接又因 INNER JOIN 被过滤、查不出来。

修复后 broken_link 应只报「真正物理悬空」的链接。
"""
import uuid
from datetime import datetime

from src.services.db import Database
from src.services.wiki_lint import WikiLint
from src.utils.config import Config


def _insert_wiki_page(title="Wiki Test", content="正文", status="draft", page_id=None):
    pid = page_id or str(uuid.uuid4())
    now = datetime.now().isoformat()
    Database.get_conn().execute(
        "INSERT INTO wiki_pages (id, title, content, source_ids, tags, concept_summary, "
        "status, lint_score, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (pid, title, content, "[]", "[]", "", status, 1.0, now, now),
    )
    Database.get_conn().commit()
    return pid


def _broken_link_findings(report: dict) -> list[dict]:
    return [f for f in report["findings"] if f["category"] == "broken_link"]


def test_broken_link_ignores_soft_deleted_pages():
    """指向 status=deleted 软删页面(物理仍存在)的合法链接,不应被报为 broken_link。"""
    active = _insert_wiki_page(title="活跃页面", content="正文", status="published")
    deleted = _insert_wiki_page(title="已删除页面", content="正文", status="deleted")
    # 活跃页面引用了一个被软删的页面 —— 合法链接,不是死链
    Database.add_wiki_link(active, deleted)

    report = WikiLint().run()

    broken = _broken_link_findings(report)
    assert broken == [], f"软删页面引用被误报为 broken_link: {broken}"


def test_broken_link_detects_truly_dangling_links():
    """target 物理上已不在 wiki_pages 的悬空链接,应被报为 broken_link。

    测试库 PRAGMA foreign_keys=ON,FK CASCADE 会在物理删页面时自动清 links。
    临时关闭 FK 插入一条悬空链接,模拟生产库(FK 关闭)下 purge 未清 links 的场景。
    """
    active = _insert_wiki_page(title="活跃页面", content="正文", status="published")
    conn = Database.get_conn()
    # 临时关闭 FK,插入一条指向不存在页面的悬空链接
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT OR REPLACE INTO wiki_links (source_page_id, target_page_id, link_type, weight) "
        "VALUES (?, ?, 'related', 1.0)",
        (active, "ghost-page-id-not-in-wiki-pages"),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    report = WikiLint().run()

    broken = _broken_link_findings(report)
    assert len(broken) == 1, f"应检出 1 条悬空 broken_link,实际 {len(broken)}: {broken}"
    assert broken[0]["page_id"] == active


def test_lint_outdated_claim():
    """source updated_at 晚于 wiki page updated_at → outdated_claim。"""
    import json as _json
    Database.insert_knowledge({
        "id": "k-outdated", "title": "src", "content": "c",
        "source_type": "file", "source_path": "r", "file_type": "md",
        "file_size": 1, "content_hash": "h", "file_created_at": "",
        "file_modified_at": "", "tags": "[]", "version": 1,
        "created_at": "2026-07-01T00:00:00", "updated_at": "2026-07-05T00:00:00",
    })
    conn = Database.get_conn()
    conn.execute(
        "INSERT INTO wiki_pages (id, title, content, source_ids, tags, concept_summary, "
        "status, lint_score, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("w-out", "Out Page", "body", _json.dumps(["k-outdated"]), "[]", "",
         "published", 1.0, "2026-07-01T00:00:00", "2026-07-02T00:00:00"),
    )
    conn.commit()
    report = WikiLint().run()
    cats = [f["category"] for f in report["findings"]]
    assert "outdated_claim" in cats


def test_lint_missing_backlinks():
    """无入链页面 → missing_backlinks。"""
    _insert_wiki_page(title="Lonely Page", content="body", status="published",
                      page_id="w-lonely")
    report = WikiLint().run()
    cats = [f["category"] for f in report["findings"]]
    assert "missing_backlinks" in cats


def test_lint_run_creates_database_when_no_global_instance(tmp_path, monkeypatch):
    """独立 eval 进程没有 Database._instance 时,WikiLint 仍应能打开配置库。"""
    old_instance = Database._instance
    monkeypatch.setattr(Config, "get_db_path", lambda: tmp_path / "kb.db")
    Database._instance = None
    try:
        report = WikiLint().run()
        assert report["total_pages"] == 0
        assert report["score"] == 1.0
    finally:
        created = Database._instance
        if created is not None and created is not old_instance:
            created.close()
        Database._instance = old_instance
