"""SearchUseCase — application entry for semantic search (Phase 2).

Does not build MCP Envelopes. Transport adapters call this use case and
map SearchExecution to their public payload shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.retrieval.candidate_pool import CandidatePoolPolicy


@dataclass(frozen=True)
class SearchRequest:
    query: str
    top_k: int = 5
    query_spec: Any = None


class SearchUseCase:
    """Thin application facade over SearchService / RetrievalOrchestrator.

    Phase 2 keeps behaviour identical to SearchService.execute while
    establishing the UseCase boundary for MCP/API adapters.
    """

    def __init__(self, search_service: Any):
        self._search = search_service

    def execute(self, request: SearchRequest):
        policy = CandidatePoolPolicy.from_request(request.top_k)
        execution = self._search.execute(
            request.query,
            top_k=policy.public_top_k,
            query_spec=request.query_spec,
        )
        # Ensure public result count never exceeds public_top_k even if a
        # future pipeline regresses packaging.
        results = list(execution.results)[: policy.public_top_k]
        if len(results) != len(execution.results):
            from src.models.search_execution import SearchExecution

            return SearchExecution(
                results=tuple(results),
                trace=dict(execution.trace or {}),
                disclose_claims=tuple(execution.disclose_claims),
                conflicts=tuple(execution.conflicts),
                fallbacks=tuple(execution.fallbacks),
                warnings=tuple(execution.warnings),
            )
        return execution

    def search(self, query: str, *, top_k: int = 5, query_spec=None) -> list[dict]:
        execution = self.execute(
            SearchRequest(query=query, top_k=top_k, query_spec=query_spec)
        )
        return list(execution.results)
