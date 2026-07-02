"""WikiSourceCompiler 测试(规则模板,零 LLM)。"""
from pathlib import Path

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


def test_compile_generates_source_summary(wiki_first_dirs):
    _insert_knowledge(
        "kid-1", "API Overview",
        "# API Overview\n\nThe MCP API exposes tools.\n\n## Endpoints\n\nPOST /ask",
        content_hash="hashabc12345",
    )
    result = WikiSourceCompiler().compile("kid-1", ingested_at="2026-07-02T10:00:00")
    assert result["status"] == "compiled"
    p = Path(result["path"])
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "API Overview" in text
    assert "hashabc12345" in text  # frontmatter source_hash
    assert "MCP" in text  # acronym extracted


def test_compile_idempotent(wiki_first_dirs):
    _insert_knowledge("kid-1", "API Overview", "# API\n\nbody text here",
                      content_hash="hashabc12345")
    c = WikiSourceCompiler()
    r1 = c.compile("kid-1", ingested_at="2026-07-02T10:00:00")
    r2 = c.compile("kid-1", ingested_at="2026-07-02T10:00:00")
    assert r1["path"] == r2["path"]  # 同路径覆盖
    sources_dir = Path(r1["path"]).parent
    assert len(list(sources_dir.glob("*.md"))) == 1  # 无第二文件


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
