"""MaintenancePolicyEngine — risk-level decisions for Wiki maintenance (Phase 5).

Spec §11.4–§11.5. Single policy entry so Scheduler / API / GUI / MCP do not
fork decision logic. Does not write Canonical state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.utils.knowledge_mode import (
    MODE_AUTHORING,
    MODE_EVIDENCE_ONLY,
    MODE_VERIFIED,
    allows_authoring,
    resolve_knowledge_mode,
)

# Risk levels
R0, R1, R2, R3, R4 = "R0", "R1", "R2", "R3", "R4"
RISK_ORDER = {R0: 0, R1: 1, R2: 2, R3: 3, R4: 4}

# Decisions
DECIDE_AUTO = "auto_execute"
DECIDE_REVIEW = "create_review"
DECIDE_BLOCK = "block"
DECIDE_DRY_RUN = "dry_run_only"

# Job types → default risk
_JOB_RISK: dict[str, str] = {
    "health_snapshot": R0,
    "impact_plan": R0,
    "observation": R0,
    "mark_evidence_stale": R1,
    "downgrade_unsupported": R1,
    "move_page_to_review": R1,
    "exclude_from_serving": R1,
    "protective_rebuild": R1,
    "outbox_recover": R2,
    "projection_repair": R2,
    "reindex_missing_blocks": R2,
    "validation": R2,
    "draft_generation": R3,
    "correction_suggestion": R3,
    "claim_draft": R3,
    "page_draft": R3,
    "publish": R4,
    "conflict_resolution": R4,
    "retract": R4,
    "delete": R4,
    "migrate_primary": R4,
    "merge_claims": R4,
}


@dataclass
class PolicyDecision:
    decision: str
    risk_level: str
    reason_codes: list[str] = field(default_factory=list)
    required_checks: list[str] = field(default_factory=list)
    required_permissions: list[str] = field(default_factory=list)
    job_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "risk_level": self.risk_level,
            "reason_codes": list(self.reason_codes),
            "required_checks": list(self.required_checks),
            "required_permissions": list(self.required_permissions),
            "job_type": self.job_type,
        }


def default_risk_for_job(job_type: str) -> str:
    return _JOB_RISK.get(job_type, R3)


class MaintenancePolicyEngine:
    """Central policy: mode + automation_level + config → decision."""

    def __init__(self, config: Mapping[str, Any] | None = None):
        self._config = config or {}

    def _cfg(self, key: str, default: Any = None) -> Any:
        if isinstance(self._config, dict):
            # support both nested and dotted
            if key in self._config:
                return self._config[key]
            parts = key.split(".")
            obj: Any = self._config
            for p in parts:
                if isinstance(obj, dict):
                    obj = obj.get(p)
                else:
                    # try Config-like
                    getter = getattr(self._config, "get", None)
                    if getter:
                        return getter(key, default)
                    return default
            return default if obj is None else obj
        getter = getattr(self._config, "get", None)
        if getter:
            return getter(key, default)
        return default

    def knowledge_mode(self) -> str:
        raw = self._cfg("knowledge_workflow.mode") or self._cfg("knowledge_workflow", {})
        if isinstance(raw, dict):
            raw = raw.get("mode")
        try:
            return resolve_knowledge_mode(raw)
        except Exception:  # noqa: BLE001
            return MODE_VERIFIED

    def automation_level(self) -> str:
        # observe | supervised | managed
        level = self._cfg("maintenance.automation_level")
        if level:
            return str(level).lower()
        mode = self.knowledge_mode()
        if mode == MODE_EVIDENCE_ONLY:
            return "observe"
        if mode == MODE_AUTHORING:
            return "supervised"
        return "supervised"

    def maintenance_enabled(self) -> bool:
        if self._cfg("maintenance.enabled", True) is False:
            return False
        if self._cfg("maintenance.center_enabled", True) is False:
            return False
        return True

    def evaluate(
        self,
        job_type: str,
        *,
        risk_level: str | None = None,
        human_confirmed: bool = False,
        confirmation_token: str | None = None,
        dry_run: bool = False,
        reason_codes: Sequence[str] | None = None,
    ) -> PolicyDecision:
        reasons: list[str] = list(reason_codes or [])
        risk = risk_level or default_risk_for_job(job_type)
        if risk not in RISK_ORDER:
            risk = R3
            reasons.append("unknown_risk_default_r3")

        if not self.maintenance_enabled():
            return PolicyDecision(
                decision=DECIDE_BLOCK,
                risk_level=risk,
                reason_codes=reasons + ["maintenance_disabled"],
                job_type=job_type,
            )

        if dry_run or job_type in ("impact_plan", "health_snapshot", "observation"):
            return PolicyDecision(
                decision=DECIDE_DRY_RUN if dry_run or job_type == "impact_plan" else DECIDE_AUTO,
                risk_level=R0 if job_type in ("impact_plan", "health_snapshot", "observation") else risk,
                reason_codes=reasons + ["observation_or_dry_run"],
                required_checks=[],
                job_type=job_type,
            )

        level = self.automation_level()
        mode = self.knowledge_mode()
        policy = self._cfg("maintenance.policy") or {}
        if not isinstance(policy, dict):
            policy = {}

        # observe: never write
        if level == "observe":
            if RISK_ORDER[risk] >= RISK_ORDER[R1]:
                return PolicyDecision(
                    decision=DECIDE_DRY_RUN if RISK_ORDER[risk] <= RISK_ORDER[R2] else DECIDE_REVIEW,
                    risk_level=risk,
                    reason_codes=reasons + ["automation_observe"],
                    required_checks=["human_review"] if RISK_ORDER[risk] >= RISK_ORDER[R3] else [],
                    job_type=job_type,
                )

        # R4 always needs human unless explicitly confirmed
        if RISK_ORDER[risk] >= RISK_ORDER[R4]:
            if not human_confirmed and not confirmation_token:
                return PolicyDecision(
                    decision=DECIDE_BLOCK if not allows_authoring(mode) and job_type in (
                        "publish", "delete", "migrate_primary", "merge_claims",
                    ) else DECIDE_REVIEW,
                    risk_level=R4,
                    reason_codes=reasons + ["r4_requires_human"],
                    required_checks=["human_confirmation", "validation", "projection_parity"],
                    required_permissions=["maintenance.r4"],
                    job_type=job_type,
                )
            # Guard: auto_* flags must still be false by default
            flag = {
                "publish": "auto_publish",
                "conflict_resolution": "auto_resolve_conflicts",
                "retract": "auto_retract",
                "delete": "auto_delete",
                "migrate_primary": "auto_migrate_primary",
                "merge_claims": "auto_merge_claims",
            }.get(job_type)
            if flag and not policy.get(flag, False) and not human_confirmed:
                return PolicyDecision(
                    decision=DECIDE_BLOCK,
                    risk_level=R4,
                    reason_codes=reasons + [f"policy_forbids_{flag}"],
                    required_permissions=["maintenance.r4"],
                    job_type=job_type,
                )
            return PolicyDecision(
                decision=DECIDE_AUTO,
                risk_level=R4,
                reason_codes=reasons + ["human_confirmed_r4"],
                required_checks=["validation", "projection_parity"],
                required_permissions=["maintenance.r4"],
                job_type=job_type,
            )

        # R3 semantic drafts: authoring only for generation; always review to publish
        if RISK_ORDER[risk] >= RISK_ORDER[R3]:
            if not allows_authoring(mode) and job_type in (
                "draft_generation", "claim_draft", "page_draft",
            ):
                # verified may still create correction *suggestions* as review items
                if job_type == "correction_suggestion" or policy.get(
                    "auto_generate_correction_suggestions", True
                ):
                    return PolicyDecision(
                        decision=DECIDE_REVIEW,
                        risk_level=R3,
                        reason_codes=reasons + ["r3_review_only_verified"],
                        required_checks=["human_review"],
                        job_type=job_type,
                    )
                return PolicyDecision(
                    decision=DECIDE_BLOCK,
                    risk_level=R3,
                    reason_codes=reasons + ["authoring_required_for_draft"],
                    job_type=job_type,
                )
            # Never auto-publish drafts
            return PolicyDecision(
                decision=DECIDE_REVIEW,
                risk_level=R3,
                reason_codes=reasons + ["r3_creates_draft_for_review"],
                required_checks=["human_review", "validation"],
                job_type=job_type,
            )

        # R1 protective: auto when policy allows (default true under supervised/managed)
        if risk == R1:
            if not policy.get("auto_protective_actions", True) and level != "managed":
                return PolicyDecision(
                    decision=DECIDE_REVIEW,
                    risk_level=R1,
                    reason_codes=reasons + ["protective_auto_disabled"],
                    job_type=job_type,
                )
            # R1 must reduce serving surface — always allowed in verified
            return PolicyDecision(
                decision=DECIDE_AUTO,
                risk_level=R1,
                reason_codes=reasons + ["r1_protective_auto"],
                required_checks=["repository_transaction", "operation_log"],
                job_type=job_type,
            )

        # R2 structural
        if risk == R2:
            auto_ok = {
                "outbox_recover": policy.get("auto_recover_outbox", True),
                "projection_repair": policy.get("auto_rebuild_projection_on_safe_drift", False),
                "reindex_missing_blocks": policy.get("auto_reindex_missing_blocks", False),
                "validation": True,
            }.get(job_type, policy.get("auto_retry_failed_jobs", True))
            if auto_ok or level == "managed":
                return PolicyDecision(
                    decision=DECIDE_AUTO,
                    risk_level=R2,
                    reason_codes=reasons + ["r2_structural_auto"],
                    required_checks=["validation"],
                    job_type=job_type,
                )
            return PolicyDecision(
                decision=DECIDE_REVIEW,
                risk_level=R2,
                reason_codes=reasons + ["r2_needs_review"],
                job_type=job_type,
            )

        # R0
        return PolicyDecision(
            decision=DECIDE_AUTO,
            risk_level=R0,
            reason_codes=reasons + ["r0_observe"],
            job_type=job_type,
        )
