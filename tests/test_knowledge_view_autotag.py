"""GUI 智能补标的取消与收尾回归测试。"""
import os
import sys
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


def test_autotag_worker_finishes_after_interruption(qapp, monkeypatch):
    """用户取消后，worker 不处理更多条目且仍发送自己的完成信号。"""
    from src.gui.knowledge_view import AutoTagWorker, Database

    monkeypatch.setattr(Database, "get_all_tags", lambda cls: [])
    worker = AutoTagWorker(items=[{"id": "x", "title": "x", "tags": []}], use_llm=True)
    worker.requestInterruption()
    completed = []
    worker.finished.connect(lambda *args: completed.append(args))

    worker.run()

    assert completed == [(0, 0, 1)]


def test_autotag_worker_finishes_after_unexpected_error(qapp, monkeypatch):
    """基础设施异常不能让 GUI 永久停在进行中状态。"""
    from src.gui.knowledge_view import AutoTagWorker, Database

    monkeypatch.setattr(Database, "get_all_tags", lambda cls: (_ for _ in ()).throw(RuntimeError("db down")))
    worker = AutoTagWorker(items=[{"id": "x", "title": "x", "tags": []}])
    completed = []
    worker.finished.connect(lambda *args: completed.append(args))

    worker.run()

    assert completed == [(0, 0, 1)]


class _Signal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in self._callbacks:
            callback(*args)


class _FakeProgress:
    instances = []

    def __init__(self, *args):
        self.canceled = _Signal()
        self.closed = False
        self.__class__.instances.append(self)

    def setWindowModality(self, *args):
        pass

    def setMinimumDuration(self, *args):
        pass

    def setCancelButton(self, *args):
        pass

    def setValue(self, *args):
        pass

    def setLabelText(self, *args):
        pass

    def close(self):
        self.closed = True


class _FakeWorker:
    instance = None

    def __init__(self, *args, **kwargs):
        self.progress = _Signal()
        self.finished = _Signal()
        self.interrupted = False
        self.started = False
        self.__class__.instance = self

    def requestInterruption(self):
        self.interrupted = True

    def start(self):
        self.started = True


class _FakeAction:
    def __init__(self):
        self.enabled = True

    def setEnabled(self, value):
        self.enabled = value


def test_autotag_progress_cancel_requests_worker_interruption(qapp, monkeypatch):
    """进度窗的“取消”按钮应请求 worker 在当前条目完成后停止。"""
    import src.gui.knowledge_view as view_module

    _FakeProgress.instances.clear()
    monkeypatch.setattr(view_module.Database, "list_knowledge", lambda cls, limit: [{"id": "x", "tags": []}])
    monkeypatch.setattr(view_module.QMessageBox, "question", lambda *args: view_module.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(view_module, "QProgressDialog", _FakeProgress)
    monkeypatch.setattr(view_module, "AutoTagWorker", _FakeWorker)
    view = SimpleNamespace(act_autotag=_FakeAction())

    view_module.KnowledgeView._auto_tag(view)
    _FakeProgress.instances[0].canceled.emit()

    assert _FakeWorker.instance.started is True
    assert _FakeWorker.instance.interrupted is True


def test_autotag_completion_reports_cancellation_and_restores_action(qapp, monkeypatch):
    """取消后的收尾必须恢复入口，并明确告知用户。"""
    import src.gui.knowledge_view as view_module

    messages = []
    monkeypatch.setattr(view_module.Database, "list_knowledge", lambda cls, limit: [])
    monkeypatch.setattr(view_module.QMessageBox, "information", lambda *args: messages.append(args[1:]))
    action = _FakeAction()
    action.setEnabled(False)
    view = SimpleNamespace(
        act_autotag=action,
        _autotag_worker=SimpleNamespace(was_cancelled=True),
        _load_knowledge=lambda: None,
    )

    view_module.KnowledgeView._on_autotag_finished(view, 0, 0, 1)

    assert action.enabled is True
    assert messages[0][0] == "补标已取消"


def test_autotag_worker_disables_llm_after_runtime_failure(qapp, monkeypatch):
    """一条目的网络失败不能让每个后续条目都等待 LLM 超时。"""
    import src.services.tag_inference as tag_inference
    from src.gui.knowledge_view import AutoTagWorker, Config, Database

    calls = []

    def fake_infer_tags(item, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            kwargs["on_llm_error"](RuntimeError("connection refused"))
        return []

    monkeypatch.setattr(Database, "get_all_tags", lambda cls: [])
    monkeypatch.setattr(Config, "get", lambda key, default: 7)
    monkeypatch.setattr(tag_inference, "infer_tags", fake_infer_tags)
    worker = AutoTagWorker(
        items=[
            {"id": "first", "title": "first", "tags": []},
            {"id": "second", "title": "second", "tags": []},
        ],
        use_llm=True,
    )

    worker.run()

    assert [call["use_llm"] for call in calls] == [True, False]
    assert [call["llm_timeout"] for call in calls] == [7.0, 7.0]
    assert worker.llm_fallback_disabled is True
    assert "connection refused" in worker.llm_error
