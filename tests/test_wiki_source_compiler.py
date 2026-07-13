"""WikiSourceCompiler 测试(规则模板,零 LLM)。"""

import pytest

from src.services.db import Database
from src.services.wiki_source_compiler import WikiSourceCompiler
from src.utils.config import Config


def _insert_knowledge(kid, title, content, file_type="md", content_hash="", source_path="raw/api.md"):
    Database.insert_knowledge({
        "id": kid, "title": title, "content": content,
        "source_type": "file", "source_path": source_path, "file_type": file_type,
        "file_size": len(content), "content_hash": content_hash,
        "file_created_at": "", "file_modified_at": "",
        "tags": "[]", "version": 1,
        "created_at": "2026-07-02T10:00:00", "updated_at": "2026-07-02T10:00:00",
    })


@pytest.fixture
def wiki_first_dirs(tmp_path):
    """配置 wiki_first 目录到 tmp_path(setup_db autouse 已建 DB + Config.load)。"""
    Config.set("knowledge_workflow.wiki_dir", str(tmp_path / "wiki"))
    Config.set("knowledge_workflow.source_summary_dir", str(tmp_path / "wiki" / "sources"))
    return tmp_path


def test_compile_prepares_source_summary(wiki_first_dirs):
    _insert_knowledge(
        "kid-1", "API Overview",
        "# API Overview\n\nThe MCP API exposes tools.\n\n## Endpoints\n\nPOST /ask",
        content_hash="hashabc12345",
    )
    result = WikiSourceCompiler().compile("kid-1", ingested_at="2026-07-02T10:00:00")
    assert result["status"] == "prepared"
    assert result["suggested_path"] == "sources/api-overview.md"
    assert result["frontmatter"]["source_hash"] == "hashabc12345"
    assert "API Overview" in result["body"]
    assert "MCP" in result["body"]  # acronym extracted
    assert not (wiki_first_dirs / "wiki" / "sources").exists()


def test_compile_prepares_source_summary_without_writing_markdown(wiki_first_dirs):
    _insert_knowledge(
        "kid-1", "API Overview",
        "# API Overview\n\nThe MCP API exposes tools.",
        content_hash="hashabc12345",
    )
    result = WikiSourceCompiler().compile("kid-1", ingested_at="2026-07-02T10:00:00")

    assert result["status"] == "prepared"
    assert result["frontmatter"]["source_ids"] == ["kid-1"]
    assert result["frontmatter"]["knowledge_id"] == "kid-1"
    assert "API Overview" in result["body"]
    assert "MCP" in result["body"]
    assert not (wiki_first_dirs / "wiki" / "sources").exists()


def test_compile_idempotent(wiki_first_dirs):
    _insert_knowledge("kid-1", "API Overview", "# API\n\nbody text here",
                      content_hash="hashabc12345")
    c = WikiSourceCompiler()
    r1 = c.compile("kid-1", ingested_at="2026-07-02T10:00:00")
    r2 = c.compile("kid-1", ingested_at="2026-07-02T10:00:00")
    assert r1["suggested_path"] == r2["suggested_path"]
    assert r1["frontmatter"] == r2["frontmatter"]
    assert r1["body"] == r2["body"]
    assert not (wiki_first_dirs / "wiki" / "sources").exists()


def test_compile_not_found(wiki_first_dirs):
    result = WikiSourceCompiler().compile("missing", ingested_at="2026-07-02T10:00:00")
    assert result["status"] == "not_found"


def test_extract_key_entities_acronyms_and_title():
    entities = WikiSourceCompiler._extract_key_entities(
        "The LLM and MCP tools work with the API.", "API Overview"
    )
    assert "LLM" in entities
    assert "MCP" in entities
    assert "API" in entities


def test_build_summary_truncates_to_500():
    long = "# Heading\n\n" + ("x" * 2000)
    summary = WikiSourceCompiler._build_summary(long)
    assert len(summary) <= 500
    assert "Heading" in summary  # heading path included
