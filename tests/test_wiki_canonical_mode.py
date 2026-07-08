"""Wiki canonical v2 模式状态机测试(Phase 3.5 / C5)。

验证 off/shadow/canary/primary 四态:
- resolve_canonical_mode 默认 off + 显式 + 向后兼容旧键 + 非法回退
- off 模式 projection disabled(legacy 零变化)
- 非 off 模式 projection enabled
- 模式切换不改 DB 表结构
- off 模式 wiki 查询走 FS(legacy 仍工作)
"""
from __future__ import annotations

import sqlite3

import pytest

from src.models.wiki_v2 import PageStatus, PageType, WikiPage
from src.services.wiki_projection import WikiProjection
from src.services.wiki_query_service import (
    CANONICAL_MODES,
    WikiCanonicalMode,
    WikiQueryService,
    resolve_canonical_mode,
)
from src.services.wiki_repository import WikiRepository

NOW = "2026-07-08T12:00:00+08:00"


class _FakeDB:
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


def _page(page_id="page_1", title="FTTR") -> WikiPage:
    return WikiPage(
        schema_version=2, page_id=page_id, title=title, page_type=PageType.CONCEPTS,
        status=PageStatus.DRAFT, revision=1, aliases=[], tags=[], source_ids=["k1"],
        claim_ids=[], created_at=NOW, updated_at=NOW,
        content_hash="sha256:x", body="# FTTR\n",
    )


@pytest.fixture
def repo(tmp_path):
    return WikiRepository(
        wiki_dir=tmp_path / "wiki",
        registry_path=tmp_path / "wiki" / "_meta" / "pages.json",
        redirects_path=tmp_path / "wiki" / "_meta" / "redirects.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )


# ===========================================================================
# 1. resolve_canonical_mode
# ===========================================================================
def test_default_is_off():
    assert resolve_canonical_mode({}) == "off"
    assert resolve_canonical_mode(None) == "off"


def test_explicit_modes():
    for m in ("off", "shadow", "canary", "primary"):
        assert resolve_canonical_mode({"wiki.canonical_v2.mode": m}) == m


def test_legacy_compat_enabled_to_primary():
    """旧 canonical_v2.enabled=true → primary(向后兼容)。"""
    assert resolve_canonical_mode({"canonical_v2.enabled": True}) == "primary"


def test_legacy_compat_disabled_to_off():
    assert resolve_canonical_mode({"canonical_v2.enabled": False}) == "off"


def test_mode_takes_precedence_over_legacy():
    """新 mode 键优先于旧 enabled。"""
    cfg = {"wiki.canonical_v2.mode": "shadow", "canonical_v2.enabled": True}
    assert resolve_canonical_mode(cfg) == "shadow"


def test_invalid_value_falls_back_to_default():
    assert resolve_canonical_mode({"wiki.canonical_v2.mode": "bogus"}) == "off"


def test_canonical_modes_tuple():
    assert set(CANONICAL_MODES) == {"off", "shadow", "canary", "primary"}
    assert WikiCanonicalMode.OFF.value == "off"
    assert WikiCanonicalMode.PRIMARY.value == "primary"


# ===========================================================================
# 2. 模式 → projection enabled
# ===========================================================================
def test_off_disables_projection(repo):
    db = _FakeDB()
    proj = WikiProjection(repository=repo, database=db,
                          enabled=resolve_canonical_mode({}) != "off")
    assert proj.enabled is False  # off → legacy 零变化


def test_non_off_enables_projection(repo):
    db = _FakeDB()
    for mode in ("shadow", "canary", "primary"):
        cfg = {"wiki.canonical_v2.mode": mode}
        proj = WikiProjection(repository=repo, database=db,
                              enabled=resolve_canonical_mode(cfg) != "off")
        assert proj.enabled is True


# ===========================================================================
# 3. 模式切换不改 DB 结构 + off 退回 legacy 仍工作
# ===========================================================================
def test_mode_switch_no_db_schema_change(repo):
    """off → primary → off,DB 表结构不变(projection rebuild 只动数据)。"""
    db = _FakeDB()
    proj = WikiProjection(repository=repo, database=db, enabled=True)
    repo.save_page(_page())

    def table_names():
        rows = db.get_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return tuple(r[0] for r in rows)

    before = table_names()
    proj.rebuild()
    proj.rebuild()  # 重复 rebuild
    after = table_names()
    assert before == after  # 结构不变


def test_off_legacy_query_still_works(repo):
    """off 模式 wiki 查询走 FS(legacy 行为不变)。"""
    db = _FakeDB()
    proj = WikiProjection(repository=repo, database=db, enabled=False)  # off
    qs = WikiQueryService(repository=repo, projection=proj, database=db)
    repo.save_page(_page())
    page = qs.get_page("page_1")  # projection disabled → FS
    assert page is not None
    candidates, warnings = qs.search_pages("FTTR")
    assert candidates and candidates[0].match_source == "filesystem"
    assert any("projection disabled" in w for w in warnings)


def test_off_to_primary_to_off_roundtrip(repo):
    """canary/off 切换:projection enabled 状态可来回切换,DB 结构稳定。"""
    db = _FakeDB()
    repo.save_page(_page())
    # off
    proj_off = WikiProjection(repository=repo, database=db, enabled=False)
    assert proj_off.enabled is False
    # primary
    proj_on = WikiProjection(repository=repo, database=db, enabled=True)
    proj_on.rebuild()
    assert proj_on.verify_parity() == []
    # 退回 off:projection 不再 enabled,但 FS 数据仍在,legacy 读仍工作
    qs = WikiQueryService(repository=repo, projection=proj_off, database=db)
    assert qs.get_page("page_1") is not None
