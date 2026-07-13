"""Phase 4 worker execution and durable lease recovery tests."""
from __future__ import annotations

from src.services.db import Database
from src.services.maintenance_worker import MaintenanceWorker
from src.services.wiki_maintenance_service import WikiMaintenanceService


class _Rebuild:
    def rebuild(self, knowledge_id: str, event: str):
        return type("Result", (), {"committed": True, "warnings": []})()


def _config() -> dict:
    return {
        "knowledge_workflow": {"mode": "verified"},
        "maintenance": {
            "enabled": True, "center_enabled": True, "automation_level": "supervised",
            "policy": {"auto_protective_actions": True}, "jobs": {"max_attempts": 2},
        },
    }


def test_worker_runs_queued_r1_job_to_completion(setup_db):
    service = WikiMaintenanceService(config=_config(), db=Database._instance, rebuild_service=_Rebuild())
    queued = service.handle_source_event("doc-1", "updated", source_revision="hash-1")

    result = MaintenanceWorker(service, worker_id="test-worker").run_once()

    assert queued["queued"] is True
    assert result["ok"] is True
    assert service.get_job(queued["job"]["job_id"])["status"] == "completed"


def test_expired_lease_is_recovered_by_next_worker(setup_db):
    service = WikiMaintenanceService(config=_config(), db=Database._instance, rebuild_service=_Rebuild())
    queued = service.handle_source_event("doc-1", "updated", source_revision="hash-1")
    leased = service.lease_next_job(worker_id="dead-worker", now="2026-01-01T00:00:00+00:00", lease_until="2026-01-01T00:00:01+00:00")
    assert leased and leased.status == "leased"

    result = MaintenanceWorker(service, worker_id="new-worker").run_once()

    assert result["ok"] is True
    assert service.get_job(queued["job"]["job_id"])["status"] == "completed"
