"""WikiIndexCompiler 测试。"""
from pathlib import Path

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
    assert result["status"] == "compiled"
    assert result["page_count"] == 3
    idx = Path(result["path"])
    assert idx.exists()
    text = idx.read_text(encoding="utf-8")
    assert "Sources" in text
    assert "Entities" in text
    assert "API Overview" in text
    assert "LLM Basics" in text
    assert "Foo" in text


def test_refresh_groups_by_type(wiki_with_pages):
    WikiIndexCompiler().refresh()
    text = (wiki_with_pages / "index.md").read_text(encoding="utf-8")
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
    text = (wiki / "index.md").read_text(encoding="utf-8")
    assert "_(none)_" in text


def test_refresh_idempotent(wiki_with_pages):
    c = WikiIndexCompiler()
    r1 = c.refresh()
    r2 = c.refresh()
    assert r1["page_count"] == r2["page_count"] == 3
