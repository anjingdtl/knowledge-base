"""Application-layer ports for Search/Ask (Phase 2 Task 2.3).

Implementations are injected by the container; tests use fakes.
Transport adapters (MCP/REST/GUI) must not reach past UseCases into
private retriever/DB methods.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CandidateRetriever(Protocol):
    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        include_legacy_wiki_fts: bool = True,
    ) -> Any:
        """Return raw retrieval result (candidates + trace)."""
        ...


@runtime_checkable
class Reranker(Protocol):
    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        ...


@runtime_checkable
class EvidenceRepository(Protocol):
    def get_passages(
        self,
        passage_ids: list[str],
    ) -> list[dict[str, Any]]:
        ...


@runtime_checkable
class SnapshotRepository(Protocol):
    def put(
        self,
        snapshot: dict[str, Any],
        *,
        query: str,
        top_k: int,
        config_hash: str,
        index_revision: str,
        db_revision: str,
    ) -> str:
        ...

    def get(
        self,
        snapshot_id: str,
        *,
        query: str,
        top_k: int,
        config_hash: str,
        index_revision: str,
        db_revision: str,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        ...


@runtime_checkable
class FactExtractor(Protocol):
    def extract(
        self,
        query: str,
        evidence: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ...


@runtime_checkable
class AnswerRenderer(Protocol):
    def render(self, plan: dict[str, Any]) -> dict[str, Any]:
        ...


@runtime_checkable
class AnswerValidator(Protocol):
    def validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...
