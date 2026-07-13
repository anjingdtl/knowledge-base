"""Dry-run migration support for strict Claim serving validation.

This Phase 2 helper intentionally never invents review or publication proof.
Phase 8 owns the separately authorised apply/backup/rollback workflow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.models.wiki_v2 import ClaimStatus


@dataclass(frozen=True)
class ServingValidationMigrationReport:
    scanned_claims: int = 0
    active_claims: int = 0
    already_current: int = 0
    missing_proof: int = 0
    review_proposals: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": True,
            "scanned_claims": self.scanned_claims,
            "active_claims": self.active_claims,
            "already_current": self.already_current,
            "missing_proof": self.missing_proof,
            "review_proposals": [dict(item) for item in self.review_proposals],
        }


class WikiServingValidationMigrator:
    """Inspect legacy Claims without writing records or altering status."""

    def __init__(self, repository) -> None:
        self._repository = repository

    def dry_run(self) -> ServingValidationMigrationReport:
        claims = list(self._repository.list_claims() or [])
        active = [claim for claim in claims if claim.status is ClaimStatus.ACTIVE]
        current = 0
        proposals: list[dict[str, Any]] = []
        for claim in active:
            proof = claim.serving_validation
            if proof and (
                proof.passed
                and proof.review_approved
                and proof.validated_revision == claim.revision
                and proof.published_revision == claim.revision
                and bool(proof.serving_evidence_ids)
            ):
                current += 1
                continue
            proposals.append({
                "claim_id": claim.claim_id,
                "review_type": "serving_validation_migration",
                "reason_codes": ["serving_validation_unproven"],
                "proposed_action": "review_then_validate_then_explicit_publish",
            })
        return ServingValidationMigrationReport(
            scanned_claims=len(claims),
            active_claims=len(active),
            already_current=current,
            missing_proof=len(proposals),
            review_proposals=tuple(proposals),
        )

    def apply(self) -> None:
        raise RuntimeError("Serving validation apply is gated until Phase 8; use dry_run only")
