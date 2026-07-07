"""WikiReadStage SQLite fallback 测试(双轨收敛 Task 4)。"""
from src.services.rag_pipeline import WikiReadStage


class _FakeDb:
    def __init__(self, rows=None, raise_exc=None):
        self.rows = rows or []
        self.raise_exc = raise_exc

    def search_wiki_fts(self, query, limit=10):
        if self.raise_exc:
            raise self.raise_exc
        return self.rows


def test_sqlite_fallback_converts_rows():
    """rows → wiki 候选 schema(JSON source_ids 解析为 list)。"""
    stage = WikiReadStage()
    rows = [{"id": "p1", "title": "P1", "content": "C",
             "source_ids": '["k1","k2"]', "concept_summary": "CS",
             "fts_rank": -0.5}]
    cands = stage._sqlite_fallback("q", db=_FakeDb(rows))
    assert len(cands) == 1
    c = cands[0]
    assert c["id"] == "wiki:sqlite:p1"
    assert c["text"] == "C"
    assert c["metadata"]["source_ids"] == ["k1", "k2"]
    assert c["metadata"]["title"] == "P1"
    assert c["metadata"]["page_type"] == "sqlite_concept"
    assert c["match_channels"] == ["wiki_sqlite"]


def test_sqlite_fallback_handles_db_error():
    """Database 异常时返回 [](容错,不抛)。"""
    stage = WikiReadStage()
    cands = stage._sqlite_fallback("q", db=_FakeDb(raise_exc=RuntimeError("down")))
    assert cands == []


def test_sqlite_fallback_empty_rows():
    stage = WikiReadStage()
    cands = stage._sqlite_fallback("q", db=_FakeDb([]))
    assert cands == []


def test_sqlite_fallback_falls_back_to_content_when_no_concept_summary():
    stage = WikiReadStage()
    rows = [{"id": "p2", "title": "P2", "content": "Body",
             "source_ids": "[]", "concept_summary": "", "fts_rank": 0}]
    cands = stage._sqlite_fallback("q", db=_FakeDb(rows))
    assert cands[0]["text"] == "Body"
    assert cands[0]["metadata"]["source_ids"] == []
