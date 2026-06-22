import json

from src.models.knowledge import KnowledgeItem
from src.services.db import Database
from src.services.file_graph import FileGraphService


def _service(tmp_path):
    from src.services.block_store import BlockStore
    from src.utils.config import Config

    Config.set("storage.graph_dir", str(tmp_path / "graph"))
    return FileGraphService(Config, Database, BlockStore(db=Database), embedding=None)


def test_create_page_writes_markdown_and_rebuilds_cache(tmp_path):
    service = _service(tmp_path)

    page_id = service.create_page("Graph Test", "Parent\nChild", tags=["t1"])
    item = Database.get_knowledge(page_id)
    blocks = Database.get_chunks_by_knowledge(page_id)

    assert item["source_path"].endswith(".md")
    assert "Graph Test" in item["title"]
    assert len(blocks) == 2
    assert "id::" in open(item["source_path"], encoding="utf-8").read()


def test_sync_page_adds_missing_block_ids_and_searches_fts(tmp_path):
    service = _service(tmp_path)
    root = service.ensure_graph()
    path = root / "pages" / "manual--abc.md"
    path.write_text("id:: page-manual\ntitle:: Manual\ntags:: x\n\n- 独特搜索词\n", encoding="utf-8")

    result = service.sync_page(str(path))
    rows = Database.search_blocks_fts("独特搜索词", limit=5)

    assert result["id"] == "page-manual"
    assert rows and rows[0]["page_id"] == "page-manual"
    assert "id::" in path.read_text(encoding="utf-8")


def test_sync_all_deletes_cache_for_removed_manifest_page(tmp_path):
    service = _service(tmp_path)
    page_id = service.create_page("Delete Me", "content")
    item = Database.get_knowledge(page_id)
    path = item["source_path"]

    service.sync_all()
    import os
    os.remove(path)
    result = service.sync_all()

    assert result["deleted"] == 1
    assert Database.get_knowledge(page_id) is None


def test_export_db_to_graph_dry_run_and_apply(tmp_path):
    service = _service(tmp_path)
    item = KnowledgeItem(title="Existing", content="A\nB", tags=["old"])
    Database.insert_knowledge(item.to_row())

    dry = service.export_db_to_graph(dry_run=True)
    assert dry["count"] == 1
    assert not list((service.ensure_graph() / "pages").glob("*.md"))

    applied = service.export_db_to_graph(dry_run=False, backup=False)
    files = list((service.ensure_graph() / "pages").glob("*.md"))
    manifest = json.loads((service.ensure_graph() / ".kb" / "manifest.json").read_text(encoding="utf-8"))

    assert applied["count"] == 1
    assert files
    assert item.id in manifest


def test_create_page_preserves_file_type_metadata(tmp_path):
    """BUG-7: create_page 应保留入参 file_type，否则 sync_page 会 fallback 为 'md'，
    导致 list_knowledge(file_type='pdf') 查不到 PDF 条目。"""
    service = _service(tmp_path)

    page_id = service.create_page(
        "PDF Doc",
        "PDF 内容",
        metadata={"source_type": "file", "file_type": "pdf"},
    )
    item = Database.get_knowledge(page_id)

    assert item["file_type"] == "pdf"
