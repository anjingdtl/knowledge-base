"""wiki_slug 共用工具测试。"""

from src.services.wiki_slug import (
    read_frontmatter,
    resolve_slug,
    slugify,
    write_markdown,
)


def test_slugify_lowercase_and_hyphen():
    assert slugify("Hello World") == "hello-world"


def test_slugify_strips_punctuation():
    assert slugify("API: Overview (v2)") == "api-overview-v2"


def test_slugify_keeps_chinese():
    assert slugify("知识库 入门") == "知识库-入门"


def test_slugify_empty():
    assert slugify("") == "untitled"
    assert slugify("!!!") == "untitled"


def test_resolve_slug_no_conflict(tmp_path):
    slug, path = resolve_slug(tmp_path, "My Title", "abc123")
    assert slug == "my-title"
    assert path == tmp_path / "my-title.md"


def test_resolve_slug_same_hash_idempotent(tmp_path):
    """同 hash 已存在 → 返回同路径(覆盖)。"""
    write_markdown(tmp_path / "dup.md", {"source_hash": "abc123"}, "old")
    slug, path = resolve_slug(tmp_path, "dup", "abc123")
    assert path == tmp_path / "dup.md"


def test_resolve_slug_conflict_appends_hash(tmp_path):
    """同 title 不同 hash → 追加 -{hash[:8]}。"""
    write_markdown(tmp_path / "conflict.md", {"source_hash": "oldhash"}, "old")
    slug, path = resolve_slug(tmp_path, "conflict", "newhash123")
    assert slug == "conflict-newhash1"
    assert path == tmp_path / "conflict-newhash1.md"


def test_write_and_read_frontmatter(tmp_path):
    p = tmp_path / "x.md"
    write_markdown(p, {"title": "T", "n": 3}, "Body text")
    fm = read_frontmatter(p)
    assert fm["title"] == "T"
    assert fm["n"] == 3
    assert "Body text" in p.read_text(encoding="utf-8")


def test_read_frontmatter_missing(tmp_path):
    p = tmp_path / "nofm.md"
    p.write_text("no frontmatter here", encoding="utf-8")
    assert read_frontmatter(p) == {}
