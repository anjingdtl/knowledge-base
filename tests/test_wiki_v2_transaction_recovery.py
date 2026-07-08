"""Canonical Wiki V2 多对象事务原子性与崩溃恢复测试(Phase 3.5 / C3)。

验证 WikiRepository 严格 staging transaction(spec §14.1):
- commit 全成功 → 全发布(COMMITTED + outbox)
- validation/revision/supersede 校验失败 → 不发布(无半写)
- publish/registry/COMMITTED/outbox 各阶段中断 → recover 恢复一致
- outbox 重放幂等(tx_id 去重 + projection INSERT OR REPLACE)

故障注入两种方式:
A. monkeypatch 注入(publish/registry/outbox 阶段抛)——模拟 crash 过程
B. 构造残留 _staging/<tx_id>/——模拟 crash 后状态,直接测 recover
"""
from __future__ import annotations

import json
import os

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
from src.services.wiki_repository import (
    StaleRevisionError,
    TransactionValidationError,
    WikiRepository,
)

NOW = "2026-07-08T12:00:00+08:00"


@pytest.fixture
def repo(tmp_path):
    return WikiRepository(
        wiki_dir=tmp_path / "wiki",
        registry_path=tmp_path / "wiki" / "_meta" / "pages.json",
        redirects_path=tmp_path / "wiki" / "_meta" / "redirects.json",
        outbox_path=tmp_path / "wiki_projection_outbox.jsonl",
    )


def _page(page_id="page_1", title="FTTR", claim_ids=None) -> WikiPage:
    return WikiPage(
        schema_version=2, page_id=page_id, title=title, page_type=PageType.CONCEPTS,
        status=PageStatus.DRAFT, revision=1, aliases=[], tags=[], source_ids=["k1"],
        claim_ids=claim_ids or [], created_at=NOW, updated_at=NOW,
        content_hash="sha256:x", body="# FTTR\n", supersedes_page_id=None,
    )


def _claim(claim_id="claim_1", status=ClaimStatus.ACTIVE,
           stance=EvidenceStance.SUPPORTS) -> Claim:
    return Claim(
        schema_version=1, claim_id=claim_id, statement="s", normalized_statement="s",
        claim_type="fact", status=status, confidence=0.9, valid_from=None, valid_to=None,
        subject_refs=["entity:X"], predicate="p", object_refs=["o"],
        evidence=[Evidence(evidence_id="ev1", stance=stance, knowledge_id="k1", block_id="b1")],
        relations=[], created_at=NOW, updated_at=NOW, revision=1,
    )


def _write_staging_tx(repo, tx_id, *, committed=False, pages=None, claims=None,
                      write_canonical=True, reg_write=False):
    """构造残留 _staging/<tx_id>/(manifest[+COMMITTED]),可选写 canonical/registry。"""
    tx_dir = repo._staging_dir / tx_id
    tx_dir.mkdir(parents=True, exist_ok=True)
    pages = pages or []
    claims = claims or []
    manifest = {"tx_id": tx_id, "pages": pages, "claims": claims}
    (tx_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    if committed:
        (tx_dir / "COMMITTED").write_text("ok", encoding="utf-8")
    if write_canonical:
        for p in pages:
            (repo._wiki_dir / p["path"]).parent.mkdir(parents=True, exist_ok=True)
            (repo._wiki_dir / p["path"]).write_text("stub", encoding="utf-8")
    if reg_write:
        reg = repo.get_registry()
        for p in pages:
            reg[p["page_id"]] = {"path": p["path"], "title": p.get("title", "T"),
                                 "page_type": "concepts", "revision": p["revision"],
                                 "content_hash": "sha256:x"}
        repo._write_registry(reg)
    return manifest


# ===========================================================================
# A. validation / 乐观锁 / supersede 校验失败 → 不发布(无半写)
# ===========================================================================
def test_supersede_partial_validation_no_half_write(repo):
    """spec #12: supersede old+new,new validate 失败 → 抛 TransactionValidationError,old 不变。"""
    old = _claim("c1", status=ClaimStatus.ACTIVE, stance=EvidenceStance.SUPPORTS)
    repo.save_claim(old)
    # new active 但 evidence 是 contradicts(无 supports)→ validate 失败
    new_bad = _claim("c2", status=ClaimStatus.ACTIVE, stance=EvidenceStance.CONTRADICTS)
    old_superseded = _claim("c1", status=ClaimStatus.SUPERSEDED, stance=EvidenceStance.SUPPORTS)
    with pytest.raises(TransactionValidationError):
        with repo.transaction() as tx:
            tx.stage_claim(old_superseded, expected_revision=1)
            tx.stage_claim(new_bad)
    # commit 在 validate 阶段抛,publish 未执行 → old 保持 active,new 未创建
    assert repo.get_claim("c1").status is ClaimStatus.ACTIVE
    assert repo.get_claim("c2") is None


def test_revision_conflict_aborts_transaction(repo):
    """spec #10: transaction 内 expected_revision 冲突 → StaleRevisionError,不发布。"""
    repo.save_page(_page())  # revision 1
    p = _page()
    with pytest.raises(StaleRevisionError):
        with repo.transaction() as tx:
            tx.stage_page(p, expected_revision=99)  # 实际是 1
    # 未发布,registry 仍是 revision 1
    assert repo.get_registry()["page_1"]["revision"] == 1


def test_concurrent_claim_update_second_loses(repo):
    """spec #11: 同一 claim 并发更新,后提交的 expected_revision 失配 → 冲突。"""
    repo.save_claim(_claim("c1"))  # revision 1
    c1a = _claim("c1")
    c1b = _claim("c1")
    with repo.transaction() as tx:  # 第一提交成功 → revision 2
        tx.stage_claim(c1a, expected_revision=1)
    with pytest.raises(StaleRevisionError):  # 第二仍 expected 1 → 失配(实际 2)
        with repo.transaction() as tx:
            tx.stage_claim(c1b, expected_revision=1)


# ===========================================================================
# B. publish / registry 中断 → recover(孤儿不污染查询)
# ===========================================================================
def test_publish_interrupt_orphan_not_visible(repo, monkeypatch):
    """spec #1/#2: publish 中途 os.replace 抛 → recover 清理,registry 不含(孤儿不污染)。"""
    p1 = _page(page_id="page_a", title="A")
    p2 = _page(page_id="page_b", title="B")
    original = os.replace
    calls = [0]

    def flaky(src, dst):
        calls[0] += 1
        if calls[0] == 2:
            raise OSError("simulated publish crash")
        return original(src, dst)

    monkeypatch.setattr("src.services.wiki_repository.os.replace", flaky)
    with pytest.raises(OSError):
        with repo.transaction() as tx:
            tx.stage_page(p1)
            tx.stage_page(p2)
    monkeypatch.undo()
    repo.recover()
    # registry 不含未提交对象(查询源是 registry → 不污染)
    reg = repo.get_registry()
    assert "page_a" not in reg
    assert "page_b" not in reg
    assert repo.list_pages() == []
    # 重新提交可成功
    repo.save_page(p1)
    assert repo.get_page("page_a") is not None


def test_registry_written_no_committed_recovers_forward(repo):
    """spec #3/#4: registry 已写但 COMMITTED 未写 → recover 前向(reg_written)补 outbox。"""
    _write_staging_tx(
        repo, "tx_x", committed=False, reg_write=True,
        pages=[{"page_id": "page_x", "revision": 1, "event": "page.created", "path": "concepts/x.md"}],
    )
    recovered = repo.recover()
    assert "tx_x" in recovered
    events = repo.read_outbox()
    assert any(e.get("tx_id") == "tx_x" and e["type"] == "page.created" for e in events)


# ===========================================================================
# C. COMMITTED 有 / outbox 缺 → recover 幂等补写
# ===========================================================================
def test_committed_but_outbox_missing_recovers(repo):
    """spec #5: COMMITTED 有但 outbox 缺 → recover 按 manifest 补 outbox。"""
    _write_staging_tx(
        repo, "tx_y", committed=True, reg_write=True,
        pages=[{"page_id": "page_y", "revision": 1, "event": "page.created", "path": "concepts/y.md"}],
        claims=[{"claim_id": "claim_y", "revision": 1, "event": "claim.created"}],
    )
    recovered = repo.recover()
    assert "tx_y" in recovered
    events = repo.read_outbox()
    tx_events = [e for e in events if e.get("tx_id") == "tx_y"]
    assert len(tx_events) == 2  # page + claim


def test_recover_idempotent_no_duplicate_outbox(repo):
    """spec #6/#8: 重复 recover 不重复补 outbox(tx_id 去重)。"""
    _write_staging_tx(
        repo, "tx_z", committed=True, reg_write=True,
        pages=[{"page_id": "page_z", "revision": 1, "event": "page.created", "path": "concepts/z.md"}],
    )
    repo.recover()
    repo.recover()  # 重复
    tx_events = [e for e in repo.read_outbox() if e.get("tx_id") == "tx_z"]
    assert len(tx_events) == 1  # 不重复


def test_stage_crash_no_manifest_cleaned_up(repo):
    """staging 残留无 manifest(stage 中断)→ recover 安全清理。"""
    tx_dir = repo._staging_dir / "tx_orphan"
    tx_dir.mkdir()
    (tx_dir / "partial.tmp").write_text("x", encoding="utf-8")  # 无 manifest
    repo.recover()
    assert not tx_dir.exists()


# ===========================================================================
# D. Windows 文件占用 / projection 重放幂等
# ===========================================================================
def test_windows_file_occupied_aborts_no_half_write(repo, monkeypatch):
    """spec #9: os.replace PermissionError(文件占用)→ commit 抛,registry 未写(无半写)。"""
    p1 = _page(page_id="page_w", title="W")

    def denied(src, dst):
        raise PermissionError("file locked")

    monkeypatch.setattr("src.services.wiki_repository.os.replace", denied)
    with pytest.raises(PermissionError):
        with repo.transaction() as tx:
            tx.stage_page(p1)
    monkeypatch.undo()
    repo.recover()
    assert "page_w" not in repo.get_registry()


def test_projection_replay_idempotent(repo):
    """spec #7/#8: outbox 重复 event → projection INSERT OR REPLACE 不产生重复行。"""
    # 构造一个 page + 重复 outbox events
    repo.save_page(_page(page_id="page_p", title="P"))
    events = repo.read_outbox()
    assert events
    # 人为重复 append 同一 event
    for ev in list(events):
        repo._append_outbox(ev)
    # projection 消费:幂等(INSERT OR REPLACE by page_id)
    from src.services.wiki_projection import WikiProjection

    class _FakeDB:
        def __init__(self):
            import sqlite3
            self._conn = sqlite3.connect(":memory:")
            for ddl in [
                "CREATE TABLE wiki_pages_v2 (page_id TEXT PRIMARY KEY, path TEXT, title TEXT, page_type TEXT, status TEXT, revision INTEGER, content TEXT, content_hash TEXT, aliases_json TEXT, tags_json TEXT, source_ids_json TEXT, claim_ids_json TEXT, created_at TEXT, updated_at TEXT)",
                "CREATE TABLE wiki_pages_v2_fts (page_id TEXT, title TEXT, content TEXT)",
                "CREATE TABLE wiki_page_claims (page_id TEXT, claim_id TEXT, display_order INTEGER)",
                "CREATE TABLE wiki_claim_evidence (evidence_id TEXT PRIMARY KEY, claim_id TEXT, stance TEXT, knowledge_id TEXT, block_id TEXT, location_json TEXT, source_revision TEXT, excerpt_hash TEXT, observed_at TEXT)",
                "CREATE TABLE wiki_claims (claim_id TEXT PRIMARY KEY, statement TEXT, normalized_statement TEXT, claim_type TEXT, status TEXT, confidence REAL, claim_scope TEXT, valid_from TEXT, valid_to TEXT, revision INTEGER, created_at TEXT, updated_at TEXT)",
                "CREATE TABLE wiki_dependencies (from_type TEXT, from_id TEXT, to_type TEXT, to_id TEXT, relation TEXT)",
            ]:
                self._conn.execute(ddl)
            self._conn.commit()

        def get_conn(self):
            return self._conn

    proj = WikiProjection(repository=repo, database=_FakeDB(), enabled=True)
    result = proj.process_outbox()  # 重复 event 幂等消费
    conn = proj._db.get_conn()
    rows = conn.execute("SELECT COUNT(*) FROM wiki_pages_v2 WHERE page_id = ?", ("page_p",)).fetchone()
    assert rows[0] == 1  # 不重复
    assert result.errors == []


# ===========================================================================
# E. 全成功路径 + recover 启动清理
# ===========================================================================
def test_transaction_commit_all_published(repo):
    """全成功:多 page + 多 claim 全发布,COMMITTED 写,outbox 完整。"""
    p1 = _page(page_id="page_a", title="A")
    c1 = _claim("claim_a")
    with repo.transaction() as tx:
        tx.stage_page(p1)
        tx.stage_claim(c1)
    assert repo.get_page("page_a") is not None
    assert repo.get_claim("claim_a") is not None
    events = repo.read_outbox()
    assert any(e.get("type") == "page.created" for e in events)
    assert any(e.get("type") == "claim.created" for e in events)
    # staging 清理
    assert not any(tx_dir.name.startswith("tx_") for tx_dir in repo._staging_dir.iterdir() if tx_dir.is_dir())


def test_recover_returns_completed_tx_ids(repo):
    """recover 返回前向完成的 tx_id 列表(可观测)。"""
    _write_staging_tx(
        repo, "tx_obs", committed=True, reg_write=True,
        pages=[{"page_id": "page_obs", "revision": 1, "event": "page.created", "path": "concepts/obs.md"}],
    )
    recovered = repo.recover()
    assert recovered == ["tx_obs"]
    # 二次 recover 无新完成(staging 已清)
    assert repo.recover() == []
