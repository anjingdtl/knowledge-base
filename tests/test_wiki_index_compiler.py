"""WikiIndexCompiler 测试。"""

import pytest

from src.services.wiki_index_compiler import WikiIndexCompiler
from src.services.wiki_slug import write_markdown
from src.utils.config import Config


@pytest.fixture
def wiki_with_pages(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "sources").mkdir(parents=True)
    (wiki / "entities").mkdir(parents=True)
    write_markdown(wiki / "sources" / "api.md", {"title": "API Overview"}, "# API")
    write_markdown(wiki / "sources" / "llm.md", {"title": "LLM Basics"}, "# LLM")
    write_markdown(wiki / "entities" / "foo.md", {"title": "Foo"}, "# Foo")
    Config.set("knowledge_workflow.wiki_dir", str(wiki))
    return wiki


def test_refresh_generates_index(wiki_with_pages):
    result = WikiIndexCompiler().refresh()
    assert result["status"] == "prepared"
    assert result["page_count"] == 3
    assert result["suggested_path"] == "index.md"
    text = result["body"]
    assert "Sources" in text
    assert "Entities" in text
    assert "API Overview" in text
    assert "LLM Basics" in text
    assert "Foo" in text
    assert not (wiki_with_pages / "index.md").exists()


def test_refresh_prepares_index_without_writing_markdown(wiki_with_pages):
    result = WikiIndexCompiler().refresh()

    assert result["status"] == "prepared"
    assert result["suggested_path"] == "index.md"
    assert result["frontmatter"] == {"generated": True}
    assert result["page_count"] == 3
    assert "API Overview" in result["body"]
    assert "Foo" in result["body"]
    assert not (wiki_with_pages / "index.md").exists()


def test_refresh_groups_by_type(wiki_with_pages):
    result = WikiIndexCompiler().refresh()
    text = result["body"]
    sources_section = text.split("## Sources")[1].split("## Entities")[0]
    entities_section = text.split("## Entities")[1].split("## Concepts")[0]
    assert sources_section.count("- [") == 2
    assert entities_section.count("- [") == 1


def test_refresh_empty_wiki(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    Config.set("knowledge_workflow.wiki_dir", str(wiki))
    result = WikiIndexCompiler().refresh()
    assert result["page_count"] == 0
    text = result["body"]
    assert "_(none)_" in text
    assert not (wiki / "index.md").exists()


def test_refresh_idempotent(wiki_with_pages):
    c = WikiIndexCompiler()
    r1 = c.refresh()
    r2 = c.refresh()
    assert r1["page_count"] == r2["page_count"] == 3
