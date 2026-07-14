"""RetrievalOrchestrator — single entry for Evidence-only / Verified search.

Does not generate Answer or MCP envelopes. Returns SearchExecution only.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from src.models.search_execution import SearchExecution
from src.retrieval.policies.evidence_only import EvidenceOnlyPolicy
from src.retrieval.policies.verified import VerifiedPolicy
from src.retrieval.shadow_comparator import compare_executions, log_shadow_diff

if TYPE_CHECKING:
    from src.services.search_service import SearchService

logger = logging.getLogger(__name__)

_VALID_MODES = frozenset({"legacy", "shadow", "unified"})

# WP1-T6: unified is the formal default; legacy remains for rollback.
_DEFAULT_MODE = "unified"


def resolve_orchestrator_mode(config: Any) -> str:
    """Read retrieval.orchestrator from Config object or nested dict.

    Default: unified (maintainability closure WP1-T6).
    """
    raw: Any = None
    if config is None:
        return _DEFAULT_MODE
    if isinstance(config, dict):
        retrieval = config.get("retrieval") or {}
        if isinstance(retrieval, dict):
            raw = retrieval.get("orchestrator")
        if raw is None:
            raw = config.get("retrieval.orchestrator")
    else:
        getter = getattr(config, "get", None)
        if callable(getter):
            raw = getter("retrieval.orchestrator", None)
            if raw is None:
                block = getter("retrieval", None)
                if isinstance(block, dict):
                    raw = block.get("orchestrator")
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_MODE
    mode = str(raw).strip().lower()
    if mode not in _VALID_MODES:
        logger.warning(
            "Unknown retrieval.orchestrator=%r; falling back to %s",
            raw,
            _DEFAULT_MODE,
        )
        return _DEFAULT_MODE
    return mode


class RetrievalOrchestrator:
    """Select policy by knowledge mode; support legacy/shadow/unified switch."""

    def __init__(self, search_service: "SearchService", config: Any = None):
        self._svc = search_service
        self._config = config if config is not None else search_service._config

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        query_spec: Any = None,
        deadline: float | None = None,
    ) -> SearchExecution:
        mode = resolve_orchestrator_mode(self._config)

        if mode == "legacy":
            return self._svc.execute_primary_legacy(
                query, top_k=top_k, query_spec=query_spec,
            )

        if mode == "shadow":
            t0 = time.monotonic()
            primary = self._svc.execute_primary_legacy(
                query, top_k=top_k, query_spec=query_spec,
            )
            p_ms = (time.monotonic() - t0) * 1000
            exceptions: list[str] = []
            candidate: SearchExecution | None = None
            c_ms = 0.0
            try:
                t1 = time.monotonic()
                candidate = self._execute_unified(
                    query, top_k=top_k, query_spec=query_spec, deadline=deadline,
                )
                c_ms = (time.monotonic() - t1) * 1000
            except Exception as e:  # noqa: BLE001
                exceptions.append(type(e).__name__)
                logger.warning("retrieval shadow unified path failed: %s", e)

            if candidate is not None:
                diff = compare_executions(
                    primary,
                    candidate,
                    top_k=top_k,
                    latency_ms_primary=p_ms,
                    latency_ms_candidate=c_ms,
                    exception_types=tuple(exceptions),
                )
                log_shadow_diff(diff, query_preview=query)
            elif exceptions:
                logger.info(
                    "retrieval_shadow query=%r primary_only exceptions=%s",
                    (query or "")[:80],
                    exceptions,
                )
            return primary

        # unified
        return self._execute_unified(
            query, top_k=top_k, query_spec=query_spec, deadline=deadline,
        )

    def _execute_unified(
        self,
        query: str,
        *,
        top_k: int = 5,
        query_spec: Any = None,
        deadline: float | None = None,
    ) -> SearchExecution:
        if query_spec is not None:
            spec_exec = self._svc.execute_query_spec(query_spec, top_k=top_k)
            if spec_exec.results:
                return spec_exec
            # empty spec → fall through to normal retrieval (legacy parity)

        if self._svc._should_use_verified_hybrid():
            return VerifiedPolicy(
                self._svc,
                fusion=self._svc._get_verified_fusion(),
            ).execute(
                query, top_k=top_k, query_spec=None, deadline=deadline,
            )
        return EvidenceOnlyPolicy(
            self._svc,
            raw_retriever=self._svc._get_raw_retriever(),
        ).execute(
            query, top_k=top_k, query_spec=None, deadline=deadline,
        )
