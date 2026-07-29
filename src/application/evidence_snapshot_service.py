"""EvidenceSnapshotService — shared Search/Ask snapshot boundary (Phase 2).

Search and Ask must share one snapshot contract. This service is the
application-facing entry; concrete builders live in retrieval/.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SnapshotBuildRequest:
    query: str
    top_k: int
    threshold: float
    fetch_k: int | None = None


class EvidenceSnapshotService:
    """Build / load / register evidence snapshots via injected callables.

    MCP must not call PassageStore/Database private methods for snapshots;
    inject ports or builder functions instead.
    """

    def __init__(
        self,
        *,
        build_fn: Callable[..., dict[str, Any]],
        register_fn: Callable[..., str] | None = None,
        load_fn: Callable[..., tuple[dict[str, Any] | None, str, bool]] | None = None,
    ):
        self._build = build_fn
        self._register = register_fn
        self._load = load_fn

    def build(self, request: SnapshotBuildRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "query": request.query,
            "top_k": request.top_k,
            "threshold": request.threshold,
        }
        if request.fetch_k is not None:
            kwargs["fetch_k"] = request.fetch_k
        return self._build(**kwargs)

    def register(self, snapshot: dict[str, Any], *, query: str, top_k: int) -> str:
        if self._register is None:
            raise RuntimeError("snapshot register_fn not configured")
        return self._register(snapshot, query=query, top_k=top_k)

    def load(
        self,
        snapshot_id: str,
        *,
        query: str,
        top_k: int,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        if self._load is None:
            return None, "snapshot_load_unavailable", False
        return self._load(snapshot_id, query=query, top_k=top_k)
