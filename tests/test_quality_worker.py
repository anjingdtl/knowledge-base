"""Knowledge quality repair regression tests."""

from unittest.mock import MagicMock

from src.gui.knowledge_view import QualityWorker
from src.services.file_parser import ParsedFile


def test_quality_worker_repairs_from_first_parsed_file(monkeypatch, tmp_path):
    source = tmp_path / "repaired.md"
    source.write_text("placeholder", encoding="utf-8")
    parsed = ParsedFile(
        title="repaired",
        content="Readable repaired content",
        file_type="md",
        source_path=str(source),
        metadata={},
    )
    graph_service = MagicMock()

    monkeypatch.setattr(
        "src.services.file_parser.parse_file",
        lambda path: [parsed],
    )
    monkeypatch.setattr(
        "src.gui.knowledge_view._file_graph_service",
        lambda: graph_service,
    )

    repaired = QualityWorker()._try_repair(
        {"id": "item-1", "title": "Old title", "content": "", "source_path": str(source)}
    )

    assert repaired is True
    graph_service.update_page.assert_called_once()
