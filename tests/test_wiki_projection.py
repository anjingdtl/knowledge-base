"""WikiProjection 服务测试 — 10 个测试覆盖 outbox 消费、rebuild、parity、FTS、disabled。"""
import json

import pytest

from src.models.wiki_v2 import (
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    PageStatus,
    PageType,
    WikiPage,
)
from src.services.db import Database
from src.services.wiki_projection import WikiProjection
from src.services.wiki_repository import WikiRepository


def _make_page(
    page_id="page_test1",
    title="Test Page",
    body="hello world 内容",
    claim_ids=None,
    page_type=PageType.CONCEPTS,
    status=PageStatus.PUBLISHED,
):
    return WikiPage(
        schema_version=1,
        page_id=page_id,
        title=title,
        page_type=page_type,
        status=status,
        revision=1,
        aliases=[],
        tags=["t"],
        source_ids=["s1"],
        claim_ids=claim_ids or [],
        created_at="2026-07-08T00:00:00Z",
        updated_at="2026-07-08T00:00:00Z",
        content_hash="abc12345",
        body=body,
    )


def _make_claim(claim_id="claim_test1", evidence_kid="k1"):
    return Claim(
        schema_version=1,
        claim_id=claim_id,
        statement="地球绕太阳转",
        normalized_statement="地球绕太阳转",
        claim_type="fact",
        status=ClaimStatus.ACTIVE,
        confidence=0.9,
        valid_from=None,
        valid_to=None,
        subject_refs=["地球"],
        predicate="绕",
        object_refs=["太阳"],
        evidence=[
            Evidence(
                evidence_id="ev1",
                stance=EvidenceStance.SUPPORTS,
                knowledge_id=evidence_kid,
                block_id="b1",
                location={"line": 1},
                source_revision="r1",
                excerpt_hash="h",
                observed_at="2026-07-08",
            )
        ],
        relations=[],
        created_at="2026-07-08T00:00:00Z",
        updated_at="2026-07-08T00:00:00Z",
        revision=1,
    )


@pytest.fixture
def repo_and_proj(tmp_path):
    wiki_dir = tmp_path / "wiki"
    repo = WikiRepository(
        wiki_dir=wiki_dir,
        registry_path=wiki_dir / "_meta" / "pages.json",
        redirects_path=wiki_dir / "_meta" / "redirects.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )
    db = Database._instance  # conftest setup_db autouse 已初始化
    assert db is not None, "Database 单例未初始化(conftest setup_db 应已建)"
    proj = WikiProjection(repo, db, enabled=True)
    return repo, proj, db


def _v2_count(db, table, where="", params=()):
    conn = db.get_conn()
    q = f"SELECT COUNT(*) FROM {table}"
    if where:
        q += f" WHERE {where}"
    return conn.execute(q, params).fetchone()[0]


# ---- 测试 1: page 投影 + FTS ----
def test_project_page_creates_row_and_fts(repo_and_proj):
    repo, proj, db = repo_and_proj
    page = _make_page(body="hello world 内容 test")
    repo.save_page(page)
    result = proj.process_outbox()
    assert result.processed == 1
    assert result.skipped == 0
    assert _v2_count(db, "wiki_pages_v2", "page_id=?", ("page_test1",)) == 1
    assert _v2_count(db, "wiki_pages_v2_fts") == 1
    # 验证字段
    conn = db.get_conn()
    row = conn.execute(
        "SELECT title, revision, content FROM wiki_pages_v2 WHERE page_id=?",
        ("page_test1",),
    ).fetchone()
    assert row[0] == "Test Page"
    assert row[1] == 1  # save_page: current_rev=0 (new page) → revision=1
    assert row[2] == "hello world 内容 test\n"  # write_markdown appends trailing newline


# ---- 测试 2: claim + evidence 投影 ----
def test_project_claim_creates_claim_and_evidence(repo_and_proj):
    repo, proj, db = repo_and_proj
    claim = _make_claim()
    repo.save_claim(claim)
    result = proj.process_outbox()
    assert result.processed == 1
    assert _v2_count(db, "wiki_claims") == 1
    assert _v2_count(db, "wiki_claim_evidence") == 1
    # 验证字段
    conn = db.get_conn()
    row = conn.execute(
        "SELECT stance, knowledge_id, location_json FROM wiki_claim_evidence WHERE claim_id=?",
        ("claim_test1",),
    ).fetchone()
    assert row[0] == "supports"
    assert row[1] == "k1"
    assert json.loads(row[2]) == {"line": 1}


# ---- 测试 3: 幂等重复消费 ----
def test_outbox_repeat_consumption_idempotent(repo_and_proj):
    repo, proj, db = repo_and_proj
    page = _make_page()
    repo.save_page(page)
    r1 = proj.process_outbox()
    assert r1.processed == 1
    assert _v2_count(db, "wiki_pages_v2") == 1
    # 重复消费
    r2 = proj.process_outbox()
    assert r2.processed == 1
    assert _v2_count(db, "wiki_pages_v2") == 1  # 仍 1 行


# ---- 测试 4: 缺失对象 → skipped+warning，其余正常 ----
def test_process_outbox_continues_after_missing_object(repo_and_proj):
    repo, proj, db = repo_and_proj
    # 先保存一个真实 page
    page = _make_page()
    repo.save_page(page)
    # 手写一条指向不存在 page 的事件
    outbox_path = repo._outbox_path
    with open(outbox_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "page.updated", "page_id": "nonexistent_page", "revision": 1}) + "\n")
    result = proj.process_outbox()
    assert result.processed == 1  # 真实 page 被处理
    assert result.skipped == 1  # 不存在的 page 被跳过
    assert len(result.warnings) == 1
    assert "nonexistent_page" in result.warnings[0]


# ---- 测试 5: rebuild 全量 + parity 空 ----
def test_rebuild_full_parity(repo_and_proj):
    repo, proj, db = repo_and_proj
    p1 = _make_page(page_id="p1", title="Page One")
    p2 = _make_page(page_id="p2", title="Page Two")
    c1 = _make_claim(claim_id="c1")
    repo.save_page(p1)
    repo.save_page(p2)
    repo.save_claim(c1)
    result = proj.rebuild()
    assert result.processed == 3
    assert _v2_count(db, "wiki_pages_v2") == 2
    assert _v2_count(db, "wiki_claims") == 1
    assert _v2_count(db, "wiki_claim_evidence") == 1
    # parity 完美
    findings = proj.verify_parity()
    assert findings == []


# ---- 测试 6: verify_parity 检测漂移 ----
def test_verify_parity_reports_drift(repo_and_proj):
    repo, proj, db = repo_and_proj
    page = _make_page()
    repo.save_page(page)
    proj.rebuild()
    # 人为删除投影行
    db.get_conn().execute("DELETE FROM wiki_pages_v2 WHERE page_id=?", ("page_test1",))
    db.get_conn().commit()
    findings = proj.verify_parity()
    assert len(findings) == 1
    assert findings[0].category == "projection_drift"
    assert findings[0].object_id == "page_test1"


# ---- 测试 7: delete_claim 清空投影（手写 claim.deleted 事件） ----
def test_delete_claim_clears_projection(repo_and_proj):
    repo, proj, db = repo_and_proj
    claim = _make_claim()
    repo.save_claim(claim)
    proj.process_outbox()
    assert _v2_count(db, "wiki_claims") == 1
    assert _v2_count(db, "wiki_claim_evidence") == 1
    # 手写 claim.deleted 事件 append outbox
    outbox_path = repo._outbox_path
    with open(outbox_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "claim.deleted", "claim_id": "claim_test1", "soft": True}) + "\n")
    proj.process_outbox()
    assert _v2_count(db, "wiki_claims", "claim_id=?", ("claim_test1",)) == 0
    assert _v2_count(db, "wiki_claim_evidence", "claim_id=?", ("claim_test1",)) == 0


# ---- 测试 8: page_claims 投影 ----
def test_page_claims_projected(repo_and_proj):
    repo, proj, db = repo_and_proj
    page = _make_page(claim_ids=["claim_test1"])
    claim = _make_claim()
    repo.save_page(page)
    repo.save_claim(claim)
    proj.process_outbox()
    assert _v2_count(db, "wiki_page_claims") == 1
    conn = db.get_conn()
    row = conn.execute(
        "SELECT page_id, claim_id, display_order FROM wiki_page_claims WHERE page_id=?",
        ("page_test1",),
    ).fetchone()
    assert row[0] == "page_test1"
    assert row[1] == "claim_test1"
    assert row[2] == 0


# ---- 测试 9: FTS 中文可搜索 ----
def test_fts_searchable_after_projection(repo_and_proj):
    repo, proj, db = repo_and_proj
    # unicode61 对中文将连续 CJK 视为单个 token，无法按字拆分。
    # 用英文 body 测试 FTS 基本功能（brief 允许的 fallback）。
    # 中文内容仍写入验证投影正常，FTS MATCH 用英文词。
    page = _make_page(body="quantum entanglement 现象 hello")
    repo.save_page(page)
    proj.process_outbox()
    conn = db.get_conn()
    row = conn.execute(
        "SELECT page_id FROM wiki_pages_v2_fts WHERE wiki_pages_v2_fts MATCH ?",
        ("quantum",),
    ).fetchone()
    assert row is not None
    assert row[0] == "page_test1"


# ---- 测试 10: disabled 跳过处理 ----
def test_disabled_skips_processing(repo_and_proj):
    repo, _, db = repo_and_proj
    disabled_proj = WikiProjection(repo, db, enabled=False)
    page = _make_page()
    repo.save_page(page)
    result = disabled_proj.process_outbox()
    assert result.skipped >= 1
    assert result.processed == 0
    assert _v2_count(db, "wiki_pages_v2") == 0
