"""Evidence-only retrieval policy: RawRetriever → SearchExecution."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.models.search_execution import SearchExecution
from src.retrieval.packaging import build_evidence_only_execution

if TYPE_CHECKING:
    from src.retrieval.raw_retriever import RawRetriever
    from src.services.search_service import SearchService


class EvidenceOnlyPolicy:
    """Raw evidence path (legacy pipeline semantics, including legacy wiki FTS)."""

    def __init__(
        self,
        search_service: "SearchService | None" = None,
        *,
        raw_retriever: "RawRetriever | None" = None,
    ):
        self._svc = search_service
        if raw_retriever is not None:
            self._raw = raw_retriever
        elif search_service is not None:
            self._raw = search_service._get_raw_retriever()
        else:
            raise TypeError("EvidenceOnlyPolicy requires raw_retriever or search_service")

    def execute(
        self,
        query: str,
        *,
        top_k: int = 5,
        query_spec: Any = None,
        deadline: float | None = None,
    ) -> SearchExecution:
        _ = deadline
        if query_spec is not None and self._svc is not None:
            return self._svc.execute_query_spec(query_spec, top_k=top_k)
        raw = self._raw.retrieve(
            query, top_k=top_k, include_legacy_wiki_fts=True,
        )
        return build_evidence_only_execution(raw)
