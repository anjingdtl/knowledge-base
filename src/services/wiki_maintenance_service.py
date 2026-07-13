"""WikiMaintenanceService — orchestration control plane (Phase 5).

Wires Impact Plan, Policy Engine, protective rebuild hooks, health snapshot
and review queue. Does NOT become a second fact store: all claim/page writes
go through WikiRepository / existing rebuild & feedback services.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from src.services.maintenance_policy import (
    DECIDE_AUTO,
    DECIDE_BLOCK,
    DECIDE_DRY_RUN,
    DECIDE_REVIEW,
    MaintenancePolicyEngine,
    PolicyDecision,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class MaintenanceJobRecord:
    job_id: str
    job_type: str
    risk_level: str
    status: str
    idempotency_key: str = ""
    attempt: int = 0
    source_event_ids: list[str] = field(default_factory=list)
    affected_claim_ids: list[str] = field(default_factory=list)
    affected_page_ids: list[str] = field(default_factory=list)
    decision: str = ""
    reason_codes: list[str] = field(default_factory=list)
    correlation_id: str = ""
    error: str = ""
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    result_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "risk_level": self.risk_level,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "attempt": self.attempt,
            "source_event_ids": list(self.source_event_ids),
            "affected_claim_ids": list(self.affected_claim_ids),
            "affected_page_ids": list(self.affected_page_ids),
            "decision": self.decision,
            "reason_codes": list(self.reason_codes),
            "correlation_id": self.correlation_id,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result_summary": dict(self.result_summary),
        }


@dataclass
class ReviewItemRecord:
    review_id: str
    review_type: str
    priority: str
    risk_level: str
    before: dict[str, Any] = field(default_factory=dict)
    proposed: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    status: str = "open"
    created_by: str = "system"
    created_at: str = ""
    job_id: str = ""
    claim_id: str = ""
    page_id: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "review_type": self.review_type,
            "priority": self.priority,
            "risk_level": self.risk_level,
            "before": dict(self.before),
            "proposed": dict(self.proposed),
            "evidence": list(self.evidence),
            "reason_codes": list(self.reason_codes),
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "job_id": self.job_id,
            "claim_id": self.claim_id,
            "page_id": self.page_id,
            "note": self.note,
        }


class WikiMaintenanceService:
    """In-process durable-enough job/review store for Maintenance Center.

    Jobs and reviews are kept in memory keyed by process; optional persistence
    via operation_log when available. Safe when center disabled: no-ops that
    never block Raw Search.
    """

    def __init__(
        self,
        *,
        config: Any = None,
        policy_engine: MaintenancePolicyEngine | None = None,
        wiki_repository: Any = None,
        rebuild_service: Any = None,
        dependency_service: Any = None,
        feedback_service: Any = None,
        operation_log: Any = None,
        wiki_serving_gate: Any = None,
        db: Any = None,
        clock: Callable[[], str] | None = None,
    ):
        self._config = config
        self._policy = policy_engine or MaintenancePolicyEngine(config)
        self._repo = wiki_repository
        self._rebuild = rebuild_service
        self._dep = dependency_service
        self._feedback = feedback_service
        self._op_log = operation_log
        self._gate = wiki_serving_gate
        self._db = db
        self._clock = clock or _now
        self._store = None
        if db is not None:
            from src.repositories.maintenance_repo import MaintenanceRepository
            self._store = MaintenanceRepository(db)
        # Compatibility fallback for isolated callers without SQLite. The
        # application container always provides the durable repository.
        self._jobs: dict[str, MaintenanceJobRecord] = {}
        self._reviews: dict[str, ReviewItemRecord] = {}

    def _job_from_dict(self, value: dict[str, Any] | None) -> MaintenanceJobRecord | None:
        return MaintenanceJobRecord(**value) if value else None

    def _review_from_dict(self, value: dict[str, Any] | None) -> ReviewItemRecord | None:
        return ReviewItemRecord(**value) if value else None

    def _save_job(self, job: MaintenanceJobRecord) -> None:
        if self._store is not None:
            self._store.save_job(job.to_dict())
        else:
            self._jobs[job.job_id] = job

    def _get_job(self, job_id: str) -> MaintenanceJobRecord | None:
        if self._store is not None:
            return self._job_from_dict(self._store.get_job(job_id))
        return self._jobs.get(job_id)

    def _find_job_by_idempotency(self, key: str) -> MaintenanceJobRecord | None:
        if self._store is not None:
            return self._job_from_dict(self._store.find_job_by_idempotency(key))
        return next((job for job in self._jobs.values() if job.idempotency_key == key), None)

    def _list_jobs(self, status: str | None = None, limit: int = 50) -> list[MaintenanceJobRecord]:
        if self._store is not None:
            return [MaintenanceJobRecord(**row) for row in self._store.list_jobs(status, limit)]
        rows = list(self._jobs.values())
        if status:
            rows = [job for job in rows if job.status == status]
        return sorted(rows, key=lambda job: job.created_at, reverse=True)[:limit]

    def _save_review(self, review: ReviewItemRecord) -> None:
        if self._store is not None:
            self._store.save_review(review.to_dict())
        else:
            self._reviews[review.review_id] = review

    def _get_review(self, review_id: str) -> ReviewItemRecord | None:
        if self._store is not None:
            return self._review_from_dict(self._store.get_review(review_id))
        return self._reviews.get(review_id)

    def _list_reviews(self, status: str | None = "open", review_type: str | None = None, limit: int = 50) -> list[ReviewItemRecord]:
        if self._store is not None:
            return [ReviewItemRecord(**row) for row in self._store.list_reviews(status, review_type, limit)]
        rows = list(self._reviews.values())
        if status:
            rows = [review for review in rows if review.status == status]
        if review_type:
            rows = [review for review in rows if review.review_type == review_type]
        return sorted(rows, key=lambda review: review.created_at, reverse=True)[:limit]

    # ── Health ──────────────────────────────────────────────

    def health_snapshot(self) -> dict[str, Any]:
        """Spec §11.2 Health Snapshot (best-effort, never raises)."""
        snap: dict[str, Any] = {
            "snapshot_id": _id("health"),
            "captured_at": self._clock(),
            "maintenance_enabled": self._policy.maintenance_enabled(),
            "automation_level": self._policy.automation_level(),
            "knowledge_mode": self._policy.knowledge_mode(),
            "claims": {},
            "servable_claims": 0,
            "stale_evidence": 0,
            "open_reviews": 0,
            "failed_jobs": 0,
            "dead_letter_jobs": len(self._store.list_dead_letters()) if self._store else 0,
            "serving_fallback_hint": "raw_retrieval",
            "errors": [],
        }
        try:
            if self._repo is not None:
                claims = list(self._repo.list_claims() or [])
                by_status: dict[str, int] = {}
                stale_ev = 0
                for c in claims:
                    st = c.status.value if hasattr(c.status, "value") else str(c.status)
                    by_status[st] = by_status.get(st, 0) + 1
                    for ev in getattr(c, "evidence", []) or []:
                        if getattr(ev, "stale", False):
                            stale_ev += 1
                snap["claims"] = by_status
                snap["stale_evidence"] = stale_ev
                if self._gate is not None:
                    try:
                        pairs = self._gate.filter_servable(claims, include_disclose=False)
                        snap["servable_claims"] = len(pairs)
                    except Exception as e:  # noqa: BLE001
                        snap["errors"].append(f"gate:{e}")
        except Exception as e:  # noqa: BLE001
            snap["errors"].append(f"claims:{e}")

        jobs = self._list_jobs(limit=10000)
        snap["open_reviews"] = len(self._list_reviews(status="open", limit=10000)) + len(self._list_reviews(status="assigned", limit=10000))
        snap["failed_jobs"] = sum(1 for j in jobs if j.status in ("failed", "dead_letter"))
        snap["jobs_by_status"] = {}
        for j in jobs:
            snap["jobs_by_status"][j.status] = snap["jobs_by_status"].get(j.status, 0) + 1
        if self._store is not None:
            try:
                self._store.save_health_snapshot(snap)
            except Exception as e:  # noqa: BLE001
                snap["errors"].append(f"health_store:{e}")
        return snap

    # ── Source event → impact ───────────────────────────────

    def handle_source_event(
        self,
        knowledge_id: str,
        event_type: str,
        *,
        correlation_id: str | None = None,
        source_path: str = "",
        source_revision: str = "",
        human_confirmed: bool = False,
    ) -> dict[str, Any]:
        """Main automatic maintenance flow entry (Spec §11.3).

        1. Impact plan (dry-run)
        2. Policy classify
        3. R1 auto protective / R3 review / R4 block
        """
        if not self._policy.maintenance_enabled():
            return {
                "ok": True,
                "skipped": True,
                "reason": "maintenance_disabled",
                "raw_search_unaffected": True,
            }

        corr = correlation_id or _id("corr")
        event_id = _id("evt")
        revision = source_revision or "unknown"
        idem = f"source:{event_type}:{knowledge_id}:{revision}"
        existing = self._find_job_by_idempotency(idem)
        if existing is not None:
            return {
                "ok": True,
                "deduplicated": True,
                "job": existing.to_dict(),
                "correlation_id": existing.correlation_id,
            }

        # Step 1: impact plan
        plan_summary: dict[str, Any] = {"knowledge_id": knowledge_id, "event": event_type}
        affected_claims: list[str] = []
        affected_pages: list[str] = []
        if self._rebuild is not None and hasattr(self._rebuild, "plan_rebuild"):
            try:
                plan = self._rebuild.plan_rebuild(knowledge_id, event=event_type)
                plan_summary["stats"] = getattr(plan, "stats", {}) or {}
                for ci in getattr(plan, "claims", []) or []:
                    cid = getattr(ci, "claim_id", None) or (ci.get("claim_id") if isinstance(ci, dict) else None)
                    if cid:
                        affected_claims.append(str(cid))
                for pi in getattr(plan, "pages", []) or []:
                    pid = getattr(pi, "page_id", None) or (pi.get("page_id") if isinstance(pi, dict) else None)
                    if pid:
                        affected_pages.append(str(pid))
            except Exception as e:  # noqa: BLE001
                logger.warning("impact plan failed: %s", e)
                plan_summary["error"] = str(e)

        # Choose job type by event
        if event_type == "deleted":
            job_type = "protective_rebuild"
            risk = "R1"
        elif event_type in ("updated", "created"):
            job_type = "protective_rebuild"
            risk = "R1"
        else:
            job_type = "impact_plan"
            risk = "R0"

        decision = self._policy.evaluate(
            job_type, risk_level=risk, human_confirmed=human_confirmed,
        )
        if self._store is not None and not self._store.record_source_event({
            "event_id": event_id, "idempotency_key": idem, "event_type": event_type,
            "knowledge_id": knowledge_id, "source_revision": revision, "source_path": source_path,
            "correlation_id": corr, "created_at": self._clock(),
        }):
            existing = self._find_job_by_idempotency(idem)
            if existing is not None:
                return {"ok": True, "deduplicated": True, "job": existing.to_dict(), "correlation_id": existing.correlation_id}

        job = self._create_job(
            job_type=job_type,
            risk_level=decision.risk_level,
            idempotency_key=idem,
            correlation_id=corr,
            source_event_ids=[event_id],
            affected_claim_ids=affected_claims,
            affected_page_ids=affected_pages,
            decision=decision.decision,
            reason_codes=decision.reason_codes,
        )

        self._log_op(
            "maintenance_source_event",
            knowledge_id,
            {
                "event_id": event_id,
                "event_type": event_type,
                "source_path": source_path,
                "job_id": job.job_id,
                "decision": decision.to_dict(),
                "plan": plan_summary,
            },
            correlation_id=corr,
        )

        if decision.decision == DECIDE_BLOCK:
            job.status = "cancelled"
            job.finished_at = self._clock()
            job.result_summary = {"blocked": True, "plan": plan_summary}
            self._save_job(job)
            return {"ok": True, "job": job.to_dict(), "decision": decision.to_dict()}

        if decision.decision in (DECIDE_DRY_RUN,):
            job.status = "completed"
            job.finished_at = self._clock()
            job.result_summary = {"dry_run": True, "plan": plan_summary}
            self._save_job(job)
            return {"ok": True, "job": job.to_dict(), "decision": decision.to_dict(), "plan": plan_summary}

        if decision.decision == DECIDE_REVIEW:
            review = self._create_review(
                review_type="stale_rebuild" if event_type == "updated" else "correction",
                priority="P1",
                risk_level=decision.risk_level,
                before=plan_summary,
                proposed={"action": job_type, "knowledge_id": knowledge_id},
                reason_codes=decision.reason_codes,
                job_id=job.job_id,
            )
            job.status = "waiting_review"
            job.finished_at = self._clock()
            job.result_summary = {"review_id": review.review_id, "plan": plan_summary}
            self._save_job(job)
            return {
                "ok": True,
                "job": job.to_dict(),
                "decision": decision.to_dict(),
                "review": review.to_dict(),
            }

        # DECIDE_AUTO — R1 protective
        return self._execute_protective(job, knowledge_id, event_type, plan_summary, decision)

    def _execute_protective(
        self,
        job: MaintenanceJobRecord,
        knowledge_id: str,
        event_type: str,
        plan_summary: dict[str, Any],
        decision: PolicyDecision,
    ) -> dict[str, Any]:
        job.status = "running"
        job.started_at = self._clock()
        job.attempt += 1
        self._save_job(job)
        try:
            if self._rebuild is not None and hasattr(self._rebuild, "rebuild"):
                result = self._rebuild.rebuild(knowledge_id, event=event_type)
                committed = bool(getattr(result, "committed", False))
                job.result_summary = {
                    "committed": committed,
                    "plan": plan_summary,
                    "warnings": list(getattr(result, "warnings", []) or []),
                }
            elif self._rebuild is not None and hasattr(self._rebuild, "plan_rebuild"):
                # Dry-run only environment: still complete job as protective plan applied notionally
                job.result_summary = {
                    "committed": False,
                    "plan": plan_summary,
                    "note": "rebuild_not_available_plan_only",
                }
            else:
                job.result_summary = {"committed": False, "plan": plan_summary, "note": "no_rebuild_service"}

            job.status = "completed"
            job.finished_at = self._clock()
            self._save_job(job)
            self._log_op(
                "maintenance_protective",
                knowledge_id,
                {"job_id": job.job_id, "result": job.result_summary},
                correlation_id=job.correlation_id,
            )
            return {"ok": True, "job": job.to_dict(), "decision": decision.to_dict()}
        except Exception as e:  # noqa: BLE001
            logger.error("protective maintenance failed: %s", e)
            job.error = str(e)
            max_attempts = int(self._config_value("maintenance.jobs.max_attempts", 3))
            if job.attempt >= max_attempts:
                job.status = "dead_letter"
            else:
                job.status = "failed"
            job.finished_at = self._clock()
            self._save_job(job)
            # Spec: P0 protection failure → keep claims non-serving (rebuild service does this)
            self._log_op(
                "maintenance_protective_failed",
                knowledge_id,
                {"job_id": job.job_id, "error": str(e)},
                correlation_id=job.correlation_id,
            )
            return {"ok": False, "job": job.to_dict(), "error": str(e), "decision": decision.to_dict()}

    # ── Jobs / reviews API ──────────────────────────────────

    def list_jobs(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return [job.to_dict() for job in self._list_jobs(status=status, limit=limit)]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        j = self._get_job(job_id)
        return j.to_dict() if j else None

    def retry_job(self, job_id: str) -> dict[str, Any]:
        job = self._get_job(job_id)
        if not job:
            return {"ok": False, "error": "job_not_found"}
        if job.status not in ("failed", "dead_letter"):
            return {"ok": False, "error": "not_retryable", "status": job.status}
        kid = (job.result_summary.get("plan") or {}).get("knowledge_id") or ""
        event = (job.result_summary.get("plan") or {}).get("event") or "updated"
        decision = self._policy.evaluate(job.job_type, risk_level=job.risk_level)
        if decision.decision != DECIDE_AUTO:
            return {"ok": False, "error": "policy_blocks_retry", "decision": decision.to_dict()}
        return self._execute_protective(job, kid, event, job.result_summary.get("plan") or {}, decision)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self._get_job(job_id)
        if not job:
            return {"ok": False, "error": "job_not_found"}
        if job.status in ("completed", "cancelled", "dead_letter"):
            return {"ok": False, "error": "not_cancellable", "status": job.status}
        job.status = "cancelled"
        job.finished_at = self._clock()
        self._save_job(job)
        return {"ok": True, "job": job.to_dict()}

    def list_reviews(
        self,
        status: str | None = "open",
        review_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return [review.to_dict() for review in self._list_reviews(status, review_type, limit)]

    def get_review(self, review_id: str) -> dict[str, Any] | None:
        r = self._get_review(review_id)
        return r.to_dict() if r else None

    def resolve_review(
        self,
        review_id: str,
        action: str,
        *,
        operator: str = "user",
        note: str = "",
        correction: str | None = None,
        human_confirmed: bool = False,
    ) -> dict[str, Any]:
        """confirm / reject / correct / needs_review / defer — Spec §11.6."""
        review = self._get_review(review_id)
        if not review:
            return {"ok": False, "error": "review_not_found"}
        if review.status not in ("open", "assigned"):
            return {"ok": False, "error": "review_not_open", "status": review.status}

        action = (action or "").lower().strip()
        if action not in ("confirm", "reject", "correct", "needs_review", "defer", "approve", "approved"):
            return {"ok": False, "error": f"unknown_action:{action}"}

        # R4 publish/delete path
        if review.risk_level == "R4" and action in ("confirm", "approve", "approved"):
            decision = self._policy.evaluate(
                "publish", risk_level="R4", human_confirmed=human_confirmed,
            )
            if decision.decision != DECIDE_AUTO:
                return {"ok": False, "error": "policy_blocks", "decision": decision.to_dict()}

        feedback_result = None
        if review.claim_id and self._feedback is not None and action in (
            "confirm", "reject", "correct", "needs_review",
        ):
            try:
                feedback_result = self._feedback.apply(
                    review.claim_id,
                    "confirm" if action in ("approve", "approved") else action,
                    correction=correction,
                    operator=operator,
                    note=note,
                )
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"feedback_failed:{e}"}

        if action == "defer":
            review.status = "deferred"
        elif action in ("reject",):
            review.status = "rejected"
        elif action in ("needs_review",):
            review.status = "open"
        else:
            review.status = "approved"
        review.note = note
        self._save_review(review)
        self._log_op(
            "maintenance_review_resolve",
            review.claim_id or review.page_id or review_id,
            {
                "review_id": review_id,
                "action": action,
                "operator": operator,
                "feedback": getattr(feedback_result, "__dict__", feedback_result),
            },
        )
        return {
            "ok": True,
            "review": review.to_dict(),
            "feedback": (
                feedback_result.__dict__ if hasattr(feedback_result, "__dict__")
                else feedback_result
            ),
        }

    def propose_draft(
        self,
        *,
        claim_id: str = "",
        proposed: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        reason_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        """R3: create draft review item without publishing."""
        decision = self._policy.evaluate("claim_draft", risk_level="R3")
        if decision.decision == DECIDE_BLOCK:
            return {"ok": False, "decision": decision.to_dict()}
        job = self._create_job(
            job_type="claim_draft",
            risk_level="R3",
            decision=decision.decision,
            reason_codes=decision.reason_codes,
            affected_claim_ids=[claim_id] if claim_id else [],
        )
        review = self._create_review(
            review_type="new_claim" if not claim_id else "correction",
            priority="P2",
            risk_level="R3",
            proposed=proposed or {},
            evidence=evidence or [],
            reason_codes=reason_codes or decision.reason_codes,
            job_id=job.job_id,
            claim_id=claim_id,
        )
        job.status = "waiting_review"
        job.finished_at = self._clock()
        job.result_summary = {"review_id": review.review_id}
        self._save_job(job)
        return {"ok": True, "job": job.to_dict(), "review": review.to_dict(), "decision": decision.to_dict()}

    def evaluate_r4(self, job_type: str, *, human_confirmed: bool = False) -> dict[str, Any]:
        d = self._policy.evaluate(job_type, risk_level="R4", human_confirmed=human_confirmed)
        return d.to_dict()

    # ── internals ───────────────────────────────────────────

    def _create_job(self, **kwargs) -> MaintenanceJobRecord:
        job = MaintenanceJobRecord(
            job_id=_id("mjob"),
            created_at=self._clock(),
            status="pending",
            correlation_id=kwargs.pop("correlation_id", None) or _id("corr"),
            **kwargs,
        )
        self._save_job(job)
        return job

    def _create_review(self, **kwargs) -> ReviewItemRecord:
        review = ReviewItemRecord(
            review_id=_id("review"),
            created_at=self._clock(),
            **kwargs,
        )
        self._save_review(review)
        return review

    def _config_value(self, path: str, default: Any) -> Any:
        if self._config is None:
            return default
        if hasattr(self._config, "get") and not isinstance(self._config, dict):
            return self._config.get(path, default)
        value: Any = self._config
        for part in path.split("."):
            if not isinstance(value, dict):
                return default
            value = value.get(part)
        return default if value is None else value

    def _log_op(self, operation: str, target_id: str, payload: dict, *, correlation_id: str = "") -> None:
        if self._op_log is None:
            return
        try:
            self._op_log.log(
                operation=operation,
                target_type="maintenance",
                target_id=target_id or "unknown",
                after={**payload, "correlation_id": correlation_id},
            )
        except TypeError:
            try:
                self._op_log.log(operation, "maintenance", target_id or "unknown", after=payload)
            except Exception as e:  # noqa: BLE001
                logger.debug("operation_log failed: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.debug("operation_log failed: %s", e)
