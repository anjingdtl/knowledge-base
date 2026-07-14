"""Raw evidence retriever boundary (Phase-2).

First iteration is an adapter: delegates to SearchService private pipeline
helpers. No algorithm move in the initial PR.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.retrieval.models import RawRetrievalResult

if TYPE_CHECKING:
    from src.services.search_service import SearchService

logger = logging.getLogger(__name__)


class RawRetriever:
    """Evidence retrieval capability boundary."""

    def __init__(self, search_service: "SearchService"):
        self._svc = search_service

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        include_legacy_wiki_fts: bool = True,
    ) -> RawRetrievalResult:
        """Run raw retrieval pipeline (rewrite → hybrid/fallback → rerank → diversity → package).

        When ``include_legacy_wiki_fts`` is True, prepends legacy wiki FTS hits
        the same way as ``_search_legacy_pipeline`` (evidence-only path).
        """
        return self._svc.run_raw_retrieval_adapter(
            query,
            top_k=top_k,
            include_legacy_wiki_fts=include_legacy_wiki_fts,
        )
