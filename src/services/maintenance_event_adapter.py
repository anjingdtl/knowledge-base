"""Single non-blocking Source Event entrypoint for Maintenance."""
from __future__ import annotations

import logging
from typing import Any, cast

logger = logging.getLogger(__name__)


class MaintenanceEventAdapter:
    """Translate indexer/watcher events into durable maintenance events.

    Raw ingestion completes before this adapter is invoked. Any enqueue error
    is deliberately a warning and can never make indexing or Raw retrieval fail.
    """

    def __init__(self, maintenance_service: Any) -> None:
        self._service = maintenance_service

    def enqueue_source_event(
        self, knowledge_id: str, event_type: str, *, source_revision: str = "", source_path: str = "",
    ) -> dict[str, Any]:
        try:
            return cast(dict[str, Any], self._service.handle_source_event(
                knowledge_id, event_type, source_revision=source_revision, source_path=source_path,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("maintenance enqueue failed after raw index for %s: %s", knowledge_id, exc)
            return {"ok": False, "warning": str(exc), "raw_index_unaffected": True}
