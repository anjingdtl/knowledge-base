"""WikiWriteService 统一双写测试(双轨收敛 Task 3)。"""
from src.services.wiki_write_service import WikiWriteService


class _FakeCompiler:
    def __init__(self, raise_exc=None):
        self.called = None
        self.raise_exc = raise_exc

    def save_answer(self, q, a, sids, auto_publish=None, enhance=True):
        self.called = (q, a, sids, auto_publish, enhance)
        if self.raise_exc:
            raise self.raise_exc
        return "sqlite-page-1"


class _FakeWorkflow:
    def __init__(self, raise_exc=None):
        self.called = None
        self.raise_exc = raise_exc

    def save_query(self, q, a, sids, confidence=0.0, save_mode="manual", timestamp=""):
        self.called = (q, a, sids, confidence, save_mode, timestamp)
        if self.raise_exc:
            raise self.raise_exc


def test_save_writes_both_tracks():
    c, w = _FakeCompiler(), _FakeWorkflow()
    svc = WikiWriteService(c, w)
    r = svc.save("Q", "A", ["k1"], confidence=0.8, save_mode="manual", timestamp="t1")
    assert r["sqlite_page_id"] == "sqlite-page-1"
    assert r["fs_saved"] is True
    assert r["errors"] == []
    assert c.called[2] == ["k1"]
    assert w.called[3] == 0.8


def test_save_fs_failure_does_not_block_sqlite():
    c, w = _FakeCompiler(), _FakeWorkflow(raise_exc=RuntimeError("fs boom"))
    svc = WikiWriteService(c, w)
    r = svc.save("Q", "A", ["k1"])
    assert r["sqlite_page_id"] == "sqlite-page-1"  # A 仍成功
    assert r["fs_saved"] is False
    assert any("fs:" in e for e in r["errors"])


def test_save_sqlite_failure_does_not_block_fs():
    c, w = _FakeCompiler(raise_exc=RuntimeError("sql boom")), _FakeWorkflow()
    svc = WikiWriteService(c, w)
    r = svc.save("Q", "A", ["k1"])
    assert r["sqlite_page_id"] is None
    assert r["fs_saved"] is True
    assert any("sqlite:" in e for e in r["errors"])
