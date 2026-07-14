"""RetrievalOrchestrator — single entry for Evidence-only / Verified search.

Does not generate Answer or MCP envelopes. Returns SearchExecution only.

WP5: only the unified path remains. Config values ``legacy`` / ``shadow`` are
accepted as deprecated aliases that resolve to unified (with a warning).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.models.search_execution import SearchExecution
from src.retrieval.policies.evidence_only import EvidenceOnlyPolicy
from src.retrieval.policies.verified import VerifiedPolicy

if TYPE_CHECKING:
    from src.services.search_service import SearchService

logger = logging.getLogger(__name__)

_VALID_MODES = frozenset({"unified", "legacy", "shadow"})
_DEFAULT_MODE = "unified"
_DEPRECATED_ALIASES = frozenset({"legacy", "shadow"})


def resolve_orchestrator_mode(config: Any) -> str:
    """Read retrieval.orchestrator from Config object or nested dict.

    Default: unified. Deprecated ``legacy`` / ``shadow`` aliases map to unified.
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
    if mode in _DEPRECATED_ALIASES:
        logger.warning(
            "retrieval.orchestrator=%s is removed in v1.10.0; using unified",
            mode,
        )
        return _DEFAULT_MODE
    return mode


class RetrievalOrchestrator:
    """Select policy by knowledge mode; unified is the only execution path."""

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
        # resolve for side-effect warnings / config validation
        resolve_orchestrator_mode(self._config)
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
            # empty spec → fall through to normal retrieval

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
