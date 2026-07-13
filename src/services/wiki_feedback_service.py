"""WikiFeedbackService — 用户反馈作用于 Claim 层(Phase 6)。

动作:
- confirm: 有非 stale supports Evidence → active；否则 error
- reject: → retracted
- correct: 更新 statement + normalized，status=draft
- needs_review: → disputed

铁律:
- 不修改 Raw Source
- 不修改 Evidence 的 knowledge_id/block(仅状态/文案)
- 经 WikiRepository.transaction 写入
- 构造函数 DI，禁全局 Config/Database/container
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from src.models.wiki_v2 import ClaimStatus, EvidenceStance, normalize_statement

logger = logging.getLogger(__name__)


class FeedbackAction(str, Enum):
    CONFIRM = "confirm"
    REJECT = "reject"
    CORRECT = "correct"
    NEEDS_REVIEW = "needs_review"


@dataclass
class FeedbackResult:
    claim_id: str
    action: str
    before_status: str
    after_status: str
    op_log_id: str = ""
    errors: list[str] = field(default_factory=list)


def _default_clock() -> str:
    return datetime.now(timezone.utc).isoformat()


class WikiFeedbackService:
    def __init__(
        self,
        repository,
        operation_log=None,
        clock: Callable[[], str] | None = None,
    ):
        self._repo = repository
        self._operation_log = operation_log
        self._clock = clock or _default_clock

    def apply(
        self,
        claim_id: str,
        action: str,
        *,
        correction: str | None = None,
        operator: str = "user",
        note: str = "",
    ) -> FeedbackResult:
        try:
            act = FeedbackAction(action)
        except ValueError:
            return FeedbackResult(
                claim_id=claim_id, action=action,
                before_status="", after_status="",
                errors=[f"unknown action: {action}"],
            )

        claim = self._repo.get_claim(claim_id)
        if claim is None:
            return FeedbackResult(
                claim_id=claim_id, action=act.value,
                before_status="", after_status="",
                errors=[f"claim not found: {claim_id}"],
            )

        before = claim.status.value
        now = self._clock()
        errors: list[str] = []

        if act is FeedbackAction.CONFIRM:
            supports = [
                e for e in claim.evidence
                if e.stance is EvidenceStance.SUPPORTS and not e.stale
            ]
            if not supports:
                return FeedbackResult(
                    claim_id=claim_id, action=act.value,
                    before_status=before, after_status=before,
                    errors=["confirm requires at least one non-stale supports evidence"],
                )
            claim.status = ClaimStatus.ACTIVE
        elif act is FeedbackAction.REJECT:
            claim.status = ClaimStatus.RETRACTED
        elif act is FeedbackAction.CORRECT:
            if not correction or not str(correction).strip():
                return FeedbackResult(
                    claim_id=claim_id, action=act.value,
                    before_status=before, after_status=before,
                    errors=["correct requires non-empty correction text"],
                )
            claim.statement = str(correction).strip()
            claim.normalized_statement = normalize_statement(claim.statement)
            claim.status = ClaimStatus.DRAFT
        elif act is FeedbackAction.NEEDS_REVIEW:
            claim.status = ClaimStatus.DISPUTED

        claim.updated_at = now
        # revision 由 transaction 在 commit 时 +1；这里传 expected_revision
        expected = claim.revision
        try:
            with self._repo.transaction() as tx:
                tx.stage_claim(claim, expected_revision=expected)
        except Exception as e:
            logger.warning("feedback apply failed for %s: %s", claim_id, e, exc_info=True)
            return FeedbackResult(
                claim_id=claim_id, action=act.value,
                before_status=before, after_status=before,
                errors=[str(e)],
            )

        after = claim.status.value
        op_id = ""
        if self._operation_log is not None:
            try:
                op_id = self._operation_log.log(
                    operation="wiki_feedback",
                    target_type="claim",
                    target_id=claim_id,
                    operator=operator,
                    source="wiki_feedback",
                    before={"status": before},
                    after={"status": after, "statement": claim.statement},
                    metadata={"action": act.value, "note": note},
                ) or ""
            except Exception:
                logger.warning("operation_log for feedback failed", exc_info=True)

        return FeedbackResult(
            claim_id=claim_id,
            action=act.value,
            before_status=before,
            after_status=after,
            op_log_id=str(op_id),
            errors=errors,
        )
