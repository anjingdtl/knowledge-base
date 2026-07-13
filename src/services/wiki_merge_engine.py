"""Wiki Merge Engine — cross-source Claim merge application.

Deterministic rule applier: takes (Claim, ClaimMatchDecision) pairs and applies
them to WikiRepository within a single transaction. Generates human-readable diff
and conflict review items.

Spec: CD1-CD5, action→behavior table (CD2), ClaimStatus flow (CD3), diff format (CD4).
"""
from __future__ import annotations

import copy
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.models.wiki_v2 import (
    Claim,
    ClaimRelation,
    ClaimStatus,
    EvidenceStance,
)
from src.services.wiki_claim_matcher import ClaimMatchDecision

logger = logging.getLogger(__name__)


# C1 契约:ClaimRelation.relation 合法值
# (见 docs/architecture/wiki-v2-claim-merge-contract.md §3)。新增 relation 必须先扩此集合。
CLAIM_RELATION_KINDS: frozenset[str] = frozenset({
    "supersedes", "superseded_by", "refines", "refined_by", "contradicts",
})


# ---------------------------------------------------------------------------
# MergeResult
# ---------------------------------------------------------------------------
@dataclass
class MergeResult:
    """Output of WikiMergeEngine.apply()."""

    claims_created: list[str] = field(default_factory=list)
    claims_created_active: list[str] = field(default_factory=list)
    claims_updated: list[str] = field(default_factory=list)
    claims_disputed: list[str] = field(default_factory=list)
    claims_superseded: list[str] = field(default_factory=list)
    pages_updated: list[str] = field(default_factory=list)
    review_items: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    diff: str = ""
    committed: bool = False
    tx_id: str = ""
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# WikiMergeEngine
# ---------------------------------------------------------------------------
class WikiMergeEngine:
    """Deterministic rule applier for cross-source Claim merge.

    All mutations go through WikiRepository.transaction() for atomicity.
    Does NOT call LLM or embedding services.
    """

    def __init__(self, repository: Any, config: Any = None) -> None:
        self._repo = repository
        self._config = config

    def _cfg(self, key: str, default: Any = None) -> Any:
        if self._config is not None:
            return self._config.get(key, default)
        return default

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def apply(
        self,
        items: list[tuple[Claim, ClaimMatchDecision]],
        page: Any | None = None,
        now: str = "",
    ) -> MergeResult:
        """Apply all items in a single repository transaction.

        Batch aggregation (CD1): if multiple items target the same claim,
        evidence is accumulated before staging (avoids revision jumps).

        Args:
            items: list of (new_claim, match_decision) tuples.
            page: optional WikiPage to track claim_ids.
            now: injected timestamp for updated_at fields.

        Returns:
            MergeResult with diff, review items, errors, etc.
        """
        result = MergeResult()

        # Gate: wiki.claims.enabled
        if not self._cfg("wiki.claims.enabled", True):
            result.committed = False
            for claim, _ in items:
                result.skipped.append(claim.claim_id)
            return result

        # Phase 1: Pre-process — aggregate evidence by target_claim_id
        # This prevents multiple stages of the same target claim.
        aggregated: dict[
            str, list[tuple[Claim, ClaimMatchDecision]]
        ] = defaultdict(list)
        single_items: list[tuple[Claim, ClaimMatchDecision]] = []
        new_items: list[tuple[Claim, ClaimMatchDecision]] = []
        unresolved_items: list[tuple[Claim, ClaimMatchDecision]] = []
        supersedes_items: list[tuple[Claim, ClaimMatchDecision]] = []
        refines_items: list[tuple[Claim, ClaimMatchDecision]] = []

        for new_claim, decision in items:
            if decision.action == "new":
                new_items.append((new_claim, decision))
            elif decision.action == "unresolved":
                unresolved_items.append((new_claim, decision))
            elif decision.action == "supersedes":
                supersedes_items.append((new_claim, decision))
            elif decision.action == "refines":
                refines_items.append((new_claim, decision))
            elif decision.target_claim_id:
                aggregated[decision.target_claim_id].append((new_claim, decision))
            else:
                single_items.append((new_claim, decision))

        # Capture original page claim_ids for change detection
        original_page_claim_ids = list(page.claim_ids) if page is not None else []

        try:
            with self._repo.transaction() as tx:
                result.tx_id = tx.tx_id
                # Process aggregated items (supports, duplicate, contradicts)
                for target_id, group in sorted(aggregated.items()):
                    self._apply_aggregated(target_id, group, page, result, now, tx)

                # Process new items
                for new_claim, decision in new_items:
                    self._apply_new(new_claim, page, result, now, tx)

                # Process supersedes items
                for new_claim, decision in supersedes_items:
                    self._apply_supersedes(new_claim, decision, page, result, now, tx)

                # Process refines items
                for new_claim, decision in refines_items:
                    self._apply_refines(new_claim, decision, page, result, now, tx)

                # Process unresolved items (no repo writes, just review items)
                for new_claim, decision in unresolved_items:
                    self._apply_unresolved(new_claim, decision, result)

                # Process remaining single items
                for new_claim, decision in single_items:
                    self._apply_unresolved(new_claim, decision, result)

                # Stage page if claim_ids changed
                if page is not None and page.claim_ids != original_page_claim_ids:
                    self._stage_page_if_needed(page, result, tx)

            result.committed = True
        except Exception:
            result.committed = False
            raise

        # Build diff after commit
        result.diff = self._build_diff(result)

        return result

    # ------------------------------------------------------------------
    # Aggregated: supports/duplicate/contradicts → single stage
    # Note: refines is routed to a separate path (creates new claim).
    # ------------------------------------------------------------------
    def _apply_aggregated(
        self,
        target_id: str,
        group: list[tuple[Claim, ClaimMatchDecision]],
        page: Any | None,
        result: MergeResult,
        now: str,
        tx: Any,
    ) -> None:
        """Apply a batch of items targeting the same claim in one stage."""
        target = self._repo.get_claim(target_id)
        if target is None:
            for claim, _ in group:
                result.errors.append(f"target claim {target_id} not found, skipping {claim.claim_id}")
                result.skipped.append(claim.claim_id)
            return

        # Determine the dominant action (most severe wins)
        action = self._dominant_action(group)

        # Deep copy to avoid mutating the repo-read object
        updated = copy.deepcopy(target)
        updated.updated_at = now
        updated.revision = target.revision  # repo will increment on save

        changed = True
        if action == "contradicts":
            self._do_contradicts(updated, group, result)
        elif action in ("supports", "duplicate"):
            changed = self._do_supports_or_duplicate(updated, group, result)

        if not changed:
            return

        # Validate before staging
        errors = updated.validate()
        if errors:
            result.errors.append(f"claim {target_id} validation failed: {errors}")
            # Skip staging but don't throw (CD3 tolerance)
            for claim, _ in group:
                result.skipped.append(claim.claim_id)
            return

        tx.stage_claim(updated, expected_revision=target.revision)
        result.claims_updated.append(target_id)

        # Track status changes
        if updated.status != target.status:
            if updated.status == ClaimStatus.DISPUTED:
                result.claims_disputed.append(target_id)

    def _dominant_action(self, group: list[tuple[Claim, ClaimMatchDecision]]) -> str:
        """Most severe action wins: contradicts > duplicate > supports.

        Refines items are routed to a separate standalone path in apply()
        and never reach _apply_aggregated, so refines is not considered here.
        """
        actions = {d.action for _, d in group}
        if "contradicts" in actions:
            return "contradicts"
        if "duplicate" in actions:
            return "duplicate"
        return "supports"

    # ------------------------------------------------------------------
    # Private: action implementations (CD2)
    # ------------------------------------------------------------------
    def _do_supports_or_duplicate(
        self,
        target: Claim,
        group: list[tuple[Claim, ClaimMatchDecision]],
        result: MergeResult,
    ) -> bool:
        """Add evidence from all items, deduplicating for 'duplicate' actions."""
        # Build set of existing evidence keys for dedup
        existing_keys: set[tuple[str, str | None]] = set()
        for ev in target.evidence:
            existing_keys.add((ev.knowledge_id, ev.block_id))

        added = False
        for new_claim, _decision in group:
            for ev in new_claim.evidence:
                key = (ev.knowledge_id, ev.block_id)
                if key in existing_keys:
                    result.skipped.append(
                        f"evidence {ev.evidence_id} (knowledge_id={ev.knowledge_id}, "
                        f"block={ev.block_id}) already exists in {target.claim_id}"
                    )
                    continue
                # Add evidence with original stance
                target.evidence.append(copy.deepcopy(ev))
                existing_keys.add(key)
                added = True
        return added

    def _do_contradicts(
        self,
        target: Claim,
        group: list[tuple[Claim, ClaimMatchDecision]],
        result: MergeResult,
    ) -> None:
        """Mark target as DISPUTED, add contradicts evidence."""
        existing_keys: set[tuple[str, str | None]] = set()
        for ev in target.evidence:
            existing_keys.add((ev.knowledge_id, ev.block_id))

        for new_claim, decision in group:
            for ev in new_claim.evidence:
                key = (ev.knowledge_id, ev.block_id)
                if key in existing_keys:
                    continue
                ev_copy = copy.deepcopy(ev)
                ev_copy.stance = EvidenceStance.CONTRADICTS
                target.evidence.append(ev_copy)
                existing_keys.add(key)

        target.status = ClaimStatus.DISPUTED
        result.review_items.append({
            "type": "conflict",
            "claim_id": target.claim_id,
            "action": "contradicts",
            "message": f"Claim {target.claim_id} marked DISPUTED due to conflicting evidence",
        })

    def _apply_new(
        self,
        new_claim: Claim,
        page: Any | None,
        result: MergeResult,
        now: str,
        tx: Any,
    ) -> None:
        """Save a new claim as DRAFT (CD2)."""
        draft = copy.deepcopy(new_claim)
        draft.status = ClaimStatus.DRAFT
        draft.updated_at = now
        draft.created_at = now

        errors = draft.validate()
        if errors:
            result.errors.append(f"new claim {draft.claim_id} validation failed: {errors}")
            result.skipped.append(draft.claim_id)
            return

        tx.stage_claim(draft, expected_revision=None)
        result.claims_created.append(draft.claim_id)

        if page is not None:
            if draft.claim_id not in page.claim_ids:
                page.claim_ids.append(draft.claim_id)

    def _apply_supersedes(
        self,
        new_claim: Claim,
        decision: ClaimMatchDecision,
        page: Any | None,
        result: MergeResult,
        now: str,
        tx: Any,
    ) -> None:
        """Supersede old claim, create new ACTIVE claim (CD2).

        Atomic: both old (SUPERSEDED) and new (ACTIVE) must pass validate
        before either is staged.  Prevents half-write where old is SUPERSEDED
        but no replacement ACTIVE claim exists.
        """
        old_id = decision.target_claim_id
        if old_id is None:
            return

        old = self._repo.get_claim(old_id)
        if old is None:
            result.errors.append(f"target claim {old_id} not found for supersedes")
            result.skipped.append(new_claim.claim_id)
            return

        # Prepare old claim: SUPERSEDED + relation (not staged yet)
        old_updated = copy.deepcopy(old)
        old_updated.status = ClaimStatus.SUPERSEDED
        old_updated.updated_at = now
        old_updated.relations.append(
            ClaimRelation(relation="superseded_by", target_claim_id=new_claim.claim_id)
        )

        # Prepare new claim as ACTIVE (not staged yet)
        new_active = copy.deepcopy(new_claim)
        new_active.status = ClaimStatus.ACTIVE
        new_active.updated_at = now
        new_active.created_at = now
        new_active.relations.append(
            ClaimRelation(relation="supersedes", target_claim_id=old_id)
        )

        # Validate BOTH before staging either — atomic (no half-write)
        old_errors = old_updated.validate()
        if old_errors:
            result.errors.append(f"superseded claim {old_id} validation: {old_errors}")
            result.skipped.append(new_claim.claim_id)
            return

        new_errors = new_active.validate()
        if new_errors:
            result.errors.append(f"new superseding claim {new_active.claim_id} validation: {new_errors}")
            result.skipped.append(new_claim.claim_id)
            return

        # Both valid — stage old (SUPERSEDED) then new (ACTIVE)
        tx.stage_claim(old_updated, expected_revision=old.revision)
        result.claims_superseded.append(old_id)
        result.claims_updated.append(old_id)

        tx.stage_claim(new_active, expected_revision=None)
        result.claims_created.append(new_active.claim_id)
        result.claims_created_active.append(new_active.claim_id)

        # Page claim_ids: replace old with new
        if page is not None:
            if old_id in page.claim_ids:
                page.claim_ids.remove(old_id)
            if new_active.claim_id not in page.claim_ids:
                page.claim_ids.append(new_active.claim_id)

    def _apply_refines(
        self,
        new_claim: Claim,
        decision: ClaimMatchDecision,
        page: Any | None,
        result: MergeResult,
        now: str,
        tx: Any,
    ) -> None:
        """Refine target, create new DRAFT claim (CD2)."""
        # This is handled via aggregation; kept for standalone use.
        target_id = decision.target_claim_id
        if target_id is None:
            return

        target = self._repo.get_claim(target_id)
        if target is None:
            result.errors.append(f"target claim {target_id} not found for refines")
            result.skipped.append(new_claim.claim_id)
            return

        # Add refined_by relation to target
        target_updated = copy.deepcopy(target)
        target_updated.updated_at = now
        target_updated.relations.append(
            ClaimRelation(relation="refined_by", target_claim_id=new_claim.claim_id)
        )

        # New claim as DRAFT — validate BOTH before staging either (atomic, no half-write)
        new_draft = copy.deepcopy(new_claim)
        new_draft.status = ClaimStatus.DRAFT
        new_draft.updated_at = now
        new_draft.created_at = now

        target_errors = target_updated.validate()
        if target_errors:
            result.errors.append(f"refined target {target_id} validation: {target_errors}")
            result.skipped.append(new_claim.claim_id)
            return

        new_errors = new_draft.validate()
        if new_errors:
            result.errors.append(f"refines new draft {new_draft.claim_id} validation: {new_errors}")
            result.skipped.append(new_draft.claim_id)
            return

        tx.stage_claim(target_updated, expected_revision=target.revision)
        result.claims_updated.append(target_id)
        tx.stage_claim(new_draft, expected_revision=None)
        result.claims_created.append(new_draft.claim_id)

        # Page: append
        if page is not None:
            if new_draft.claim_id not in page.claim_ids:
                page.claim_ids.append(new_draft.claim_id)

    def _apply_unresolved(
        self,
        new_claim: Claim,
        decision: ClaimMatchDecision,
        result: MergeResult,
    ) -> None:
        """Only add review item, no repo writes (CD2)."""
        result.skipped.append(new_claim.claim_id)
        result.review_items.append({
            "type": "unresolved",
            "claim_id": new_claim.claim_id,
            "target_claim_id": decision.target_claim_id,
            "action": "unresolved",
            "message": f"Claim {new_claim.claim_id} unresolved vs {decision.target_claim_id}, needs human review",
        })

    def _stage_page_if_needed(
        self,
        page: Any,
        result: MergeResult,
        tx: Any,
    ) -> None:
        """Stage page update if claim_ids changed."""
        errors = page.validate()
        if errors:
            result.errors.append(f"page {page.page_id} validation: {errors}")
            return
        tx.stage_page(page, expected_revision=page.revision)
        result.pages_updated.append(page.page_id)

    # ------------------------------------------------------------------
    # Diff builder (CD4)
    # ------------------------------------------------------------------
    def _build_diff(self, result: MergeResult) -> str:
        """Build human-readable, stable diff text (CD4)."""
        lines: list[str] = []

        # Claims created (sorted) — distinguish active (supersedes) vs draft
        for cid in sorted(result.claims_created):
            if cid in result.claims_created_active:
                lines.append(f"[claim:{cid}] created (active)")
            else:
                lines.append(f"[claim:{cid}] created (draft)")

        # Claims updated (sorted) — status changes
        for cid in sorted(result.claims_updated):
            if cid in result.claims_disputed:
                lines.append(f"[claim:{cid}] status: active -> disputed")
            if cid in result.claims_superseded:
                lines.append(f"[claim:{cid}] status: active -> superseded")

        # Evidence additions — exclude superseded claims (their change is status/relation, not evidence)
        for cid in sorted(result.claims_updated):
            if cid not in result.claims_created and cid not in result.claims_superseded:
                lines.append(f"[claim:{cid}] +evidence merged")

        # Pages updated (sorted)
        for pid in sorted(result.pages_updated):
            lines.append(f"[page:{pid}] claim_ids updated")

        # Skipped items
        for s in sorted(result.skipped):
            lines.append(f"[skipped] {s}")

        return "\n".join(lines) if lines else "(no changes)"
