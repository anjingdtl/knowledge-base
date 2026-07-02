"""WikiPageLocator 单元测试(第二阶段 Task 1.0)。

覆盖 plan Step 1.0.1 的 5 个失败测试:命中匹配 / 零命中 / title+frontmatter+body
三通道 / 候选 schema / wiki 目录缺失降级。
"""
from __future__ import annotations

from pathlib import Path

from src.services.wiki_page_locator import WikiPageLocator
from src.services.wiki_slug import write_markdown


def _make_page(
    wiki_dir: Path,
    page_type: str,
    slug: str,
    title: str,
    body: str,
    key_entities: list[str] | None = None,
) -> Path:
    """在临时 wiki 目录下生成一个 wiki 页(带 frontmatter)。"""
    d = wiki_dir / page_type
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slug}.md"
    fm: dict = {"title": title}
    if key_entities is not None:
        fm["key_entities"] = key_entities
    write_markdown(path, fm, body)
    return path


def test_locate_returns_matching_pages(tmp_path):
    _make_page(tmp_path, "sources", "fttr", "FTTR光纤",
               "FTTR 是光纤到房间的技术", key_entities=["FTTR"])
    _make_page(tmp_path, "sources", "marketing", "营销通知", "本月营销活动安排")
    _make_page(tmp_path, "sources", "unrelated", "天气", "今天天气不错")

    locator = WikiPageLocator(wiki_dir=tmp_path)
    candidates, total = locator.locate("FTTR")

    assert total >= 1
    titles = [c["metadata"]["title"] for c in candidates]
    assert any("FTTR" in t for t in titles)
    # marketing / unrelated 不含 FTTR,不应命中
    assert not any("天气" in t for t in titles)


def test_locate_no_match_returns_empty(tmp_path):
    _make_page(tmp_path, "sources", "fttr", "FTTR", "FTTR 光纤",
               key_entities=["FTTR"])
    locator = WikiPageLocator(wiki_dir=tmp_path)
    candidates, total = locator.locate("zzznotexist9999")
    assert candidates == []
    assert total == 0


def test_locate_searches_title_frontmatter_body(tmp_path):
    # title / key_entities 均不含,仅 body 含独特 token —— 验证 body 通道
    _make_page(tmp_path, "sources", "doc", "无关标题文档",
               "本文介绍 UNIQUETERM 的实现细节", key_entities=["其他实体"])
    locator = WikiPageLocator(wiki_dir=tmp_path)
    candidates, total = locator.locate("UNIQUETERM")
    assert total >= 1
    assert candidates[0]["metadata"]["title"] == "无关标题文档"


def test_locate_candidate_schema(tmp_path):
    _make_page(tmp_path, "sources", "fttr", "FTTR", "FTTR 光纤",
               key_entities=["FTTR"])
    locator = WikiPageLocator(wiki_dir=tmp_path)
    candidates, _ = locator.locate("FTTR")
    assert candidates, "应至少命中一页"
    c = candidates[0]
    for field in ("id", "text", "metadata", "match_channels"):
        assert field in c, f"候选缺字段 {field}"
    assert c["match_channels"] == ["wiki_read"]
    assert isinstance(c["id"], str) and c["id"].startswith("wiki:")
    assert isinstance(c["metadata"], dict)


def test_locate_missing_wiki_dir_returns_empty(tmp_path):
    locator = WikiPageLocator(wiki_dir=tmp_path / "does_not_exist")
    candidates, total = locator.locate("anything")
    assert candidates == []
    assert total == 0
