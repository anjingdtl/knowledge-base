"""WikiLogCompiler 测试。"""

import pytest

from src.services.wiki_log_compiler import WikiLogCompiler
from src.utils.config import Config


@pytest.fixture
def wiki_dir(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    Config.set("knowledge_workflow.wiki_dir", str(wiki))
    return wiki


def test_append_prepares_entry(wiki_dir):
    c = WikiLogCompiler()
    result = c.append({
        "type": "ingest", "target": "API Overview",
        "timestamp": "2026-07-02T10:00:00", "detail": "compiled kid-1",
    })
    assert result["status"] == "prepared"
    text = result["body"]
    assert "ingest" in text
    assert "API Overview" in text
    assert "2026-07-02T10:00:00" in text
    assert not (wiki_dir / "log.md").exists()


def test_append_prepares_log_without_writing_markdown(wiki_dir):
    c = WikiLogCompiler()
    result = c.append({
        "type": "ingest", "target": "API Overview",
        "timestamp": "2026-07-02T10:00:00", "detail": "compiled kid-1",
    })

    assert result["status"] == "prepared"
    assert result["suggested_path"] == "log.md"
    assert "ingest" in result["body"]
    assert "API Overview" in result["body"]
    assert not (wiki_dir / "log.md").exists()


def test_rebuild_prepares_log_without_writing_markdown(wiki_dir):
    c = WikiLogCompiler()
    result = c.rebuild([
        {"type": "ingest", "target": "B", "timestamp": "2026-07-02T12:00:00", "detail": "d"},
        {"type": "ingest", "target": "A", "timestamp": "2026-07-02T10:00:00", "detail": "d"},
    ])

    assert result["status"] == "prepared"
    assert result["suggested_path"] == "log.md"
    assert result["entries"] == 2
    assert result["body"].index("2026-07-02T10:00:00") < result["body"].index("2026-07-02T12:00:00")
    assert not (wiki_dir / "log.md").exists()


def test_append_creates_header(wiki_dir):
    c = WikiLogCompiler()
    result = c.append({"type": "ingest", "target": "T", "timestamp": "ts1", "detail": "d"})
    assert result["body"].startswith("# Wiki Log")
    assert not (wiki_dir / "log.md").exists()


def test_append_dedup(wiki_dir):
    c = WikiLogCompiler()
    ev = {"type": "ingest", "target": "T", "timestamp": "ts1", "detail": "d"}
    r1 = c.append(ev)
    (wiki_dir / "log.md").write_text(r1["body"], encoding="utf-8")
    r2 = c.append(ev)
    assert r1["status"] == "prepared"
    assert r2["status"] == "duplicate"
    assert r2["body"].count("**ingest**") == 1


def test_rebuild_sorts_and_dedups(wiki_dir):
    c = WikiLogCompiler()
    events = [
        {"type": "ingest", "target": "B", "timestamp": "2026-07-02T12:00:00", "detail": "d"},
        {"type": "ingest", "target": "A", "timestamp": "2026-07-02T10:00:00", "detail": "d"},
        {"type": "ingest", "target": "A", "timestamp": "2026-07-02T10:00:00", "detail": "d"},
    ]
    result = c.rebuild(events)
    assert result["entries"] == 2
    text = result["body"]
    assert text.index("2026-07-02T10:00:00") < text.index("2026-07-02T12:00:00")
    assert not (wiki_dir / "log.md").exists()
