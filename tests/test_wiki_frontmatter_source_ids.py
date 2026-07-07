"""frontmatter source_ids 统一写入测试(双轨收敛 Task 2)。"""

from src.services.wiki_slug import read_frontmatter


def test_source_compiler_writes_source_ids(tmp_path, monkeypatch):
    """sources 页 compile 后 frontmatter 含 source_ids = [kid]。"""
    from src.services.wiki_source_compiler import WikiSourceCompiler

    item = {"id": "k1", "title": "Test Title", "content": "Some content here",
            "source_path": "f.md", "file_type": "md", "content_hash": "h1"}
    monkeypatch.setattr(
        "src.services.wiki_source_compiler.Database",
        type("DB", (), {"get_knowledge": staticmethod(lambda k: item)})())
    sources_dir = tmp_path / "sources"

    class _FC:
        @staticmethod
        def get(key, default=None):
            return str(sources_dir) if key == "knowledge_workflow.source_summary_dir" else default

    monkeypatch.setattr("src.services.wiki_source_compiler.Config", _FC)

    WikiSourceCompiler().compile("k1", "2026-07-07")
    md = next(sources_dir.glob("*.md"))
    fm = read_frontmatter(md)
    assert fm.get("source_ids") == ["k1"]
    assert fm.get("knowledge_id") == "k1"  # 向后兼容保留


def test_entity_page_writes_source_ids(tmp_path):
    """entities/concepts 页 _write_entity_page 写 source_ids = [kid]。"""
    from src.services.wiki_entity_updater import WikiEntityUpdater

    parsed = {"summary": "S", "facts": [], "contradictions": []}
    WikiEntityUpdater._write_entity_page(
        tmp_path, "EntityX", "entity", parsed, "k1", "2026-07-07", False)
    fm = read_frontmatter(tmp_path / "entityx.md")
    assert fm.get("source_ids") == ["k1"]
    assert fm.get("knowledge_id") == "k1"
