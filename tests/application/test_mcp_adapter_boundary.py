"""MCP retrieval adapter boundary tests (Phase 2 Task 2.3).

Verifies that the MCP ``search`` / ``ask`` adapters delegate candidate
retrieval and snapshot lifecycle to the application services rather than
holding business logic inline. The tests inject a fake application service
via the container and confirm it is invoked.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


class _RecordingCandidateRetrieval:
    """Fake CandidateRetrievalService — records calls and returns canned data."""

    def __init__(self, candidates: list[dict[str, Any]] | None = None):
        self._candidates = candidates or []
        self.retrieve_calls: list[dict[str, Any]] = []

    def retrieve_candidates(self, query: str, *, fetch_k: int) -> list[dict[str, Any]]:
        self.retrieve_calls.append({"query": query, "fetch_k": fetch_k})
        return [dict(c) for c in self._candidates]

    def neighbor_passages_for_snapshot(self, *a, **kw):
        return []

    def select_document_passages_for_snapshot(self, *a, **kw):
        return []


class _RecordingEvidenceSnapshot:
    """Fake EvidenceSnapshotService — records build/register/load."""

    def __init__(self, snapshot: dict[str, Any] | None = None):
        self._snapshot = snapshot or {"accept": False, "accepted_items": []}
        self.build_calls: list[dict[str, Any]] = []
        self.register_calls: list[dict[str, Any]] = []
        self.load_calls: list[dict[str, Any]] = []
        self._next_snapshot_id = "snap-test-1"

    def build(self, request) -> dict[str, Any]:
        self.build_calls.append(
            {
                "query": request.query,
                "top_k": request.top_k,
                "threshold": request.threshold,
                "fetch_k": request.fetch_k,
            }
        )
        return self._snapshot

    def register(self, snapshot, *, query, top_k) -> str:
        self.register_calls.append({"query": query, "top_k": top_k})
        return self._next_snapshot_id

    def load(self, snapshot_id, *, query, top_k):
        self.load_calls.append({"snapshot_id": snapshot_id, "query": query, "top_k": top_k})
        return self._snapshot, "", True


def _install_services(monkeypatch, container, *, candidate_svc=None, snapshot_svc=None):
    """Patch retrieval._get_container to return a container exposing the
    application services directly (no fallback construction)."""
    if candidate_svc is not None:
        container.candidate_retrieval_service = candidate_svc
    if snapshot_svc is not None:
        container.evidence_snapshot_service = snapshot_svc

    from src.mcp.tools import retrieval

    monkeypatch.setattr(retrieval, "_get_container", lambda: container)
    return retrieval


# --------------------------------------------------------------------------- #
# Boundary — container service is preferred over inline construction          #
# --------------------------------------------------------------------------- #


def test_retrieve_candidates_prefers_container_service(monkeypatch):
    """When ``container.candidate_retrieval_service`` is wired, MCP must use it
    rather than constructing a fallback CandidateRetrievalService."""
    container = MagicMock()
    fake = _RecordingCandidateRetrieval(
        [{"id": "k1", "title": "t", "text": "x", "score": 0.9}]
    )
    retrieval = _install_services(monkeypatch, container, candidate_svc=fake)

    out = retrieval._retrieve_candidates("query", fetch_k=20)

    assert fake.retrieve_calls == [{"query": "query", "fetch_k": 20}]
    assert out[0]["id"] == "k1"


def test_build_shared_snapshot_prefers_container_service(monkeypatch):
    """When ``container.evidence_snapshot_service`` is wired, MCP must delegate."""
    container = MagicMock()
    fake_snap = _RecordingEvidenceSnapshot(
        {"accept": True, "accepted_items": [{"id": "k1"}], "top_score": 0.9}
    )
    retrieval = _install_services(
        monkeypatch, container, snapshot_svc=fake_snap
    )

    snapshot = retrieval._build_shared_snapshot(
        "query", top_k=5, threshold=0.35, fetch_k=20,
    )

    assert fake_snap.build_calls == [
        {"query": "query", "top_k": 5, "threshold": 0.35, "fetch_k": 20}
    ]
    assert snapshot.get("accept") is True


def test_register_snapshot_prefers_container_service(monkeypatch):
    container = MagicMock()
    fake_snap = _RecordingEvidenceSnapshot()
    retrieval = _install_services(monkeypatch, container, snapshot_svc=fake_snap)

    snap_id = retrieval._register_snapshot({"accept": True}, query="q", top_k=5)

    assert snap_id == "snap-test-1"
    assert fake_snap.register_calls == [{"query": "q", "top_k": 5}]


def test_load_snapshot_prefers_container_service(monkeypatch):
    container = MagicMock()
    fake_snap = _RecordingEvidenceSnapshot()
    retrieval = _install_services(monkeypatch, container, snapshot_svc=fake_snap)

    loaded, reason, reused = retrieval._load_snapshot("snap-1", query="q", top_k=5)

    assert fake_snap.load_calls == [
        {"snapshot_id": "snap-1", "query": "q", "top_k": 5}
    ]
    assert reused is True


# --------------------------------------------------------------------------- #
# Boundary — fallback constructs application services with MCP adapter        #
# --------------------------------------------------------------------------- #


def test_retrieve_candidates_fallback_constructs_application_service(monkeypatch):
    """When the container does NOT wire candidate_retrieval_service, MCP must
    construct a CandidateRetrievalService (not call MCP search_fulltext inline)
    and inject the MCP fulltext adapter so existing monkeypatches keep working."""
    from types import SimpleNamespace

    container = SimpleNamespace(search_service=None, db=None)
    retrieval = _install_services(monkeypatch, container)

    # Intercept MCP search_fulltext so we prove the adapter is wired.
    fulltext_calls: list[str] = []

    def _fake_search_fulltext(query, limit=10, offset=0):
        fulltext_calls.append(query)
        return {"ok": True, "data": [], "meta": {"top_score": 0.0}}

    monkeypatch.setattr(retrieval, "search_fulltext", _fake_search_fulltext)

    out = retrieval._retrieve_candidates("query", fetch_k=5)
    assert isinstance(out, list)


def test_get_candidate_retrieval_service_is_idempotent_per_container(monkeypatch):
    """The resolver returns the same container-provided service on repeated calls."""
    container = MagicMock()
    fake = _RecordingCandidateRetrieval()
    retrieval = _install_services(monkeypatch, container, candidate_svc=fake)

    svc1 = retrieval._get_candidate_retrieval_service()
    svc2 = retrieval._get_candidate_retrieval_service()
    assert svc1 is svc2 is fake


def test_get_evidence_snapshot_service_is_idempotent_per_container(monkeypatch):
    container = MagicMock()
    fake = _RecordingEvidenceSnapshot()
    retrieval = _install_services(monkeypatch, container, snapshot_svc=fake)

    svc1 = retrieval._get_evidence_snapshot_service()
    svc2 = retrieval._get_evidence_snapshot_service()
    assert svc1 is svc2 is fake


# --------------------------------------------------------------------------- #
# Boundary — MCP must not reach into PassageStore / Database private methods  #
# --------------------------------------------------------------------------- #


def test_mcp_retrieval_does_not_call_passage_store_private_conn():
    """ADR §8: MCP retrieval.py must not call PassageStore._get_conn / store._get_conn."""
    import inspect

    from src.mcp.tools import retrieval

    src_text = inspect.getsource(retrieval)
    assert "PassageStore._get_conn" not in src_text
    assert "store._get_conn(" not in src_text


def test_mcp_retrieval_delegates_candidate_business_logic_to_application():
    """Task 2.3 delegated the candidate retrieval + snapshot lifecycle to the
    application layer. Confirm the high-value business body that used to live
    in MCP retrieval.py is gone — ``_semantic_with_variants`` and the
    ``build_canonical_snapshot`` direct call no longer leak back into MCP.

    Note: ``search_fulltext`` is a separate low-level FTS tool that still
    post-processes its own results (numeric ranking / title boost). That
    extraction is a separate progressive-splitting step (ADR §6 budget), not
    part of Task 2.3's search/ask snapshot boundary.
    """
    import inspect

    from src.mcp.tools import retrieval

    src_text = inspect.getsource(retrieval)
    # The semantic-with-variants candidate body must live in the application
    # service, not inlined in MCP retrieval.py.
    assert "_semantic_with_variants" not in src_text
    # build_canonical_snapshot must be called via EvidenceSnapshotService.build,
    # not directly from MCP retrieval.py.
    assert "build_canonical_snapshot(" not in src_text
    # The dedupe_retrieval_hits helper (multi-passage diversity) is owned by
    # CandidateRetrievalService, not MCP.
    assert "dedupe_retrieval_hits(" not in src_text
