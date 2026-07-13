from unittest.mock import MagicMock

from src.services.wiki_compiler import WikiCompiler
from src.utils.config import Config


def test_save_answer_primary_delegates_to_write_service(monkeypatch):
    Config.set("wiki.canonical_v2.mode", "primary")
    write_service = MagicMock()
    write_service.save.return_value = {
        "page_id": "page_primary",
        "sqlite_page_id": None,
        "canonical_saved": True,
        "fs_saved": False,
        "errors": [],
    }
    container = MagicMock()
    container.wiki_write_service = write_service
    monkeypatch.setattr("src.core.container.get_active_container", lambda: container)

    result = WikiCompiler().save_answer(
        "Q",
        "A" * 120,
        ["k1"],
        auto_publish=False,
        enhance=False,
    )

    assert result == "page_primary"
    write_service.save.assert_called_once()
    call = write_service.save.call_args
    assert call.args[:3] == ("Q", "A" * 120, ["k1"])
    assert call.kwargs["auto_publish"] is False
    assert call.kwargs["enhance"] is False
