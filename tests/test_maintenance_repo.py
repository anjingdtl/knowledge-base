"""Phase 3 durable Maintenance Control Plane regression tests."""
from __future__ import annotations

from src.repositories.maintenance_repo import MaintenanceRepository
from src.services.db import Database
from src.services.wiki_maintenance_service import WikiMaintenanceService


def _config(mode: str = "verified") -> dict:
    return {
        "knowledge_workflow": {"mode": mode},
        "maintenance": {
            "enabled": True,
            "center_enabled": True,
            "automation_level": "supervised",
            "policy": {"auto_protective_actions": True},
        },
    }


def test_repository_persists_jobs_reviews_and_dead_letters_across_instances(setup_db):
    db = Database._instance
    first = WikiMaintenanceService(config=_config("authoring"), db=db)
    proposed = first.propose_draft(claim_id="claim-1", proposed={"statement": "draft"})
    job_id = proposed["job"]["job_id"]
    review_id = proposed["review"]["review_id"]

    second = WikiMaintenanceService(config=_config("authoring"), db=db)
    assert second.get_job(job_id)["status"] == "waiting_review"
    assert second.get_review(review_id)["claim_id"] == "claim-1"


def test_revision_aware_event_idempotency_allows_new_revisions(setup_db):
    db = Database._instance
    service = WikiMaintenanceService(config=_config(), db=db)

    first = service.handle_source_event("doc-1", "updated", source_revision="hash-a")
    duplicate = service.handle_source_event("doc-1", "updated", source_revision="hash-a")
    next_revision = service.handle_source_event("doc-1", "updated", source_revision="hash-b")
    deleted = service.handle_source_event("doc-1", "deleted", source_revision="tombstone-b")
    deleted_duplicate = service.handle_source_event("doc-1", "deleted", source_revision="tombstone-b")

    assert first["ok"] is True
    assert duplicate["deduplicated"] is True
    assert next_revision["job"]["job_id"] != first["job"]["job_id"]
    assert deleted["job"]["job_id"] != next_revision["job"]["job_id"]
    assert deleted_duplicate["deduplicated"] is True


def test_database_lease_allows_only_one_worker_to_claim_a_job(setup_db):
    repo = MaintenanceRepository(Database._instance)
    repo.save_job({
        "job_id": "job-1", "job_type": "protective_rebuild", "risk_level": "R1",
        "status": "pending", "created_at": "2026-07-13T00:00:00Z",
        "idempotency_key": "source:updated:doc:hash", "attempt": 0,
    })

    first = repo.claim_next_job(worker_id="worker-a", now="2026-07-13T00:01:00Z", lease_until="2026-07-13T00:02:00Z")
    second = repo.claim_next_job(worker_id="worker-b", now="2026-07-13T00:01:00Z", lease_until="2026-07-13T00:02:00Z")

    assert first and first["worker_id"] == "worker-a"
    assert second is None
