"""WikiQueryService 契约测试(Phase 3.5 / C4)。

验证统一 wiki 读取端口:
- 读取顺序一致(projection → FS → legacy SQLite)
- 候选 schema 一致(page_id/title/status/claim_ids/source_ids/revision/match_source/warnings)
- wiki_pages_v2_fts 被消费(消除 C0 审计的零读取)
- projection_drift warning 统一
"""
from __future__ import annotations

import sqlite3

import pytest

from src.models.wiki_v2 import ClaimStatus, Evidence, EvidenceStance, PageStatus, PageType, WikiPage
from src.services.wiki_projection import WikiProjection
from src.services.wiki_query_service import WikiQueryService, WikiReadHealth
from src.services.wiki_repository import WikiRepository

NOW = "2026-07-08T12:00:00+08:00"


class _FakeDB:
    """内存 SQLite,建全 v2 表 + fts 虚拟表,复用连接。"""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        for ddl in [
            "CREATE TABLE wiki_pages_v2 (page_id TEXT PRIMARY KEY, path TEXT, title TEXT, page_type TEXT, status TEXT, revision INTEGER, content TEXT, content_hash TEXT, aliases_json TEXT, tags_json TEXT, source_ids_json TEXT, claim_ids_json TEXT, created_at TEXT, updated_at TEXT)",
            "CREATE TABLE wiki_claims (claim_id TEXT PRIMARY KEY, statement TEXT, normalized_statement TEXT, claim_type TEXT, status TEXT, confidence REAL, claim_scope TEXT, valid_from TEXT, valid_to TEXT, revision INTEGER, created_at TEXT, updated_at TEXT)",
            "CREATE TABLE wiki_claim_evidence (evidence_id TEXT PRIMARY KEY, claim_id TEXT, stance TEXT, knowledge_id TEXT, block_id TEXT, location_json TEXT, source_revision TEXT, excerpt_hash TEXT, observed_at TEXT)",
            "CREATE TABLE wiki_page_claims (page_id TEXT, claim_id TEXT, display_order INTEGER)",
            "CREATE TABLE wiki_dependencies (from_type TEXT, from_id TEXT, to_type TEXT, to_id TEXT, relation TEXT)",
            "CREATE TABLE wiki_projection_state (key TEXT PRIMARY KEY, value TEXT)",
            "CREATE VIRTUAL TABLE wiki_pages_v2_fts USING fts5(page_id UNINDEXED, title, content, tokenize='unicode61')",
        ]:
            self._conn.execute(ddl)
        self._conn.commit()

    def get_conn(self):
        return self._conn

    def search_wiki_fts(self, query, limit=10):
        return [{"id": "legacy_1", "title": "Legacy " + query, "status": "published", "content": "x"}]


def _page(page_id="page_fttr", title="FTTR Architecture", body="# FTTR\nfiber to the room") -> WikiPage:
    return WikiPage(
        schema_version=2, page_id=page_id, title=title, page_type=PageType.CONCEPTS,
        status=PageStatus.PUBLISHED, revision=1, aliases=[], tags=[], source_ids=["k1"],
        claim_ids=["claim_1"], created_at=NOW, updated_at=NOW,
        content_hash="sha256:x", body=body, supersedes_page_id=None,
    )


@pytest.fixture
def stack(tmp_path):
    repo = WikiRepository(
        wiki_dir=tmp_path / "wiki",
        registry_path=tmp_path / "wiki" / "_meta" / "pages.json",
        redirects_path=tmp_path / "wiki" / "_meta" / "redirects.json",
        outbox_path=tmp_path / "wiki_projection_outbox.jsonl",
    )
    db = _FakeDB()
    proj = WikiProjection(repository=repo, database=db, enabled=True)
    qs = WikiQueryService(repository=repo, projection=proj, database=db)
    return repo, proj, qs


# ===========================================================================
# 1. 读取顺序:projection-first,FS fallback
# ===========================================================================
def test_get_page_projection_first(stack):
    repo, proj, qs = stack
    repo.save_page(_page())
    proj.rebuild()  # 灌 v2 表 + fts
    page = qs.get_page("page_fttr")
    assert page is not None
    assert page.page_id == "page_fttr"
    assert page.title == "FTTR Architecture"


def test_get_page_fs_fallback_when_projection_misses(stack):
    repo, proj, qs = stack
    repo.save_page(_page())  # 不 rebuild(projection 无)→ FS fallback
    page = qs.get_page("page_fttr")
    assert page is not None  # FS 兜底
    assert page.title == "FTTR Architecture"


def test_get_page_fs_when_projection_disabled(tmp_path):
    repo = WikiRepository(
        wiki_dir=tmp_path / "wiki", registry_path=tmp_path / "wiki" / "_meta" / "pages.json",
        redirects_path=tmp_path / "wiki" / "_meta" / "redirects.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )
    proj = WikiProjection(repository=repo, database=_FakeDB(), enabled=False)
    qs = WikiQueryService(repository=repo, projection=proj)
    repo.save_page(_page())
    page = qs.get_page("page_fttr")
    assert page is not None  # projection disabled → FS


# ===========================================================================
# 2. search_pages 消费 wiki_pages_v2_fts(消除零读取)
# ===========================================================================
def test_search_pages_consumes_fts_projection_first(stack):
    repo, proj, qs = stack
    repo.save_page(_page())
    proj.rebuild()
    candidates, warnings = qs.search_pages("FTTR", limit=5)
    assert len(candidates) == 1
    assert candidates[0].match_source == "projection"  # 来自 fts/projection
    assert candidates[0].page_id == "page_fttr"


def test_search_pages_fs_fallback_when_disabled(tmp_path):
    repo = WikiRepository(
        wiki_dir=tmp_path / "wiki", registry_path=tmp_path / "wiki" / "_meta" / "pages.json",
        redirects_path=tmp_path / "wiki" / "_meta" / "redirects.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )
    proj = WikiProjection(repository=repo, database=_FakeDB(), enabled=False)
    qs = WikiQueryService(repository=repo, projection=proj)
    repo.save_page(_page())
    candidates, warnings = qs.search_pages("FTTR", limit=5)
    assert candidates and candidates[0].match_source == "filesystem"
    assert any("projection disabled" in w for w in warnings)


def test_search_pages_legacy_sqlite_fallback(tmp_path):
    repo = WikiRepository(
        wiki_dir=tmp_path / "wiki", registry_path=tmp_path / "wiki" / "_meta" / "pages.json",
        redirects_path=tmp_path / "wiki" / "_meta" / "redirects.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )
    proj = WikiProjection(repository=repo, database=_FakeDB(), enabled=False)
    qs = WikiQueryService(repository=repo, projection=proj, database=_FakeDB())
    # FS 空 + projection disabled → legacy SQLite
    candidates, warnings = qs.search_pages("missing", limit=5)
    assert candidates and candidates[0].match_source == "legacy_sqlite"
    assert any("legacy" in w for w in warnings)


# ===========================================================================
# 3. 候选 schema 一致(C4 契约)
# ===========================================================================
def test_candidate_schema_consistent_across_sources(stack):
    repo, proj, qs = stack
    repo.save_page(_page())
    proj.rebuild()
    candidates, _ = qs.search_pages("FTTR", limit=5)
    c = candidates[0]
    # 统一 schema 字段全齐
    assert c.page_id and c.title and c.page_type == "concepts"
    assert c.status == "published"
    assert c.claim_ids == ["claim_1"]
    assert c.source_ids == ["k1"]
    assert c.revision >= 1
    assert c.match_source in ("projection", "filesystem", "legacy_sqlite")
    assert isinstance(c.warnings, list)
    assert isinstance(c.to_dict(), dict)


def test_same_query_same_candidates(stack):
    """相同 query + 配置 → 候选集合一致(契约)。"""
    repo, proj, qs = stack
    repo.save_page(_page())
    proj.rebuild()
    c1, _ = qs.search_pages("FTTR")
    c2, _ = qs.search_pages("FTTR")
    assert [c.page_id for c in c1] == [c.page_id for c in c2]


# ===========================================================================
# 4. health / projection_drift
# ===========================================================================
def test_health_healthy_after_rebuild(stack):
    repo, proj, qs = stack
    repo.save_page(_page())
    proj.rebuild()
    h = qs.health()
    assert isinstance(h, WikiReadHealth)
    assert h.projection_enabled is True
    assert h.projection_status == "healthy"
    assert h.drift_count == 0
    assert h.page_count >= 1


def test_health_reports_drift_when_fs_diverges(stack):
    repo, proj, qs = stack
    repo.save_page(_page())
    proj.rebuild()
    # FS 再写一页,projection 未同步 → drift
    repo.save_page(_page(page_id="page_new", title="New Page", body="new"))
    h = qs.health()
    assert h.projection_status == "stale"
    assert h.drift_count >= 1
    assert any("projection_drift" in w for w in h.warnings)


def test_get_claim_reads_canonical_fs(stack):
    from src.models.wiki_v2 import Claim

    repo, proj, qs = stack
    repo.save_claim(Claim(
        schema_version=1, claim_id="claim_1", statement="s", normalized_statement="s",
        claim_type="fact", status=ClaimStatus.ACTIVE, confidence=0.9, valid_from=None, valid_to=None,
        subject_refs=[], predicate="", object_refs=[],
        evidence=[Evidence(evidence_id="ev1", stance=EvidenceStance.SUPPORTS, knowledge_id="k1", block_id="b1")],
        relations=[], created_at=NOW, updated_at=NOW, revision=1,
    ))
    got = qs.get_claim("claim_1")
    assert got is not None and got.claim_id == "claim_1"
