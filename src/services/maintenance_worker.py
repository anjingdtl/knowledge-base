"""Durable worker for queued Maintenance jobs."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, cast


class MaintenanceWorker:
    def __init__(self, service: Any, *, worker_id: str | None = None, lease_seconds: int = 60) -> None:
        self._service = service
        self._worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._lease_seconds = max(1, lease_seconds)

    def run_once(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        store = getattr(self._service, "_store", None)
        if store is not None:
            store.recover_expired_leases(now=now.isoformat())
        job = self._service.lease_next_job(
            worker_id=self._worker_id,
            now=now.isoformat(),
            lease_until=(now + timedelta(seconds=self._lease_seconds)).isoformat(),
        )
        if job is None:
            return {"ok": True, "idle": True}
        return cast(dict[str, Any], self._service.run_leased_job(job))
