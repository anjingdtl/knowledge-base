"""EvidenceSnapshotService unit / DI / boundary tests (Phase 2 Task 2.3).

Covers:
- build delegates to CandidateRetrievalService + build_canonical_snapshot;
- register stores snapshot with config/index/db revision fingerprints;
- load round-trips via put_snapshot fingerprints;
- SnapshotBuildRequest is frozen;
- config bits feed compute_config_hash;
- index/db revision helpers degrade safely on missing deps;
- ADR §3 / §8 boundary: application layer must not import src.mcp.

These tests do NOT touch real DB / PassageStore — all deps are fakes.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.application.candidate_retrieval_service import CandidateRetrievalService
from src.application.evidence_snapshot_service import (
    EvidenceSnapshotService,
    SnapshotBuildRequest,
)
from src.retrieval.snapshot_registry import put_snapshot


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class _FakeCandidateRetrieval:
    """Fake CandidateRetrievalService — returns canned candidates."""

    def __init__(self, candidates: list[dict[str, Any]]):
        self._candidates = candidates
        self.retrieve_calls: list[dict[str, Any]] = []

    def retrieve_candidates(self, query: str, *, fetch_k: int) -> list[dict[str, Any]]:
        self.retrieve_calls.append({"query": query, "fetch_k": fetch_k})
        return [dict(c) for c in self._candidates]

    # Snapshot-bound helpers — return empty so build_canonical_snapshot still runs.
    def neighbor_passages_for_snapshot(self, kid: str, pid: str, window: int = 1):
        return []

    def select_document_passages_for_snapshot(
        self, kid: str, query: str, existing: set[str], limit: int = 3
    ):
        return []


class _FakeConfig:
    """Config stub returning deterministic values for known keys."""

    def __init__(self, mapping: dict[str, Any] | None = None):
        self._mapping = mapping or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping.get(key, default)


def _make_service(
    candidates: list[dict[str, Any]] | None = None,
    *,
    config: Any = None,
    container: Any = None,
) -> tuple[EvidenceSnapshotService, _FakeCandidateRetrieval]:
    fake_retrieval = _FakeCandidateRetrieval(candidates or [])
    svc = EvidenceSnapshotService(
        fake_retrieval,  # type: ignore[arg-type]
        config=config,
        container=container,
    )
    return svc, fake_retrieval


# --------------------------------------------------------------------------- #
# SnapshotBuildRequest                                                         #
# --------------------------------------------------------------------------- #


def test_snapshot_build_request_is_frozen():
    req = SnapshotBuildRequest(query="q", top_k=5, threshold=0.35)
    with pytest.raises(Exception):
        req.query = "changed"  # type: ignore[misc]


def test_snapshot_build_request_fetch_k_optional():
    req = SnapshotBuildRequest(query="q", top_k=5, threshold=0.35)
    assert req.fetch_k is None
    req2 = SnapshotBuildRequest(query="q", top_k=5, threshold=0.35, fetch_k=20)
    assert req2.fetch_k == 20


# --------------------------------------------------------------------------- #
# build                                                                        #
# --------------------------------------------------------------------------- #


def test_build_returns_canonical_snapshot_with_accepted_items():
    """Strong candidate → snapshot.accept=True, accepted_items populated."""
    candidates = [
        {
            "id": "k1",
            "knowledge_id": "k1",
            "title": "Doc",
            "text": "content with query term",
            "score": 0.9,
            "match_channel": "semantic",
        }
    ]
    svc, retrieval = _make_service(candidates)
    snapshot = svc.build(SnapshotBuildRequest(query="query term", top_k=5, threshold=0.35))
    assert snapshot.get("accept") is True
    assert snapshot.get("accepted_items")
    # Candidate retrieval was called with the request's fetch_k floor (CandidatePoolPolicy).
    assert retrieval.retrieve_calls == [{"query": "query term", "fetch_k": 20}]


def test_build_rejects_when_top_score_below_threshold():
    """Weak candidate → snapshot.accept=False, reason recorded."""
    candidates = [
        {
            "id": "k1",
            "knowledge_id": "k1",
            "title": "Doc",
            "text": "x",
            "score": 0.05,
        }
    ]
    svc, _ = _make_service(candidates)
    snapshot = svc.build(SnapshotBuildRequest(query="q", top_k=5, threshold=0.95))
    assert snapshot.get("accept") is False


def test_build_uses_explicit_fetch_k_when_provided():
    svc, retrieval = _make_service([])
    svc.build(
        SnapshotBuildRequest(query="q", top_k=5, threshold=0.35, fetch_k=42)
    )
    assert retrieval.retrieve_calls == [{"query": "q", "fetch_k": 42}]


def test_build_uses_policy_fetch_k_when_not_provided():
    """Without explicit fetch_k, falls back to CandidatePoolPolicy.from_request(top_k)."""
    svc, retrieval = _make_service([])
    svc.build(SnapshotBuildRequest(query="q", top_k=10, threshold=0.35))
    # CandidatePoolPolicy.from_request(10).fetch_k == max(10*4, 20) == 40
    assert retrieval.retrieve_calls == [{"query": "q", "fetch_k": 40}]


# --------------------------------------------------------------------------- #
# register / load round-trip                                                   #
# --------------------------------------------------------------------------- #


def test_register_then_load_round_trips():
    """A registered snapshot can be loaded back when fingerprints match."""
    candidates = [
        {
            "id": "k1",
            "knowledge_id": "k1",
            "title": "Doc",
            "text": "content with query term",
            "score": 0.9,
        }
    ]
    config = _FakeConfig(
        {
            "rag.ask.no_answer_threshold": 0.35,
            "rag.search.no_match_threshold": 0.35,
            "rag.ask.max_sources": 5,
        }
    )
    container = MagicMock()
    container.db = None  # forces db:unknown revision (stable)
    svc, _ = _make_service(candidates, config=config, container=container)

    snapshot = svc.build(SnapshotBuildRequest(query="q", top_k=5, threshold=0.35))
    snap_id = svc.register(snapshot, query="q", top_k=5)
    assert isinstance(snap_id, str) and len(snap_id) > 0

    loaded, reason, reused = svc.load(snap_id, query="q", top_k=5)
    assert loaded is not None
    assert reused is True
    assert reason == ""


def test_load_returns_miss_when_snapshot_id_missing():
    svc, _ = _make_service([])
    loaded, reason, reused = svc.load("", query="q", top_k=5)
    assert loaded is None
    assert reused is False
    assert reason == "snapshot_id_missing"


def test_load_returns_miss_when_not_found():
    svc, _ = _make_service([])
    loaded, reason, reused = svc.load("never-exists", query="q", top_k=5)
    assert loaded is None
    assert reused is False
    assert reason in ("snapshot_not_found_or_expired", "snapshot_id_missing")


def test_load_returns_miss_on_query_mismatch():
    candidates = [
        {
            "id": "k1",
            "knowledge_id": "k1",
            "title": "Doc",
            "text": "content with query term",
            "score": 0.9,
        }
    ]
    svc, _ = _make_service(candidates)
    snapshot = svc.build(SnapshotBuildRequest(query="original", top_k=5, threshold=0.35))
    snap_id = svc.register(snapshot, query="original", top_k=5)

    loaded, reason, reused = svc.load(snap_id, query="different", top_k=5)
    assert loaded is None
    assert reused is False
    assert reason == "query_mismatch"


def test_load_returns_miss_on_top_k_mismatch():
    candidates = [
        {
            "id": "k1",
            "knowledge_id": "k1",
            "title": "Doc",
            "text": "content with query term",
            "score": 0.9,
        }
    ]
    svc, _ = _make_service(candidates)
    snapshot = svc.build(SnapshotBuildRequest(query="q", top_k=5, threshold=0.35))
    snap_id = svc.register(snapshot, query="q", top_k=5)

    loaded, reason, reused = svc.load(snap_id, query="q", top_k=10)
    assert loaded is None
    assert reused is False
    assert reason == "top_k_mismatch"


# --------------------------------------------------------------------------- #
# Revision / config helpers                                                   #
# --------------------------------------------------------------------------- #


def test_index_revision_degrades_to_unknown_on_failure():
    svc, _ = _make_service([])
    # Default factory imports PassageStore which may fail without DB init.
    revision = svc._index_revision()
    assert isinstance(revision, str)
    # Either a real token or the explicit fallback.
    assert revision.startswith("passages:") or revision == "passages:unknown"


def test_db_revision_uses_container_db_count_when_available():
    """When container.db exposes a connection, db_revision returns knowledge:N."""
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = (42,)
    fake_db = MagicMock()
    fake_db.get_conn.return_value = fake_conn
    container = MagicMock()
    container.db = fake_db

    svc, _ = _make_service(container=container)
    revision = svc._db_revision()
    assert revision == "knowledge:42"
    fake_conn.execute.assert_called_once()
    # The query must filter out soft-deleted knowledge items.
    sql = fake_conn.execute.call_args[0][0]
    assert "deleted_at" in sql


def test_db_revision_degrades_when_container_none():
    svc, _ = _make_service(container=None)
    assert svc._db_revision() == "db:unknown"


def test_db_revision_degrades_when_db_none():
    container = MagicMock()
    container.db = None
    svc, _ = _make_service(container=container)
    assert svc._db_revision() == "db:unknown"


def test_snapshot_config_bits_include_required_keys():
    """ADR §3.3 / Task 2.0.2b: config bits feed the snapshot fingerprint and
    must include the no-answer/no-match thresholds, max_sources and the
    retrieval unit."""
    svc, _ = _make_service(config=_FakeConfig({"rag.ask.max_sources": 7}))
    bits = svc._snapshot_config_bits()
    assert "no_answer_threshold" in bits
    assert "no_match_threshold" in bits
    assert "max_sources" in bits
    assert bits["max_sources"] == 7
    assert bits["retrieval_unit"] == "passage"


def test_config_get_supports_dict_and_object_configs():
    """The service must accept both dict configs and Config-like objects."""
    dict_svc, _ = _make_service(
        config={"rag": {"ask": {"no_answer_threshold": 0.42}}}
    )
    assert dict_svc._config_get("rag.ask.no_answer_threshold", 0.35) == 0.42

    obj_svc, _ = _make_service(config=_FakeConfig({"rag.ask.no_answer_threshold": 0.5}))
    assert obj_svc._config_get("rag.ask.no_answer_threshold", 0.35) == 0.5


def test_config_get_returns_default_when_config_none():
    svc, _ = _make_service(config=None)
    assert svc._config_get("any.key", "fallback") == "fallback"


# --------------------------------------------------------------------------- #
# Revision consistency — fingerprint stability                                #
# --------------------------------------------------------------------------- #


def test_revision_fingerprints_are_stable_across_calls_when_unchanged():
    """Same config + same db state → same revision tokens → same fingerprint."""
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = (10,)
    fake_db = MagicMock()
    fake_db.get_conn.return_value = fake_conn
    container = MagicMock()
    container.db = fake_db
    config = _FakeConfig(
        {
            "rag.ask.no_answer_threshold": 0.35,
            "rag.search.no_match_threshold": 0.35,
            "rag.ask.max_sources": 5,
        }
    )
    svc, _ = _make_service(config=config, container=container)
    bits1 = svc._snapshot_config_bits()
    bits2 = svc._snapshot_config_bits()
    assert bits1 == bits2
    assert svc._db_revision() == svc._db_revision()


def test_revision_fingerprint_changes_when_db_count_changes():
    """A change in the underlying knowledge count MUST change db_revision so
    stale snapshots are rejected on resume (Task 2.0.3 freeze invariant)."""
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = (10,)
    fake_db = MagicMock()
    fake_db.get_conn.return_value = fake_conn
    container = MagicMock()
    container.db = fake_db
    svc, _ = _make_service(container=container)
    rev1 = svc._db_revision()
    assert rev1 == "knowledge:10"

    fake_conn.execute.return_value.fetchone.return_value = (11,)
    rev2 = svc._db_revision()
    assert rev2 == "knowledge:11"
    assert rev1 != rev2


# --------------------------------------------------------------------------- #
# Boundary — ADR §3 / §8                                                      #
# --------------------------------------------------------------------------- #


def test_service_does_not_import_mcp():
    """ADR §3 / Task 2.6: application layer must not import src.mcp."""
    import inspect

    import src.application.evidence_snapshot_service as mod

    src_text = inspect.getsource(mod)
    assert "from src.mcp" not in src_text, (
        "EvidenceSnapshotService must not import src.mcp (ADR §3)"
    )
    assert "import src.mcp" not in src_text, (
        "EvidenceSnapshotService must not import src.mcp (ADR §3)"
    )


def test_service_does_not_call_passage_store_private_conn():
    """ADR §8: must not call PassageStore._get_conn() / store._get_conn()."""
    import inspect

    import src.application.evidence_snapshot_service as mod

    src_text = inspect.getsource(mod)
    assert "PassageStore._get_conn" not in src_text
    assert "store._get_conn(" not in src_text
