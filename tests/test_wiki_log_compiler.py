"""WikiLogCompiler 测试。"""
from pathlib import Path

import pytest

from src.services.wiki_log_compiler import WikiLogCompiler
from src.utils.config import Config


@pytest.fixture
def wiki_dir(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    Config.set("knowledge_workflow.wiki_dir", str(wiki))
    return wiki


def test_append_writes_entry(wiki_dir):
    c = WikiLogCompiler()
    result = c.append({
        "type": "ingest", "target": "API Overview",
        "timestamp": "2026-07-02T10:00:00", "detail": "compiled kid-1",
    })
    assert result["status"] == "appended"
    text = (wiki_dir / "log.md").read_text(encoding="utf-8")
    assert "ingest" in text
    assert "API Overview" in text
    assert "2026-07-02T10:00:00" in text


def test_append_creates_header(wiki_dir):
    c = WikiLogCompiler()
    c.append({"type": "ingest", "target": "T", "timestamp": "ts1", "detail": "d"})
    text = (wiki_dir / "log.md").read_text(encoding="utf-8")
    assert text.startswith("# Wiki Log")


def test_append_dedup(wiki_dir):
    c = WikiLogCompiler()
    ev = {"type": "ingest", "target": "T", "timestamp": "ts1", "detail": "d"}
    r1 = c.append(ev)
    r2 = c.append(ev)
    assert r1["status"] == "appended"
    assert r2["status"] == "duplicate"
    text = (wiki_dir / "log.md").read_text(encoding="utf-8")
    assert text.count("**ingest**") == 1


def test_rebuild_sorts_and_dedups(wiki_dir):
    c = WikiLogCompiler()
    events = [
        {"type": "ingest", "target": "B", "timestamp": "2026-07-02T12:00:00", "detail": "d"},
        {"type": "ingest", "target": "A", "timestamp": "2026-07-02T10:00:00", "detail": "d"},
        {"type": "ingest", "target": "A", "timestamp": "2026-07-02T10:00:00", "detail": "d"},
    ]
    result = c.rebuild(events)
    assert result["entries"] == 2
    text = (wiki_dir / "log.md").read_text(encoding="utf-8")
    assert text.index("2026-07-02T10:00:00") < text.index("2026-07-02T12:00:00")
