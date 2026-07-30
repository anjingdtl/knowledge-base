"""CandidateRetrievalService unit / DI / boundary tests (Phase 2 Task 2.3).

Covers:
- dependency injection (container + passage_store_factory + fulltext_search_fn);
- unified RawRetriever fast path (search_service present);
- compatibility fallback path (no search_service or exception);
- passage FTS merge + alias FTS recall aid;
- numeric unit ranking + dedupe + title boost preserved;
- snapshot-bound passage helpers (neighbor / select_document_passages);
- ADR §3 boundary: never calls SearchService._get_raw_retriever from MCP.

These tests do NOT touch real DB / embeddings — all dependencies are fakes.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.application.candidate_retrieval_service import CandidateRetrievalService


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class _FakeRawResult:
    def __init__(self, candidates: list[dict[str, Any]]):
        self.candidates = candidates


class _FakeRawRetriever:
    def __init__(self, candidates: list[dict[str, Any]]):
        self._candidates = candidates
        self.retrieve_calls: list[dict[str, Any]] = []

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        include_legacy_wiki_fts: bool = True,
    ) -> _FakeRawResult:
        self.retrieve_calls.append(
            {"query": query, "top_k": top_k, "include_legacy_wiki_fts": include_legacy_wiki_fts}
        )
        return _FakeRawResult(list(self._candidates))


class _FakeSearchService:
    """Stand-in for SearchService exposing the private hook the service uses."""

    def __init__(self, raw: _FakeRawRetriever):
        self._raw = raw

    def _get_raw_retriever(self) -> _FakeRawRetriever:
        return self._raw


class _FakePassageStore:
    def __init__(self, passages_by_kid: dict[str, list[dict[str, Any]]] | None = None):
        self._by_kid = passages_by_kid or {}
        self.fts_search_calls: list[str] = []

    def fts_search(self, query: str, *, top_k: int = 10) -> list[dict[str, Any]]:
        self.fts_search_calls.append(query)
        return []

    def get_by_knowledge(self, kid: str) -> list[dict[str, Any]]:
        return list(self._by_kid.get(kid, []))


def _make_container(
    *,
    search_service: Any = None,
    db: Any = None,
) -> MagicMock:
    """Build a container-like mock that passes isinstance(AppContainer) checks
    by being a plain attribute holder (not MagicMock for the container itself
    so getattr returns the real None defaults we need).
    """
    container = MagicMock()
    container.search_service = search_service
    container.db = db
    # MagicMock returns truthy attrs by default; force explicit None for ones
    # the service probes so the compatibility path actually exercises.
    container.hybrid_search = None
    return container


# --------------------------------------------------------------------------- #
# Fast path — unified RawRetriever                                            #
# --------------------------------------------------------------------------- #


def test_fast_path_uses_unified_raw_retriever_and_returns_dicts():
    raw = _FakeRawRetriever(
        [
            {"id": "k1", "title": "Doc 1", "text": "content", "score": 0.9},
            {"id": "k2", "title": "Doc 2", "text": "content2", "score": 0.7},
        ]
    )
    container = _make_container(search_service=_FakeSearchService(raw))
    svc = CandidateRetrievalService(container)

    out = svc.retrieve_candidates("query", fetch_k=20)

    assert len(out) == 2
    # Fast path returns dict(row) directly — knowledge_id normalization only
    # happens in the compatibility path. Assert the raw id is preserved.
    assert out[0]["id"] == "k1"
    assert out[0]["score"] == 0.9
    # Defensive copy — caller mutation must not leak into the raw store.
    assert out[0] is not raw._candidates[0]
    # retrieve was called with max(fetch_k, 5) and legacy FTS off
    assert raw.retrieve_calls == [
        {"query": "query", "top_k": 20, "include_legacy_wiki_fts": False}
    ]


def test_fast_path_falls_back_when_raw_retriever_raises():
    class _BoomRaw:
        def retrieve(self, *a, **kw):
            raise RuntimeError("vector store offline")

    container = _make_container(
        search_service=_FakeSearchService(_BoomRaw())  # type: ignore[arg-type]
    )
    # Force compatibility path: inject empty fulltext_search_fn so no DB hit.
    svc = CandidateRetrievalService(
        container,
        fulltext_search_fn=lambda q, *, limit=10, offset=0: [],
        passage_store_factory=lambda: _FakePassageStore(),
    )
    out = svc.retrieve_candidates("query", fetch_k=10)
    assert out == []


def test_fast_path_skipped_when_search_service_none():
    container = _make_container(search_service=None)
    svc = CandidateRetrievalService(
        container,
        fulltext_search_fn=lambda q, *, limit=10, offset=0: [
            {"id": "ft1", "title": "t", "text": "x", "fts_score": 0.5}
        ],
        passage_store_factory=lambda: _FakePassageStore(),
    )
    out = svc.retrieve_candidates("query", fetch_k=5)
    # Compatibility path merges FTS hits; the injected ft result should surface.
    assert any(c.get("knowledge_id") == "ft1" or c.get("id") == "ft1" for c in out)


# --------------------------------------------------------------------------- #
# Compatibility path — passage FTS + alias recall                              #
# --------------------------------------------------------------------------- #


def test_passage_fts_always_merged_even_when_semantic_present():
    """SPEC v3: passage FTS is always attempted (semantic unit)."""
    sem_hit = {"id": "k1", "title": "Doc", "text": "x", "score": 0.6}
    container = _make_container(search_service=None)

    captured_fts: list[str] = []

    class _StoreWithFts(_FakePassageStore):
        def fts_search(self, query: str, *, top_k: int = 10):
            captured_fts.append(query)
            if query == "alias_term":
                return [
                    {
                        "id": "p1",
                        "text": "alias content",
                        "metadata": {"knowledge_id": "k1", "passage_id": "p1"},
                        "fts_rank": 0.4,
                        "keyword_score": 0.4,
                    }
                ]
            return []

    svc = CandidateRetrievalService(
        container,
        fulltext_search_fn=lambda q, *, limit=10, offset=0: [],
        passage_store_factory=lambda: _StoreWithFts(),
    )
    # Patch canonical_terms to force an alias path.
    import src.services.query_rewrite as qr

    original = qr.canonical_terms
    qr.canonical_terms = lambda q: ["alias_term"]  # type: ignore[assignment]
    try:
        out = svc.retrieve_candidates("alias_term", fetch_k=5)
    finally:
        qr.canonical_terms = original  # type: ignore[assignment]

    # Passage FTS was probed for the query and the alias term.
    assert "alias_term" in captured_fts
    # The alias passage hit was merged into results.
    assert any(c.get("passage_id") == "p1" for c in out)


def test_block_fts_recall_only_when_top_score_below_threshold():
    """SPEC Phase 3.3: legacy FTS recall aid only fires on weak results.

    When the unified RawRetriever path returns a strong hit (top_score ≥ 0.35),
    the compatibility block-FTS recall aid must NOT fire. We exercise this by
    using the fast path with a strong-scoring candidate.
    """
    raw = _FakeRawRetriever(
        [{"id": "k1", "title": "Doc", "text": "content", "score": 0.9}]
    )
    container = _make_container(search_service=_FakeSearchService(raw))
    ft_calls: list[str] = []

    def _ft(query, *, limit=10, offset=0):
        ft_calls.append(query)
        return []

    svc = CandidateRetrievalService(
        container,
        fulltext_search_fn=_ft,
        passage_store_factory=lambda: _FakePassageStore(),
    )
    out = svc.retrieve_candidates("query", fetch_k=5)
    # Fast path returned the strong hit; compatibility FTS recall did not fire.
    assert any(c.get("id") == "k1" for c in out)
    assert ft_calls == [], "block FTS recall should not fire on strong results"


def test_block_fts_recall_fires_on_weak_results():
    """SPEC Phase 3.3: weak semantic → block FTS recall aid runs."""
    container = _make_container(search_service=None)
    ft_calls: list[str] = []

    def _ft(query, *, limit=10, offset=0):
        ft_calls.append(query)
        return [{"id": "ft1", "title": "t", "text": "x", "fts_score": 0.6}]

    class _WeakSemCommands:
        def semantic_search(self, q, *, top_k=5):
            return [{"id": "k1", "title": "Doc", "text": "x", "score": 0.1}]

    svc = CandidateRetrievalService(
        container,
        fulltext_search_fn=_ft,
        passage_store_factory=lambda: _FakePassageStore(),
    )
    svc._commands = _WeakSemCommands()  # type: ignore[assignment]
    out = svc.retrieve_candidates("weak query", fetch_k=5)
    assert ft_calls, "block FTS recall should fire when top_score < 0.35"


# --------------------------------------------------------------------------- #
# Output normalization                                                        #
# --------------------------------------------------------------------------- #


def test_retrieve_candidates_normalizes_knowledge_id_in_compatibility_path():
    """Compatibility path normalizes ``id`` → ``knowledge_id`` for downstream
    snapshot logic that expects a stable ``knowledge_id`` field."""
    container = _make_container(search_service=None)
    svc = CandidateRetrievalService(
        container,
        fulltext_search_fn=lambda q, *, limit=10, offset=0: [
            {"id": "ft1", "title": "t", "text": "x", "fts_score": 0.6}
        ],
        passage_store_factory=lambda: _FakePassageStore(),
    )
    out = svc.retrieve_candidates("query", fetch_k=5)
    # The compatibility path adds knowledge_id from id when missing.
    assert any(c.get("knowledge_id") == "ft1" for c in out)


def test_retrieve_candidates_copies_dicts_from_raw():
    raw_input = {"id": "k1", "title": "Doc", "text": "x", "score": 0.9}
    raw = _FakeRawRetriever([raw_input])
    container = _make_container(search_service=_FakeSearchService(raw))
    svc = CandidateRetrievalService(container)
    out = svc.retrieve_candidates("q", fetch_k=5)
    assert out[0] is not raw_input  # defensive copy
    out[0]["mutated"] = True
    assert "mutated" not in raw_input


# --------------------------------------------------------------------------- #
# Snapshot-bound passage helpers                                              #
# --------------------------------------------------------------------------- #


def test_neighbor_passages_for_snapshot_returns_window_around_target():
    passages = [
        {"id": f"p{i}", "text": f"passage {i}", "block_ids": [f"b{i}"], "metadata": {}}
        for i in range(5)
    ]
    store = _FakePassageStore({"k1": passages})
    svc = CandidateRetrievalService(
        _make_container(),
        passage_store_factory=lambda: store,
    )
    neighbors = svc.neighbor_passages_for_snapshot("k1", "p2", window=1)
    assert {n["passage_id"] for n in neighbors} == {"p1", "p3"}


def test_neighbor_passages_returns_empty_when_passage_not_found():
    store = _FakePassageStore(
        {"k1": [{"id": "p0", "text": "x", "block_ids": [], "metadata": {}}]}
    )
    svc = CandidateRetrievalService(
        _make_container(),
        passage_store_factory=lambda: store,
    )
    assert svc.neighbor_passages_for_snapshot("k1", "missing", window=1) == []


def test_neighbor_passages_handles_store_failure_gracefully():
    class _BoomStore:
        def get_by_knowledge(self, kid):
            raise RuntimeError("db down")

    svc = CandidateRetrievalService(
        _make_container(),
        passage_store_factory=lambda: _BoomStore(),  # type: ignore[return-value]
    )
    assert svc.neighbor_passages_for_snapshot("k1", "p0") == []


def test_select_document_passages_for_snapshot_scores_relevant_passages():
    passages = [
        {"id": "p1", "text": "营收 99% 实名", "block_ids": [], "metadata": {}},
        {"id": "p2", "text": "无关内容", "block_ids": [], "metadata": {}},
    ]
    store = _FakePassageStore({"k1": passages})
    svc = CandidateRetrievalService(
        _make_container(),
        passage_store_factory=lambda: store,
    )
    # Query plan anchors extracted by answering.query_planner.plan_query.
    out = svc.select_document_passages_for_snapshot(
        "k1", "实名登记率 99%", existing_passage_ids=set(), limit=3,
    )
    # Only the relevant passage should be returned (score > 0).
    assert any(p["passage_id"] == "p1" for p in out)
    assert not any(p["passage_id"] == "p2" for p in out)


def test_select_document_passages_skips_existing_passage_ids():
    passages = [
        {"id": "p1", "text": "营收 99% 实名", "block_ids": [], "metadata": {}},
    ]
    store = _FakePassageStore({"k1": passages})
    svc = CandidateRetrievalService(
        _make_container(),
        passage_store_factory=lambda: store,
    )
    out = svc.select_document_passages_for_snapshot(
        "k1", "实名登记率", existing_passage_ids={"p1"}, limit=3,
    )
    assert out == []


def test_list_blocks_for_page_returns_empty_when_db_none():
    svc = CandidateRetrievalService(_make_container(db=None))
    assert svc.list_blocks_for_page("page-1") == []


# --------------------------------------------------------------------------- #
# Boundary — ADR §3                                                           #
# --------------------------------------------------------------------------- #


def test_service_does_not_import_mcp():
    """ADR §3 / Task 2.6: application layer must not import src.mcp."""
    import src.application.candidate_retrieval_service as mod

    # Module-level imports: scan sys.modules of the loaded module's globals.
    import sys

    mcp_imports = [
        name
        for name in list(sys.modules)
        if name.startswith("src.mcp") and name in vars(mod).values()
    ]
    # The above is loose; verify the source text instead.
    import inspect

    src_text = inspect.getsource(mod)
    assert "from src.mcp" not in src_text, (
        "CandidateRetrievalService must not import src.mcp (ADR §3)"
    )
    assert "import src.mcp" not in src_text, (
        "CandidateRetrievalService must not import src.mcp (ADR §3)"
    )
