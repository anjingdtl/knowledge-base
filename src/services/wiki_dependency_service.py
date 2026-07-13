"""Canonical Wiki v2 依赖图与影响规划(Phase 5)。

依赖图真源 = canonical 状态:
    source(knowledge_id) --produces--> evidence --evidences--> claim
    claim --cited_in--> page
    claim --supersedes/refines/contradicts--> claim(环风险点)

影响规划从 repository 按需计算(遍历 list_claims/list_pages 构建邻接),
不依赖 wiki_dependencies 投影表。环检测 + max_depth(仅计 claim↔claim 传递)。
拓扑稳定:同层按 claim_id 字典序。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.models.wiki_v2 import EvidenceStance


@dataclass
class EvidenceImpact:
    evidence_id: str
    claim_id: str
    reason: str  # block_changed / block_deleted / source_deleted


@dataclass
class ClaimImpact:
    claim_id: str
    current_status: str
    proposed_status: str  # active→active | active→unsupported
    reason: str


@dataclass
class PageImpact:
    page_id: str
    current_status: str
    proposed_status: str  # published→review
    reason: str


@dataclass
class ImpactPlan:
    root: str
    affected_evidence: list[EvidenceImpact] = field(default_factory=list)
    affected_claims: list[ClaimImpact] = field(default_factory=list)
    affected_pages: list[PageImpact] = field(default_factory=list)
    topological_order: list[str] = field(default_factory=list)
    cycle_warnings: list[str] = field(default_factory=list)
    truncated: bool = False
    stats: dict = field(default_factory=dict)


class WikiDependencyService:
    def __init__(self, repository: Any, config: Any = None, *,
                 clock: Callable[[], str] | None = None) -> None:
        self._repo = repository
        self._config = config
        self._clock = clock or (lambda: "")

    def get_impacted_by_source(self, knowledge_id: str, *, max_depth: int = 5) -> ImpactPlan:
        """source(knowledge_id) 变更/删除 → 受影响 evidence/claim/page + claim 关系传递。"""
        plan = ImpactPlan(root=knowledge_id)
        claims = self._repo.list_claims()
        pages = self._repo.list_pages()

        # 1. 该 source 的所有非 stale evidence → affected
        ev_to_claim: dict[str, str] = {}
        for claim in claims:
            for ev in claim.evidence:
                if ev.knowledge_id == knowledge_id and not ev.stale:
                    plan.affected_evidence.append(EvidenceImpact(
                        evidence_id=ev.evidence_id, claim_id=claim.claim_id,
                        reason="source_deleted"))
                    ev_to_claim[ev.evidence_id] = claim.claim_id

        # 2. 持有受影响 evidence 的 claim → 评估 proposed status
        touched_claim_ids: set[str] = set(ev_to_claim.values())
        self._evaluate_claims(claims, touched_claim_ids, knowledge_id, plan)

        # 3. claim↔claim 关系传递(环检测 + max_depth)
        self._fanout_claim_relations(claims, touched_claim_ids, max_depth, plan)

        # 4. 受影响 claim → page(published→review)
        self._evaluate_pages(pages, {c.claim_id for c in plan.affected_claims}, plan)

        plan.topological_order = sorted({c.claim_id for c in plan.affected_claims})
        plan.stats = {
            "evidence": len(plan.affected_evidence),
            "claims": len(plan.affected_claims),
            "pages": len(plan.affected_pages),
            "cycles": len(plan.cycle_warnings),
        }
        return plan

    def get_impacted_by_claim(self, claim_id: str, *, max_depth: int = 5) -> ImpactPlan:
        """claim 变更 → 关联 page + claim↔claim 关系传递。"""
        plan = ImpactPlan(root=claim_id)
        claims = self._repo.list_claims()
        pages = self._repo.list_pages()
        seed = {claim_id}
        self._evaluate_claims(claims, seed, None, plan)
        self._fanout_claim_relations(claims, seed, max_depth, plan)
        self._evaluate_pages(pages, {c.claim_id for c in plan.affected_claims}, plan)
        plan.topological_order = sorted({c.claim_id for c in plan.affected_claims})
        return plan

    # ---- 内部 ----

    def _evaluate_claims(self, claims, touched_ids, knowledge_id, plan) -> None:
        """评估受影响 claim 的 proposed_status:仍有他源 supports(active)否则 unsupported。

        active 判定只看 supports evidence(与 Claim.validate() invariant 一致)。
        """
        by_id = {c.claim_id: c for c in claims}
        for cid in sorted(touched_ids):
            claim = by_id.get(cid)
            if claim is None:
                continue
            remaining = [
                e for e in claim.evidence
                if e.stance is EvidenceStance.SUPPORTS
                and not e.stale
                and (knowledge_id is None or e.knowledge_id != knowledge_id)
            ]
            proposed = "active" if remaining else "unsupported"
            plan.affected_claims.append(ClaimImpact(
                claim_id=cid, current_status=claim.status.value,
                proposed_status=proposed,
                reason="remaining_supports" if remaining else "no_remaining_supports"))

    def _fanout_claim_relations(self, claims, seed, max_depth, plan) -> None:
        """BFS claim↔claim 关系传递;visited 防环;深度超 max_depth → truncated。"""
        by_id = {c.claim_id: c for c in claims}
        visited: set[str] = set()
        frontier = list(seed)
        depth = 0
        while frontier:
            if depth > max_depth:
                plan.truncated = True
                break
            nxt: list[str] = []
            for cid in frontier:
                if cid in visited:
                    continue
                visited.add(cid)
                claim = by_id.get(cid)
                if claim is None:
                    continue
                for rel in claim.relations:
                    tid = rel.target_claim_id
                    if tid in visited:
                        plan.cycle_warnings.append(
                            f"claim relation cycle at {cid} -> {tid} ({rel.relation})")
                        continue
                    nxt.append(tid)
                    if tid not in {c.claim_id for c in plan.affected_claims}:
                        self._evaluate_claims(claims, {tid}, None, plan)
            frontier = nxt
            depth += 1

    def _evaluate_pages(self, pages, claim_ids, plan) -> None:
        for page in pages:
            if page.status.value != "published":
                continue
            if set(page.claim_ids) & claim_ids:
                plan.affected_pages.append(PageImpact(
                    page_id=page.page_id, current_status=page.status.value,
                    proposed_status="review", reason="affected_claim"))
