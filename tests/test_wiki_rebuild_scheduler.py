"""RebuildScheduler per-kid debounce 合并测试(Phase 5)。"""
from src.services.wiki_rebuild_scheduler import RebuildScheduler


class _FakeRebuild:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def rebuild(self, knowledge_id, *, event, **kw):
        self.calls.append((knowledge_id, event))
        return type("R", (), {"committed": True, "cancelled": False, "warnings": []})()


def test_update_update_merges_to_single_update():
    svc = _FakeRebuild()
    sch = RebuildScheduler(rebuild_service=svc, debounce_ms=0)
    sch.schedule("k1", "update")
    sch.schedule("k1", "update")
    sch.flush()
    assert svc.calls == [("k1", "update")]


def test_update_then_delete_merges_to_delete():
    svc = _FakeRebuild()
    sch = RebuildScheduler(rebuild_service=svc, debounce_ms=0)
    sch.schedule("k1", "update")
    sch.schedule("k1", "delete")  # delete 主导
    sch.flush()
    assert svc.calls == [("k1", "delete")]


def test_distinct_kids_not_merged():
    svc = _FakeRebuild()
    sch = RebuildScheduler(rebuild_service=svc, debounce_ms=0)
    sch.schedule("k1", "update")
    sch.schedule("k2", "delete")
    assert sch.pending_count == 2
    sch.flush()
    assert sorted(svc.calls) == [("k1", "update"), ("k2", "delete")]


def test_pending_count_after_delete_dominating_update():
    svc = _FakeRebuild()
    sch = RebuildScheduler(rebuild_service=svc, debounce_ms=0)
    sch.schedule("k1", "update")
    sch.schedule("k1", "delete")
    sch.schedule("k1", "update")  # delete + update → delete(不 drop)
    assert sch.pending_count == 1


def test_flush_clears_pending_and_returns_result():
    svc = _FakeRebuild()
    sch = RebuildScheduler(rebuild_service=svc, debounce_ms=0)
    sch.schedule("k1", "update")
    result = sch.flush()
    assert sch.pending_count == 0
    assert result.processed == 1
    assert result.failed == []


def test_invalid_event_ignored():
    svc = _FakeRebuild()
    sch = RebuildScheduler(rebuild_service=svc, debounce_ms=0)
    sch.schedule("k1", "bogus")  # 非 update/delete → 忽略
    assert sch.pending_count == 0
