"""Retrieval orchestration package (Phase-2 maintainability).

Internal layer: RawRetriever + VerifiedProvider + Policy + Orchestrator.
Public entry remains SearchService.execute / search.
"""
from src.retrieval.models import RawRetrievalResult, VerifiedServingResult

__all__ = [
    "RawRetrievalResult",
    "VerifiedServingResult",
]
