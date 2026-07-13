"""WikiWriteService 统一双写测试(双轨收敛 Task 3)。"""
from src.models.wiki_v2 import PageStatus, PageType
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


class _FakeConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _FakeCanonicalRepo:
    def __init__(self):
        self.saved_pages = []

    def save_page(self, page, expected_revision=None):
        self.saved_pages.append((page, expected_revision))
        return type("SaveResult", (), {"ok": True, "object_id": page.page_id, "revision": 1})()


class _FakeProjection:
    def __init__(self):
        self.processed = 0

    def process_outbox(self):
        self.processed += 1
        return type("ProjectionResult", (), {"processed": 1, "errors": [], "warnings": []})()


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


def test_primary_save_uses_canonical_repository_without_legacy_double_write():
    compiler = _FakeCompiler()
    workflow = _FakeWorkflow()
    repo = _FakeCanonicalRepo()
    projection = _FakeProjection()
    svc = WikiWriteService(
        compiler,
        workflow,
        repository=repo,
        projection=projection,
        config=_FakeConfig({"wiki.canonical_v2.mode": "primary"}),
    )

    result = svc.save(
        "What is FTTR?",
        "FTTR answer body" + "x" * 120,
        ["k1"],
        confidence=0.8,
        timestamp="2026-07-09T12:00:00",
    )

    assert result["sqlite_page_id"] is None
    assert result["fs_saved"] is False
    assert result["canonical_saved"] is True
    assert result["page_id"].startswith("page_")
    assert result["projection_pending"] is False
    assert result["projection_processed"] == 1
    assert compiler.called is None
    assert workflow.called is None
    page = repo.saved_pages[0][0]
    assert page.page_type == PageType.SYNTHESES
    assert page.status == PageStatus.DRAFT
    assert page.source_ids == ["k1"]
    assert "FTTR answer body" in page.body
