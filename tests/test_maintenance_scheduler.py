"""Phase 4 schedule leases prevent duplicate periodic execution."""
from __future__ import annotations

from src.repositories.maintenance_repo import MaintenanceRepository
from src.services.db import Database
from src.services.maintenance_scheduler import MaintenanceScheduler


def test_two_schedulers_run_one_leased_periodic_task(setup_db):
    calls: list[str] = []
    repo = MaintenanceRepository(Database._instance)
    first = MaintenanceScheduler(repo, {"daily_validation": lambda: calls.append("first")})
    second = MaintenanceScheduler(repo, {"daily_validation": lambda: calls.append("second")})

    first.run_once()
    second.run_once()

    assert len(calls) == 1
