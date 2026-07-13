"""frontmatter source_ids 统一写入测试(双轨收敛 Task 2)。"""


def test_source_compiler_prepares_source_ids(tmp_path, monkeypatch):
    """sources 页建议载荷 frontmatter 含 source_ids = [kid]。"""
    from src.services.wiki_source_compiler import WikiSourceCompiler

    item = {"id": "k1", "title": "Test Title", "content": "Some content here",
            "source_path": "f.md", "file_type": "md", "content_hash": "h1"}
    monkeypatch.setattr(
        "src.services.wiki_source_compiler.Database",
        type("DB", (), {"get_knowledge": staticmethod(lambda k: item)})())

    result = WikiSourceCompiler().compile("k1", "2026-07-07")
    assert result["frontmatter"]["source_ids"] == ["k1"]
    assert result["frontmatter"]["knowledge_id"] == "k1"  # 向后兼容保留
    assert not (tmp_path / "sources").exists()


def test_entity_suggestion_preserves_source_ids(tmp_path):
    """entities/concepts 建议保留 source_ids = [kid]。"""
    from src.services.wiki_entity_updater import WikiEntityUpdater

    parsed = {"summary": "S", "facts": [], "contradictions": []}
    suggestion = WikiEntityUpdater._build_entity_suggestion(
        tmp_path, "EntityX", "entity", parsed, "k1", "2026-07-07", False)
    assert suggestion["frontmatter"]["source_ids"] == ["k1"]
    assert suggestion["frontmatter"]["knowledge_id"] == "k1"
    assert suggestion["source_ids"] == ["k1"]
    assert not (tmp_path / "entityx.md").exists()
