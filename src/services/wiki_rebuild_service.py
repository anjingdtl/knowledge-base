"""Canonical Wiki v2 来源失效传播 staged rebuild(Phase 5)。

source 更新/删除 → block 哈希比对 → evidence stale → claim/page 状态迁移 →
WikiRepository 事务落盘 → projection 刷新。保守:published→review,unsupported 不 retract。

plan_rebuild 为 dry-run 语义(不写);rebuild 执行事务(T5.2b)。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.models.wiki_v2 import EvidenceStance
from src.services.wiki_claim_extractor import compute_excerpt_hash
from src.services.wiki_dependency_service import (
    ClaimImpact,
    EvidenceImpact,
    ImpactPlan,
    PageImpact,
    WikiDependencyService,
)


@dataclass
class RebuildResult:
    knowledge_id: str
    event: str
    plan: ImpactPlan
    committed: bool = False
    cancelled: bool = False
    warnings: list[str] = field(default_factory=list)


class WikiRebuildService:
    def __init__(self, repository: Any, projection: Any, block_repository: Any,
                 dependency_service: WikiDependencyService, config: Any = None, *,
                 clock: Callable[[], str] | None = None) -> None:
        self._repo = repository
        self._projection = projection
        self._blocks = block_repository
        self._dep = dependency_service
        self._config = config
        self._clock = clock or (lambda: "")

    def _cfg(self, key: str, default: Any = None) -> Any:
        if self._config is not None:
            return self._config.get(key, default)
        return default

    def plan_rebuild(self, knowledge_id: str, *, event: str,
                     max_depth: int | None = None,
                     max_pages_per_job: int | None = None) -> ImpactPlan:
        """规划受影响集(dry-run 语义:不写)。event ∈ {"update","delete"}。"""
        md = max_depth if max_depth is not None else int(self._cfg("wiki.rebuild.max_depth", 5))
        mpp = (max_pages_per_job if max_pages_per_job is not None
               else int(self._cfg("wiki.rebuild.max_pages_per_job", 100)))
        plan = self._dep.get_impacted_by_source(knowledge_id, max_depth=md)
        plan.stats["event"] = event

        if event == "delete":
            # dep 已正确标 source_deleted + 他源 remaining + pages,直接截断
            return self._cap_pages(plan, mpp)

        # update:block 哈希比对,精修 evidence reason + 失效集
        current_hashes = self._current_block_hashes(knowledge_id)
        refined: list[EvidenceImpact] = []
        claims_by_id = {c.claim_id: c for c in self._repo.list_claims()}
        for ev_impact in plan.affected_evidence:
            claim = claims_by_id.get(ev_impact.claim_id)
            ev = (next((e for e in claim.evidence if e.evidence_id == ev_impact.evidence_id), None)
                  if claim else None)
            if ev is None or not ev.block_id:
                refined.append(ev_impact)  # 无 block_id 证据:保守按失效
                continue
            if ev.block_id not in current_hashes:
                refined.append(EvidenceImpact(ev_impact.evidence_id, ev_impact.claim_id, "block_deleted"))
            elif current_hashes[ev.block_id] != ev.excerpt_hash:
                refined.append(EvidenceImpact(ev_impact.evidence_id, ev_impact.claim_id, "block_changed"))
            else:
                continue  # 未变化 → 不失效(u01/u03)
        plan.affected_evidence = refined
        # block-diff 后失效集改变 → claims/pages 必须基于真正 stale evidence 重建
        self._reevaluate_claims(plan)
        self._reevaluate_pages(plan)
        return self._cap_pages(plan, mpp)

    def _current_block_hashes(self, knowledge_id: str) -> dict[str, str]:
        """当前 knowledge_id 的 {block_id: compute_excerpt_hash(content)}。"""
        rows = self._blocks.list_by_page(knowledge_id, limit=10000)
        out: dict[str, str] = {}
        for blk in rows:
            bid = getattr(blk, "id", None) or getattr(blk, "block_id", None)
            content = getattr(blk, "content", "")
            if bid and content:
                out[str(bid)] = compute_excerpt_hash(content)
        return out

    def _reevaluate_claims(self, plan: ImpactPlan) -> None:
        """block-diff 后重估:只有真正含 stale evidence 的 claim 才进 affected_claims。

        active 判定只看 supports evidence 且未被本轮标 stale(与 Claim.validate() 一致)。
        """
        stale_ev_by_claim: dict[str, set[str]] = {}
        for ev in plan.affected_evidence:
            stale_ev_by_claim.setdefault(ev.claim_id, set()).add(ev.evidence_id)
        claims_by_id = {c.claim_id: c for c in self._repo.list_claims()}
        new_impacts: list[ClaimImpact] = []
        for cid in sorted(stale_ev_by_claim):
            claim = claims_by_id.get(cid)
            if claim is None:
                continue
            stale_set = stale_ev_by_claim[cid]
            remaining = [
                e for e in claim.evidence
                if e.stance is EvidenceStance.SUPPORTS
                and not e.stale
                and e.evidence_id not in stale_set
            ]
            proposed = "active" if remaining else "unsupported"
            new_impacts.append(ClaimImpact(
                claim_id=cid, current_status=claim.status.value,
                proposed_status=proposed,
                reason="remaining_supports" if remaining else "no_remaining_supports"))
        plan.affected_claims = new_impacts

    def _reevaluate_pages(self, plan: ImpactPlan) -> None:
        """基于当前 affected_claims 重算受影响 published page(→ review)。"""
        claim_ids = {c.claim_id for c in plan.affected_claims}
        pages = self._repo.list_pages()
        plan.affected_pages = [
            PageImpact(page_id=p.page_id, current_status=p.status.value,
                       proposed_status="review", reason="affected_claim")
            for p in pages
            if p.status.value == "published" and set(p.claim_ids) & claim_ids
        ]

    def _cap_pages(self, plan: ImpactPlan, max_pages: int) -> ImpactPlan:
        if len(plan.affected_pages) > max_pages:
            plan.stats["pending_pages"] = [p.page_id for p in plan.affected_pages[max_pages:]]
            plan.affected_pages = plan.affected_pages[:max_pages]
            plan.truncated = True
        return plan
