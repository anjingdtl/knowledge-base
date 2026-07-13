"""WikiRebuildService 影响规划与 staged rebuild 测试(Phase 5)。

T5.2a:plan_rebuild dry-run(u01-u03 / d01-d03 规划)。
T5.2b:rebuild staging 事务 + projection + cancel(后追加)。
"""
from src.models.wiki_v2 import (
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    PageStatus,
    PageType,
    WikiPage,
)
from src.services.wiki_claim_extractor import compute_excerpt_hash
from src.services.wiki_dependency_service import WikiDependencyService
from src.services.wiki_rebuild_service import WikiRebuildService


def _ev(eid, kid, block_id="b1", excerpt="h1", stance=EvidenceStance.SUPPORTS):
    return Evidence(evidence_id=eid, stance=stance, knowledge_id=kid, block_id=block_id,
                    source_revision="v1", excerpt_hash=excerpt)


def _claim(cid, evidence, status=ClaimStatus.ACTIVE):
    return Claim(schema_version=1, claim_id=cid, statement=cid, normalized_statement=cid,
                 claim_type="fact", status=status, confidence=0.9, valid_from=None, valid_to=None,
                 subject_refs=["s"], predicate="p", object_refs=["o"], evidence=evidence,
                 relations=[], created_at="t", updated_at="t", revision=1)


def _page(pid, claim_ids, status=PageStatus.PUBLISHED):
    return WikiPage(schema_version=1, page_id=pid, title=pid, page_type=PageType.CONCEPTS,
                    status=status, revision=1, aliases=[], tags=[], source_ids=[],
                    claim_ids=claim_ids, created_at="t", updated_at="t", content_hash="ch", body="")


class _FakeBlocks:
    """模拟 BlockRepository.list_by_page:{block_id: content}。"""

    def __init__(self, current_blocks: dict):
        self._current = current_blocks

    def list_by_page(self, page_id, limit=10000):
        from src.models.block import Block
        return [Block(id=bid, page_id=page_id, content=content)
                for bid, content in self._current.items()]


class _FakeRepo:
    def __init__(self, claims=None, pages=None):
        self._claims = claims or []
        self._pages = pages or []

    def list_claims(self):
        return list(self._claims)

    def list_pages(self):
        return list(self._pages)

    def get_claim(self, cid):
        return next((c for c in self._claims if c.claim_id == cid), None)


class _NoopProjection:
    enabled = True

    def process_outbox(self, *, force=False):
        return type("R", (), {"processed": 0, "skipped": 0, "warnings": [], "errors": []})()

    def verify_parity(self):
        return []


def _svc(repo, blocks, **kw):
    dep = WikiDependencyService(repository=repo)
    return WikiRebuildService(
        repository=repo, projection=_NoopProjection(),
        block_repository=blocks, dependency_service=dep,
        config={"wiki.rebuild.max_pages_per_job": 100, "wiki.rebuild.max_depth": 5},
        clock=lambda: "NOW", **kw,
    )


# ---- u02:来源更新且 block 变 → 变化 evidence 标 stale ----
def test_plan_update_changed_block_marks_stale():
    c1 = _claim("c1", [_ev("e1", "k1", block_id="b1", excerpt="sha256:OLD")])
    repo = _FakeRepo([c1], [_page("p1", ["c1"])])
    blocks = _FakeBlocks({"b1": "NEW CONTENT"})  # b1 内容变 → hash 不同
    svc = _svc(repo, blocks)
    plan = svc.plan_rebuild("k1", event="update")
    ev_impact = next(e for e in plan.affected_evidence if e.evidence_id == "e1")
    assert ev_impact.reason == "block_changed"
    c1_impact = next(c for c in plan.affected_claims if c.claim_id == "c1")
    assert c1_impact.proposed_status == "unsupported"


# ---- u01/u03:来源更新但 block 未变 → 不失效,不重编译 ----
def test_plan_update_unchanged_block_no_impact():
    h = compute_excerpt_hash("SAME")
    c1 = _claim("c1", [_ev("e1", "k1", block_id="b1", excerpt=h)])
    repo = _FakeRepo([c1], [_page("p1", ["c1"])])
    blocks = _FakeBlocks({"b1": "SAME"})  # 内容不变 → hash 同
    svc = _svc(repo, blocks)
    plan = svc.plan_rebuild("k1", event="update")
    assert plan.affected_evidence == []
    assert plan.affected_claims == []


# ---- block 被删 → block_deleted ----
def test_plan_update_block_deleted():
    c1 = _claim("c1", [_ev("e1", "k1", block_id="bGone", excerpt="sha256:h")])
    repo = _FakeRepo([c1], [_page("p1", ["c1"])])
    blocks = _FakeBlocks({})  # bGone 不在当前 blocks
    svc = _svc(repo, blocks)
    plan = svc.plan_rebuild("k1", event="update")
    assert plan.affected_evidence[0].reason == "block_deleted"


# ---- d02:删来源且无他源 → unsupported ----
def test_plan_delete_no_other_supports_unsupported():
    c1 = _claim("c1", [_ev("e1", "k1")])
    repo = _FakeRepo([c1], [_page("p1", ["c1"])])
    svc = _svc(repo, _FakeBlocks({}))
    plan = svc.plan_rebuild("k1", event="delete")
    assert plan.affected_evidence[0].reason == "source_deleted"
    assert next(c for c in plan.affected_claims if c.claim_id == "c1").proposed_status == "unsupported"


# ---- d01:删来源但有他源 → active ----
def test_plan_delete_with_other_supports_active():
    c1 = _claim("c1", [_ev("e1", "k1"), _ev("e2", "k2")])
    repo = _FakeRepo([c1], [_page("p1", ["c1"])])
    svc = _svc(repo, _FakeBlocks({}))
    plan = svc.plan_rebuild("k1", event="delete")
    assert next(c for c in plan.affected_claims if c.claim_id == "c1").proposed_status == "active"
