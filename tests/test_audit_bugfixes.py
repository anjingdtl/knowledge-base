"""Audit-found bug regression tests (post Wiki V2 code audit)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

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
from src.services.wiki_claim_matcher import ClaimMatchDecision
from src.services.wiki_merge_engine import WikiMergeEngine
from src.services.wiki_rebuild_scheduler import RebuildScheduler
from src.services.wiki_repository import RegistryCorruptError, WikiRepository

NOW = "2026-07-13T12:00:00+08:00"


class _FakeRebuild:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def rebuild(self, knowledge_id, *, event, **kw):
        self.calls.append((knowledge_id, event))
        return type("R", (), {"committed": True, "cancelled": False, "warnings": []})()


def test_rebuild_scheduler_auto_flushes_after_debounce():
    svc = _FakeRebuild()
    sch = RebuildScheduler(rebuild_service=svc, debounce_ms=50)
    sch.schedule("k1", "update")
    assert sch.pending_count == 1
    deadline = time.time() + 2.0
    while sch.pending_count > 0 and time.time() < deadline:
        time.sleep(0.02)
    assert sch.pending_count == 0
    assert svc.calls == [("k1", "update")]


def test_rebuild_scheduler_debounce_ms_zero_auto_flushes_soon():
    svc = _FakeRebuild()
    sch = RebuildScheduler(rebuild_service=svc, debounce_ms=0)
    sch.schedule("k1", "delete")
    deadline = time.time() + 2.0
    while not svc.calls and time.time() < deadline:
        time.sleep(0.01)
    assert svc.calls == [("k1", "delete")]
    assert sch.pending_count == 0


def _repo(tmp: Path) -> WikiRepository:
    wiki = tmp / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    return WikiRepository(
        wiki_dir=wiki,
        registry_path=wiki / "_meta" / "pages.json",
        redirects_path=wiki / "_meta" / "redirects.json",
        outbox_path=tmp / "outbox.jsonl",
    )


def _claim(cid="c1", status=ClaimStatus.ACTIVE) -> Claim:
    return Claim(
        schema_version=1, claim_id=cid, statement="s", normalized_statement="s",
        claim_type="fact", status=status, confidence=0.9, valid_from=None, valid_to=None,
        subject_refs=["e"], predicate="p", object_refs=["o"],
        evidence=[Evidence(evidence_id="e1", stance=EvidenceStance.SUPPORTS,
                           knowledge_id="k1", block_id="b1")],
        relations=[], created_at=NOW, updated_at=NOW, revision=1,
    )


def _page(pid="p1", title="T", claim_ids=None) -> WikiPage:
    return WikiPage(
        schema_version=2, page_id=pid, title=title, page_type=PageType.CONCEPTS,
        status=PageStatus.DRAFT, revision=1, aliases=[], tags=[], source_ids=["k1"],
        claim_ids=claim_ids or [], created_at=NOW, updated_at=NOW,
        content_hash="sha256:x", body="# body\n", supersedes_page_id=None,
    )


def test_recover_appends_missing_claim_events_when_partial_outbox(tmp_path):
    repo = _repo(tmp_path)
    with repo.transaction() as tx:
        tx.stage_page(_page("p1"), expected_revision=None)
        tx.stage_claim(_claim("c1"), expected_revision=None)

    events = repo.read_outbox()
    page_ev = [e for e in events if e.get("type", "").startswith("page.")][0]
    claim_ev_meta = [e for e in events if e.get("type", "").startswith("claim.")][0]
    tx_id = page_ev["tx_id"]
    repo._outbox_path.write_text(
        json.dumps(page_ev, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tx_dir = repo._staging_dir / tx_id
    tx_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "tx_id": tx_id,
        "pages": [{
            "page_id": "p1",
            "revision": page_ev["revision"],
            "event": page_ev["type"],
            "path": page_ev["path"],
        }],
        "claims": [{
            "claim_id": "c1",
            "revision": claim_ev_meta["revision"],
            "event": claim_ev_meta["type"],
        }],
    }
    (tx_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tx_dir / "COMMITTED").write_text("ok", encoding="utf-8")

    recovered = repo.recover()
    assert tx_id in recovered
    out = repo.read_outbox()
    claim_ids = [e.get("claim_id") for e in out if e.get("claim_id")]
    assert "c1" in claim_ids


def test_recover_claim_only_without_committed_if_claim_on_disk(tmp_path):
    repo = _repo(tmp_path)
    claim = _claim("c_only")
    repo._write_claim_file(repo._claim_path("c_only"), claim)
    tx_id = "tx-claim-only"
    tx_dir = repo._staging_dir / tx_id
    tx_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "tx_id": tx_id,
        "pages": [],
        "claims": [{"claim_id": "c_only", "revision": 1, "event": "claim.created"}],
    }
    (tx_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    recovered = repo.recover()
    assert tx_id in recovered
    out = repo.read_outbox()
    assert any(e.get("claim_id") == "c_only" for e in out)


def test_reject_emits_claim_deleted_outbox_event(tmp_path):
    from src.services.wiki_feedback_service import WikiFeedbackService

    repo = _repo(tmp_path)
    with repo.transaction() as tx:
        tx.stage_claim(_claim("c_rej", ClaimStatus.ACTIVE), expected_revision=None)
    if repo._outbox_path.exists():
        repo._outbox_path.write_text("", encoding="utf-8")

    fb = WikiFeedbackService(repository=repo)
    r = fb.apply("c_rej", "reject")
    assert r.after_status == "retracted"
    events = repo.read_outbox()
    assert any(e.get("type") == "claim.deleted" and e.get("claim_id") == "c_rej" for e in events)


def test_registry_corrupt_raises(tmp_path):
    repo = _repo(tmp_path)
    repo._registry_path.parent.mkdir(parents=True, exist_ok=True)
    repo._registry_path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(RegistryCorruptError):
        repo.get_registry()


def test_refines_validates_both_before_any_stage(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    existing = _claim("c1")
    repo.save_claim(existing, expected_revision=None)

    engine = WikiMergeEngine(repository=repo)
    new_claim = _claim("n_bad")
    original_validate = Claim.validate

    def flaky_validate(self):
        if self.claim_id == "n_bad":
            return ["forced invalid"]
        return original_validate(self)

    monkeypatch.setattr(Claim, "validate", flaky_validate)
    decision = ClaimMatchDecision(
        action="refines", target_claim_id="c1", score=0.9, reason_codes=["test"],
    )
    result = engine.apply([(new_claim, decision)], page=None, now=NOW)
    assert "n_bad" in result.skipped or result.errors
    target = repo.get_claim("c1")
    assert target is not None
    assert not any(
        r.relation == "refined_by" and r.target_claim_id == "n_bad" for r in target.relations
    )
    assert repo.get_claim("n_bad") is None


def test_soft_delete_keeps_block_vectors():
    from src.services.block_store import BlockStore
    from src.services.db import Database
    from src.services.file_graph import FileGraphService
    from tests.conftest import insert_test_block, insert_test_knowledge

    kid = insert_test_knowledge(title="soft-vec", content="keep vectors on soft delete")
    bid = insert_test_block(kid, content="keep vectors on soft delete", block_type="text")
    store = BlockStore()
    store.add_block_embedding(bid, [0.2] * 1024)

    cfg = MagicMock()
    cfg.get = lambda key, default=None: default
    cfg.get_data_dir = lambda: Path(".")
    svc = FileGraphService(config=cfg, db=Database, block_store=store, embedding=None)
    svc._delete_cache(kid, hard=False)

    row = Database.get_knowledge(kid, include_deleted=True)
    assert row is not None
    assert row.get("deleted_at")
    assert Database.get_knowledge(kid) is None


def test_sync_page_restores_soft_deleted_row(tmp_path):
    from src.services.block_store import BlockStore
    from src.services.db import Database
    from src.services.file_graph import FileGraphService
    from tests.conftest import insert_test_knowledge

    kid = insert_test_knowledge(title="restore-me", content="body text for restore")
    Database.soft_delete_knowledge(kid)
    assert Database.get_knowledge(kid) is None

    graph = tmp_path / "graph"
    pages = graph / "pages"
    pages.mkdir(parents=True)
    md = pages / f"restore-me--{kid[:8]}.md"
    md.write_text(
        f"id:: {kid}\ntitle:: restore-me\n\n- body text for restore\n",
        encoding="utf-8",
    )

    class Cfg:
        def get(self, key, default=None):
            if key == "storage.graph_dir":
                return str(graph)
            return default

        def get_data_dir(self):
            return tmp_path

    svc = FileGraphService(config=Cfg(), db=Database, block_store=BlockStore(), embedding=None)
    svc.sync_page(str(md))
    row = Database.get_knowledge(kid)
    assert row is not None
    assert not row.get("deleted_at")


def test_migrator_register_existing_when_registry_empty(tmp_path):
    from src.services.wiki_v2_migrator import WikiV2Migrator

    wiki = tmp_path / "wiki"
    concepts = wiki / "concepts"
    concepts.mkdir(parents=True)
    page_id = "page-existing-1"
    (concepts / "FTTR.md").write_text(
        f"---\npage_id: {page_id}\ntitle: FTTR\npage_type: concepts\nsource_ids: []\n---\n\n# FTTR\nbody\n",
        encoding="utf-8",
    )
    repo = WikiRepository(
        wiki_dir=wiki,
        registry_path=wiki / "_meta" / "pages.json",
        redirects_path=wiki / "_meta" / "redirects.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )
    migrator = WikiV2Migrator(
        wiki_dir=wiki,
        repository=repo,
        database=None,
        projection=None,
        backups_dir=tmp_path / "backups",
    )
    report = migrator.dry_run()
    actions = [p.action for p in report.page_plans]
    assert "register_existing" in actions, f"expected register_existing, got {actions}"
