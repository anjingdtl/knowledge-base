"""WikiFsLint 文件系统 wiki 体检测试(spec Phase2 W4 Gap B)。

镜像 test_wiki_lint.py 的断言风格,但走文件系统:用 write_markdown 在 tmp_path
自建 wiki/<page_type>/*.md fixture(本机无 wiki/ 产物)。
"""
from __future__ import annotations

from pathlib import Path

from src.services.wiki_fs_lint import WikiFsLint
from src.services.wiki_slug import write_markdown


def _page(wiki: Path, ptype: str, slug: str, fm: dict, body: str) -> Path:
    d = wiki / ptype
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{slug}.md"
    write_markdown(target, fm, body)
    return target


def test_run_empty_wiki_dir_returns_zero(tmp_path):
    report = WikiFsLint(wiki_dir=tmp_path / "wiki").run()
    assert report["total_pages"] == 0
    assert report["findings"] == []
    assert report["score"] == 1.0


def test_orphan_page_detected(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1", "source_hash": "h1"}, "正文无链接")
    report = WikiFsLint(wiki_dir=wiki).run()
    orphans = [f for f in report["findings"] if f["category"] == "orphan"]
    assert len(orphans) == 1
    assert orphans[0]["page_id"] == "wiki:sources:alpha"


def test_dead_reference_detected(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1", "source_hash": "h1"},
          "正文引用了 [[不存在的页面]]")
    report = WikiFsLint(wiki_dir=wiki).run()
    dead = [f for f in report["findings"] if f["category"] == "dead_reference"]
    assert len(dead) == 1
    assert "不存在的页面" in dead[0]["detail"]["missing_titles"]


def test_valid_cross_link_no_dead_reference(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1", "source_hash": "h1"},
          "见 [[Beta]]")
    _page(wiki, "entities", "beta", {"title": "Beta", "kind": "entity"}, "实体页")
    report = WikiFsLint(wiki_dir=wiki).run()
    dead = [f for f in report["findings"] if f["category"] == "dead_reference"]
    assert dead == []


def test_duplicate_titles_detected(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "sources", "a", {"title": "同名", "knowledge_id": "k1"}, "x")
    _page(wiki, "sources", "b", {"title": "同名", "knowledge_id": "k2"}, "y")
    report = WikiFsLint(wiki_dir=wiki).run()
    dups = [f for f in report["findings"] if f["category"] == "duplicate"]
    assert len(dups) >= 1


def test_missing_backlinks_detected(tmp_path):
    wiki = tmp_path / "wiki"
    # Alpha -> Beta,但无人指向 Alpha
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1"}, "引 [[Beta]]")
    _page(wiki, "entities", "beta", {"title": "Beta", "kind": "entity"}, "b")
    report = WikiFsLint(wiki_dir=wiki).run()
    missing_bl = [f for f in report["findings"] if f["category"] == "missing_backlinks"]
    page_ids_missing = {f["page_id"] for f in missing_bl}
    assert "wiki:sources:alpha" in page_ids_missing
    assert "wiki:entities:beta" not in page_ids_missing  # Beta 有入链


def test_empty_page_detected(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1", "source_hash": "h1"}, "")
    report = WikiFsLint(wiki_dir=wiki).run()
    empties = [f for f in report["findings"] if f["category"] == "empty"]
    assert len(empties) == 1


def test_healthy_page_score(tmp_path):
    wiki = tmp_path / "wiki"
    # 互相链接、有内容、无重复 → 两页都 healthy,score=1.0
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1", "source_hash": "h1"}, "引 [[Beta]]")
    _page(wiki, "entities", "beta", {"title": "Beta", "kind": "entity"}, "引 [[Alpha]]")
    report = WikiFsLint(wiki_dir=wiki).run()
    assert report["total_pages"] == 2
    assert report["healthy_pages"] == 2
    assert report["score"] == 1.0
