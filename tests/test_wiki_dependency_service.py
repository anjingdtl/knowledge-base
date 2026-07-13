"""WikiDependencyService 依赖图与影响规划测试(Phase 5)。"""
from src.models.wiki_v2 import (
    Claim,
    ClaimRelation,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    PageStatus,
    PageType,
    WikiPage,
)
from src.services.wiki_dependency_service import WikiDependencyService


def _claim(cid, evidence, status=ClaimStatus.ACTIVE, relations=None):
    return Claim(
        schema_version=1, claim_id=cid, statement=f"stmt {cid}",
        normalized_statement=f"stmt {cid}", claim_type="fact", status=status,
        confidence=0.9, valid_from=None, valid_to=None, subject_refs=["s"],
        predicate="p", object_refs=["o"], evidence=evidence,
        relations=relations or [], created_at="t", updated_at="t", revision=1,
    )


def _ev(eid, kid, stance=EvidenceStance.SUPPORTS, stale=False):
    return Evidence(evidence_id=eid, stance=stance, knowledge_id=kid, block_id="b1",
                    source_revision="v1", excerpt_hash="h1", stale=stale)


def _page(pid, claim_ids, status=PageStatus.PUBLISHED):
    return WikiPage(
        schema_version=1, page_id=pid, title=f"Title {pid}", page_type=PageType.CONCEPTS,
        status=status, revision=1, aliases=[], tags=[], source_ids=[], claim_ids=claim_ids,
        created_at="t", updated_at="t", content_hash="ch", body="",
    )


class _FakeRepo:
    def __init__(self, claims, pages):
        self._claims = claims
        self._pages = pages

    def list_claims(self):
        return list(self._claims)

    def list_pages(self):
        return list(self._pages)


def test_get_impacted_by_source_multi_support_keeps_active():
    """E2E-4:A、B 均支持 c1 → 删 A(k1) 的影响集:c1 仍 active(剩 B 的 evidence)。"""
    c1 = _claim("c1", [_ev("eA", "k1"), _ev("eB", "k2")])  # A=k1, B=k2
    page1 = _page("p1", ["c1"])
    svc = WikiDependencyService(repository=_FakeRepo([c1], [page1]))
    plan = svc.get_impacted_by_source("k1")
    # eA 来自 k1 → 受影响 evidence;eB 来自 k2 → 不受影响
    assert {e.evidence_id for e in plan.affected_evidence} == {"eA"}
    # c1 仍有 eB(supports,非 stale)→ proposed_status 保持 active
    c1_impact = next(c for c in plan.affected_claims if c.claim_id == "c1")
    assert c1_impact.proposed_status == "active"


def test_get_impacted_by_source_single_support_becomes_unsupported():
    """E2E-3 片段:仅 A(k1) 支持 c1 → 删 A → c1 proposed unsupported。"""
    c1 = _claim("c1", [_ev("eA", "k1")])
    page1 = _page("p1", ["c1"])
    svc = WikiDependencyService(repository=_FakeRepo([c1], [page1]))
    plan = svc.get_impacted_by_source("k1")
    c1_impact = next(c for c in plan.affected_claims if c.claim_id == "c1")
    assert c1_impact.proposed_status == "unsupported"
    # 受影响 published page → proposed review
    p1_impact = next(p for p in plan.affected_pages if p.page_id == "p1")
    assert p1_impact.proposed_status == "review"


def test_topological_order_stable_and_cycle_detection():
    """claim↔claim 关系:c2 refines c1;环:c1 refines c2 + c2 refines c1 → cycle_warning。"""
    c1 = _claim("c1", [_ev("e1", "k1")])
    c2 = _claim("c2", [_ev("e2", "k1")], relations=[ClaimRelation("refines", "c1")])
    svc = WikiDependencyService(repository=_FakeRepo([c1, c2], []))
    plan = svc.get_impacted_by_claim("c2", max_depth=5)
    # c2 → c1(refines 边):两者都进 affected_claims
    assert {c.claim_id for c in plan.affected_claims} >= {"c1", "c2"}
    # 环:c1 refines c2 + c2 refines c1
    c1b = _claim("c1", [_ev("e1", "k1")], relations=[ClaimRelation("refines", "c2")])
    c2b = _claim("c2", [_ev("e2", "k1")], relations=[ClaimRelation("refines", "c1")])
    svc_cycle = WikiDependencyService(repository=_FakeRepo([c1b, c2b], []))
    plan_cycle = svc_cycle.get_impacted_by_claim("c1", max_depth=5)
    assert len(plan_cycle.cycle_warnings) >= 1


def test_max_depth_truncates_claim_relation_fanout():
    """claim 关系链超 max_depth → truncated=True。"""
    claims = []
    for i in range(7):
        rel = [ClaimRelation("refines", f"c{i - 1}")] if i > 0 else []
        claims.append(_claim(f"c{i}", [_ev(f"e{i}", "k1")], relations=rel))
    svc = WikiDependencyService(repository=_FakeRepo(claims, []))
    plan = svc.get_impacted_by_claim("c6", max_depth=2)
    assert plan.truncated is True


def test_unsupported_claim_not_retracted_and_topo_sorted():
    """unsupported 影响 claim 字典序拓扑;非 published 页不进 review。"""
    c1 = _claim("c1", [_ev("e1", "k1")])
    c2 = _claim("c2", [_ev("e2", "k1")])
    page_review = _page("p_review", ["c1"], status=PageStatus.REVIEW)  # 已 review,不再变
    svc = WikiDependencyService(repository=_FakeRepo([c1, c2], [page_review]))
    plan = svc.get_impacted_by_source("k1")
    # 拓扑序字典序
    assert plan.topological_order == ["c1", "c2"]
    # 已 review 的页不重复进 affected_pages
    assert plan.affected_pages == []
