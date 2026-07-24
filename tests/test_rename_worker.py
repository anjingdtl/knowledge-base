"""Regression tests for batched GUI smart rename."""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


def test_generate_titles_batch_uses_one_request_and_falls_back_for_missing_result(monkeypatch):
    import src.gui.import_dialog as dialog

    calls = []

    class FakeLLM:
        def chat(self, messages, **kwargs):
            calls.append((messages, kwargs))
            return '[{"id":"one","needs_supplement":true,"supplement":"实施方案"}]'

    monkeypatch.setattr(dialog, "LLMService", FakeLLM)

    titles = dialog.generate_titles_batch([
        {"id": "one", "filename": "项目", "content": "项目实施的完整方案"},
        {"id": "two", "filename": "报告", "content": "经营分析报告"},
    ])

    assert titles == {"one": "项目（实施方案）", "two": "报告"}
    assert len(calls) == 1
    assert calls[0][1]["max_tokens_override"] == 800
    payload = json.loads(calls[0][0][0]["content"].split("## 输入条目 JSON\n", 1)[1])
    assert [item["id"] for item in payload] == ["one", "two"]


def test_rename_worker_sends_items_in_batches_of_ten(qapp, monkeypatch):
    import src.gui.import_dialog as dialog
    from src.gui.knowledge_view import Database, RenameWorker

    batches = []
    updates = []

    def fake_generate_titles_batch(items):
        batches.append(items)
        return {item["id"]: f"新标题-{item['id']}" for item in items}

    monkeypatch.setattr(dialog, "generate_titles_batch", fake_generate_titles_batch)
    monkeypatch.setattr(
        Database,
        "update_knowledge",
        lambda _db, item_id, **fields: updates.append((item_id, fields["title"])),
    )
    items = [
        {
            "id": str(index),
            "title": f"旧标题-{index}",
            "content": "测试正文",
            "source_path": fr"C:\docs\file-{index}.txt",
        }
        for index in range(11)
    ]

    worker = RenameWorker(items=items)
    worker._do_rename()

    assert [len(batch) for batch in batches] == [10, 1]
    assert [item["id"] for item in batches[0]] == [str(index) for index in range(10)]
    assert len(updates) == 11
    assert worker._renamed == 11
