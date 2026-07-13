"""Leased periodic Maintenance tasks (validation, parity and quality audits)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable


class MaintenanceScheduler:
    def __init__(self, repository: Any, tasks: dict[str, Callable[[], Any]], *, lease_seconds: int = 300) -> None:
        self._repository = repository
        self._tasks = tasks
        self._lease_seconds = max(1, lease_seconds)

    def run_once(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        lease_until = (now + timedelta(seconds=self._lease_seconds)).isoformat()
        completed: list[str] = []
        errors: dict[str, str] = {}
        for name, task in self._tasks.items():
            if not self._repository.claim_schedule(name, now=now.isoformat(), lease_until=lease_until):
                continue
            try:
                task()
                completed.append(name)
            except Exception as exc:  # noqa: BLE001
                errors[name] = str(exc)
        return {"completed": completed, "errors": errors}
