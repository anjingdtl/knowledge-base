"""Phase 5 E2E:E2E-3(来源更新) + E2E-4(来源删除仍有他源)真实 WikiRepository 集成。"""
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
from src.services.wiki_repository import WikiRepository


def _build_real_repo(tmp_path):
    return WikiRepository(
        wiki_dir=tmp_path / "wiki",
        registry_path=tmp_path / "wiki/_meta/pages.json",
        redirects_path=tmp_path / "wiki/_meta/redirects.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )


def _seed_claim_with_page(repo, claim_id, evidence, page_id):
    """用真实事务 seed 一条 active claim + 一张 published page 引用它。"""
    claim = Claim(
        schema_version=1, claim_id=claim_id, statement=claim_id,
        normalized_statement=claim_id, claim_type="fact",
        status=ClaimStatus.ACTIVE, confidence=0.9, valid_from=None, valid_to=None,
        subject_refs=["s"], predicate="p", object_refs=["o"], evidence=evidence,
        relations=[], created_at="t", updated_at="t", revision=1,
    )
    page = WikiPage(
        schema_version=1, page_id=page_id, title=page_id, page_type=PageType.CONCEPTS,
        status=PageStatus.PUBLISHED, revision=1, aliases=[], tags=[], source_ids=[],
        claim_ids=[claim_id], created_at="t", updated_at="t", content_hash="ch", body="",
    )
    with repo.transaction() as tx:
        tx.stage_claim(claim)
        tx.stage_page(page)
        tx.commit()


class _FakeBlocks:
    def __init__(self, mapping):  # {block_id: content}
        self._m = mapping

    def list_by_page(self, page_id, limit=10000):
        from src.models.block import Block
        return [Block(id=bid, page_id=page_id, content=c) for bid, c in self._m.items()]


class _NoopProjection:
    enabled = True

    def process_outbox(self, *, force=False):
        return type("R", (), {"processed": 0, "skipped": 0, "warnings": [], "errors": []})()

    def verify_parity(self):
        return []


def _svc(repo, blocks):
    dep = WikiDependencyService(repository=repo)
    return WikiRebuildService(
        repository=repo, projection=_NoopProjection(), block_repository=blocks,
        dependency_service=dep,
        config={"wiki.rebuild.max_pages_per_job": 100, "wiki.rebuild.max_depth": 5},
        clock=lambda: "NOW",
    )


def test_e2e3_source_update_orphan_block_makes_claim_unsupported(tmp_path):
    """E2E-3:A v1 支持 c1 → A v2 删段(block 消失)→ evidence stale → c1 unsupported → page review。"""
    repo = _build_real_repo(tmp_path)
    old_hash = compute_excerpt_hash("original content")
    ev = Evidence(evidence_id="evA", stance=EvidenceStance.SUPPORTS, knowledge_id="kA",
                  block_id="b1", source_revision="v1", excerpt_hash=old_hash)
    _seed_claim_with_page(repo, "c1", [ev], "p1")
    svc = _svc(repo, _FakeBlocks({}))  # b1 消失
    result = svc.rebuild("kA", event="update")
    assert result.committed is True
    after = repo.get_claim("c1")
    assert after.status is ClaimStatus.UNSUPPORTED
    assert after.evidence[0].stale is True
    assert after.evidence[0].stale_at == "NOW"
    assert repo.get_page("p1").status is PageStatus.REVIEW
    assert after is not None  # d03:claim 不物理删除


def test_e2e4_source_delete_with_other_supports_stays_active(tmp_path):
    """E2E-4:A、B 均支持 c1 → 删 A → c1 仍 active(剩 B 的 evidence)。"""
    repo = _build_real_repo(tmp_path)
    ev_a = Evidence(evidence_id="evA", stance=EvidenceStance.SUPPORTS, knowledge_id="kA",
                    block_id="bA", source_revision="v1", excerpt_hash="sha256:hA")
    ev_b = Evidence(evidence_id="evB", stance=EvidenceStance.SUPPORTS, knowledge_id="kB",
                    block_id="bB", source_revision="v1", excerpt_hash="sha256:hB")
    _seed_claim_with_page(repo, "c1", [ev_a, ev_b], "p1")
    svc = _svc(repo, _FakeBlocks({}))
    result = svc.rebuild("kA", event="delete")
    assert result.committed is True
    after = repo.get_claim("c1")
    assert after.status is ClaimStatus.ACTIVE  # 仍有 evB
    evA = next(e for e in after.evidence if e.evidence_id == "evA")
    evB = next(e for e in after.evidence if e.evidence_id == "evB")
    assert evA.stale is True
    assert evB.stale is False


def test_e2e3_unchanged_block_keeps_claim_active(tmp_path):
    """u01/u03:block 未变 → claim 保持 active,page 不进 review(不重编译)。"""
    repo = _build_real_repo(tmp_path)
    same_hash = compute_excerpt_hash("stable content")
    ev = Evidence(evidence_id="evA", stance=EvidenceStance.SUPPORTS, knowledge_id="kA",
                  block_id="b1", source_revision="v1", excerpt_hash=same_hash)
    _seed_claim_with_page(repo, "c1", [ev], "p1")
    svc = _svc(repo, _FakeBlocks({"b1": "stable content"}))  # 内容不变
    result = svc.rebuild("kA", event="update")
    assert result.committed is True
    after = repo.get_claim("c1")
    assert after.status is ClaimStatus.ACTIVE  # 未失效
    assert after.evidence[0].stale is False
    assert repo.get_page("p1").status is PageStatus.PUBLISHED  # 不进 review
