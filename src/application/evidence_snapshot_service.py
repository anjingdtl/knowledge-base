"""EvidenceSnapshotService — shared Search/Ask snapshot boundary (Phase 2).

Search and Ask must share one snapshot contract. This service is the
application-facing entry; it owns:
- candidate retrieval (delegated to ``CandidateRetrievalService``);
- canonical snapshot construction (``src.retrieval.canonical_snapshot``);
- snapshot registration / loading (``src.retrieval.snapshot_registry``);
- revision token computation (PassageStore / Database).

MCP adapters must call this service rather than reaching into
PassageStore/Database private methods (ADR ``retrieval-answer-boundaries-v2``
§3 / §8). The service preserves the exact behaviour previously inlined in
``src/mcp/tools/retrieval.py``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.application.candidate_retrieval_service import CandidateRetrievalService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapshotBuildRequest:
    query: str
    top_k: int
    threshold: float
    fetch_k: int | None = None


class EvidenceSnapshotService:
    """Build / load / register evidence snapshots.

    The previous thin shell accepted injected callables; this real
    implementation owns the snapshot lifecycle via ``CandidateRetrievalService``
    + ``canonical_snapshot`` + ``snapshot_registry``. The container injects
    the concrete dependencies; tests inject fakes via the constructor.
    """

    def __init__(
        self,
        candidate_retrieval: CandidateRetrievalService,
        *,
        config: Any = None,
        container: Any = None,
    ):
        self._candidates = candidate_retrieval
        self._config = config
        self._container = container

    # ------------------------------------------------------------------ #
    # Build                                                              #
    # ------------------------------------------------------------------ #

    def build(self, request: SnapshotBuildRequest) -> dict[str, Any]:
        """Build the single canonical retrieval snapshot for search and ask."""
        from src.retrieval.candidate_pool import CandidatePoolPolicy
        from src.retrieval.canonical_snapshot import build_canonical_snapshot
        from src.services.query_rewrite import expand_query

        # ADR §5: same CandidatePoolPolicy as RawRetriever (4x/20 floor).
        if request.fetch_k is not None:
            fk = int(request.fetch_k)
        else:
            fk = CandidatePoolPolicy.from_request(request.top_k).fetch_k
        candidates = self._candidates.retrieve_candidates(request.query, fetch_k=fk)
        # SPEC v5: passage path — no list_blocks_fn; optional passage neighbors.
        return build_canonical_snapshot(
            request.query,
            candidates,
            threshold=request.threshold,
            top_k=request.top_k,
            expanded_queries=expand_query(request.query),
            list_blocks_fn=None,
            list_neighbor_passages_fn=self._candidates.neighbor_passages_for_snapshot,
            select_document_passages_fn=self._candidates.select_document_passages_for_snapshot,
            adjacent_window=1,
        )

    # ------------------------------------------------------------------ #
    # Register                                                           #
    # ------------------------------------------------------------------ #

    def register(self, snapshot: dict[str, Any], *, query: str, top_k: int) -> str:
        from src.retrieval.snapshot_registry import compute_config_hash, put_snapshot

        return put_snapshot(
            snapshot,
            query=query,
            top_k=top_k,
            config_hash=compute_config_hash(self._snapshot_config_bits()),
            index_revision=self._index_revision(),
            db_revision=self._db_revision(),
        )

    # ------------------------------------------------------------------ #
    # Load                                                               #
    # ------------------------------------------------------------------ #

    def load(
        self,
        snapshot_id: str,
        *,
        query: str,
        top_k: int,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        from src.retrieval.snapshot_registry import compute_config_hash, get_snapshot

        return get_snapshot(
            snapshot_id,
            query=query,
            top_k=top_k,
            config_hash=compute_config_hash(self._snapshot_config_bits()),
            index_revision=self._index_revision(),
            db_revision=self._db_revision(),
        )

    # ------------------------------------------------------------------ #
    # Revision / config helpers (preserved from MCP retrieval.py)         #
    # ------------------------------------------------------------------ #

    def _snapshot_config_bits(self) -> dict[str, Any]:
        cfg = self._config
        get = self._config_get
        return {
            "no_answer_threshold": get("rag.ask.no_answer_threshold", 0.35),
            "no_match_threshold": get("rag.search.no_match_threshold", 0.35),
            "max_sources": get("rag.ask.max_sources", 5),
            "retrieval_unit": "passage",
        }

    def _config_get(self, key: str, default: Any) -> Any:
        cfg = self._config
        if cfg is None:
            return default
        if isinstance(cfg, dict):
            # dotted lookup
            parts = key.split(".")
            cur: Any = cfg
            for p in parts:
                if isinstance(cur, dict):
                    cur = cur.get(p)
                else:
                    cur = None
            return cur if cur is not None else default
        getter = getattr(cfg, "get", None)
        if callable(getter):
            value = getter(key, None)
            return value if value is not None else default
        return default

    def _index_revision(self) -> str:
        try:
            from src.services.passage_store import PassageStore

            return PassageStore().revision_token()
        except Exception:
            return "passages:unknown"

    def _db_revision(self) -> str:
        try:
            container = self._container
            if container is None:
                return "db:unknown"
            db = getattr(container, "db", None)
            if db is None:
                return "db:unknown"
            conn = db.get_conn() if hasattr(db, "get_conn") else None
            if conn is None:
                return "db:unknown"
            row = conn.execute(
                "SELECT COUNT(*) FROM knowledge_items "
                "WHERE deleted_at IS NULL OR deleted_at = ''"
            ).fetchone()
            return f"knowledge:{row[0]}"
        except Exception:
            return "db:unknown"
