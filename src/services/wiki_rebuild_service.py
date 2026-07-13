"""Canonical Wiki v2 来源失效传播 staged rebuild(Phase 5)。

source 更新/删除 → block 哈希比对 → evidence stale → claim/page 状态迁移 →
WikiRepository 事务落盘 → projection 刷新。保守:published→review,unsupported 不 retract。

plan_rebuild 为 dry-run 语义(不写);rebuild 执行事务(T5.2b)。
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.models.wiki_v2 import ClaimStatus, EvidenceStance, PageStatus
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


class _RebuildCancelled(Exception):
    """rebuild 执行中协作取消:抛出以让 WikiRepository 事务回滚已 stage 的部分。"""


class RebuildJob:
    """rebuild 协作取消句柄(同步进程内)。"""

    def __init__(self) -> None:
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()


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

    def rebuild(self, knowledge_id: str, *, event: str, job: RebuildJob | None = None,
                dry_run: bool = False, max_depth: int | None = None,
                max_pages_per_job: int | None = None) -> RebuildResult:
        """按 plan 执行 staged rebuild。dry_run 只规划;job 协作取消生效。

        取消语义:进入前取消 → 直接返回 cancelled;执行中取消 → 抛 _RebuildCancelled
        让 WikiRepository 事务回滚已 stage 的部分(一致状态)。projection 刷新失败不回滚
        canonical(铁律 §3.4),只记 warning。
        """
        plan = self.plan_rebuild(knowledge_id, event=event, max_depth=max_depth,
                                 max_pages_per_job=max_pages_per_job)
        result = RebuildResult(knowledge_id=knowledge_id, event=event, plan=plan)
        if dry_run or (job is not None and job.cancelled):
            result.cancelled = bool(job and job.cancelled)
            return result

        now = self._clock()
        impacted_claim_ids = {c.claim_id for c in plan.affected_claims}
        impacted_page_ids = {p.page_id for p in plan.affected_pages}
        claims_by_id = {c.claim_id: c for c in self._repo.list_claims()}
        pages_by_id = {p.page_id: p for p in self._repo.list_pages()}
        stale_ev_ids = {e.evidence_id for e in plan.affected_evidence}

        try:
            with self._repo.transaction() as tx:
                for cid in sorted(impacted_claim_ids):
                    if job is not None and job.cancelled:
                        raise _RebuildCancelled()
                    claim = claims_by_id.get(cid)
                    if claim is None:
                        continue
                    impact = next(c for c in plan.affected_claims if c.claim_id == cid)
                    mutated = self._mutate_claim(claim, impact, stale_ev_ids, now)
                    tx.stage_claim(mutated, expected_revision=claim.revision)
                for pid in sorted(impacted_page_ids):
                    if job is not None and job.cancelled:
                        raise _RebuildCancelled()
                    page = pages_by_id.get(pid)
                    if page is None:
                        continue
                    page.status = PageStatus.REVIEW
                    page.updated_at = now
                    tx.stage_page(page, expected_revision=page.revision)
                if job is not None and job.cancelled:
                    raise _RebuildCancelled()
                tx.commit()
        except _RebuildCancelled:
            result.cancelled = True
            result.warnings.append("rebuild cancelled; transaction rolled back")
            return result

        result.committed = True
        try:
            self._projection.process_outbox()
            drift = self._projection.verify_parity()
            if drift:
                result.warnings.append(f"projection drift after rebuild: {len(drift)} findings")
        except Exception as exc:  # noqa: BLE001 - projection 失败不回滚 canonical
            result.warnings.append(f"projection refresh failed: {exc}")
        return result

    def _mutate_claim(self, claim, impact, stale_ev_ids: set[str], now: str):
        """标 stale evidence + 迁移 status。不 retract(保留审计,对齐 d03)。"""
        for ev in claim.evidence:
            if ev.evidence_id in stale_ev_ids:
                ev.stale = True
                ev.stale_at = now
        if impact.proposed_status == "unsupported":
            claim.status = ClaimStatus.UNSUPPORTED
        claim.updated_at = now
        return claim
