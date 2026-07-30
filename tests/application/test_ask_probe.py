"""AskProbe + MCP ``_do_ask`` boundary tests (Phase 2 Task 2.4).

Verifies that the pre-LLM evidence probe (live-external short-circuit +
snapshot load/build/reuse + gate rejection) is owned by
:class:`AskProbe`, not inlined in MCP ``_do_ask``. Tests inject fakes via
the container and assert the probe is invoked with the right parameters
and that the no-answer envelope shape is preserved.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class _FakeSnapshotService:
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


def _accepted_snapshot(*, top_score: float = 0.9, reason: str | None = None) -> dict:
    return {
        "accept": True,
        "accepted_items": [{"id": "k1"}],
        "accepted_knowledge_ids": ["k1"],
        "accepted_block_ids": ["b1"],
        "accepted_passage_ids": ["p1"],
        "adjacent_allowlist": [],
        "top_score": top_score,
        "threshold": 0.35,
        "reason": reason,
        "intent": "ordinary",
        "snapshot_fingerprint": "fp-1",
    }


def _rejected_snapshot(*, top_score: float = 0.1, reason: str = "insufficient_relevant_evidence") -> dict:
    return {
        "accept": False,
        "accepted_items": [],
        "accepted_knowledge_ids": [],
        "accepted_block_ids": [],
        "accepted_passage_ids": [],
        "adjacent_allowlist": [],
        "top_score": top_score,
        "threshold": 0.35,
        "reason": reason,
        "intent": "ordinary",
        "direct_slot_audit": None,
        "snapshot_fingerprint": "fp-rej",
    }


def _install_ask_probe(monkeypatch, container, *, ask_probe=None):
    from src.mcp.tools import retrieval

    if ask_probe is not None:
        container.ask_probe = ask_probe
    monkeypatch.setattr(retrieval, "_get_container", lambda: container)
    return retrieval


# --------------------------------------------------------------------------- #
# AskProbe — direct unit tests (no MCP envelope)                              #
# --------------------------------------------------------------------------- #


def test_ask_probe_live_external_short_circuits_to_no_answer():
    """Live-external queries (today/quotes) must short-circuit before retrieval."""
    from src.application.ask_probe import AskProbe

    container = MagicMock()
    probe = AskProbe(container)
    result = probe.probe("中国电信股价今天多少")

    assert result.no_answer_payload is not None
    assert result.no_answer_payload["answer_mode"] == "no_answer"
    assert result.no_answer_payload["reason"] == "requires_current_external_data"
    assert result.no_answer_payload["snapshot_reused"] is False
    assert result.no_answer_payload["retrieval_count"] == 0
    # Live-external payload must NOT carry evidence_snapshot metadata.
    assert "evidence_snapshot" not in result.no_answer_payload
    assert "snapshot_fingerprint" not in result.no_answer_payload


def test_ask_probe_accepted_snapshot_proceeds_without_no_answer():
    """When the snapshot accepts, the probe returns it for the runner to use."""
    from src.application.ask_probe import AskProbe

    fake_snap = _FakeSnapshotService(_accepted_snapshot())
    container = SimpleNamespace(
        search_service=MagicMock(),
        evidence_snapshot_service=fake_snap,
        config=None,
    )
    # Pass snapshot helpers directly so the probe runs without AppContainer.
    probe = AskProbe(
        container,
        snapshot_builder=lambda q, **kw: fake_snap.build(
            SimpleNamespace(query=q, top_k=kw["top_k"], threshold=kw["threshold"], fetch_k=kw["fetch_k"])
        ),
        snapshot_registerer=lambda s, **kw: fake_snap.register(s, **kw),
    )
    result = probe.probe("营收资金管理办法 收支两条线")

    assert result.no_answer_payload is None
    assert result.snapshot is not None
    assert result.snapshot["accept"] is True
    assert result.accepted_knowledge_ids == {"k1"}
    assert result.accepted_block_ids == {"b1"}
    assert result.retrieval_count == 1
    assert result.snapshot_reused is False


def test_ask_probe_rejected_snapshot_returns_no_answer_payload():
    """When the snapshot rejects, the probe returns a no-answer payload with
    evidence_snapshot metadata (so the agent can audit which candidates were
    considered)."""
    from src.application.ask_probe import AskProbe

    fake_snap = _FakeSnapshotService(_rejected_snapshot(top_score=0.1, reason="insufficient_relevant_evidence"))
    container = SimpleNamespace(
        search_service=MagicMock(),
        evidence_snapshot_service=fake_snap,
        config=None,
    )
    probe = AskProbe(
        container,
        snapshot_builder=lambda q, **kw: fake_snap.build(
            SimpleNamespace(query=q, top_k=kw["top_k"], threshold=kw["threshold"], fetch_k=kw["fetch_k"])
        ),
        snapshot_registerer=lambda s, **kw: fake_snap.register(s, **kw),
    )
    result = probe.probe("公司搞比赛给员工发奖金 上限是多少")

    assert result.no_answer_payload is not None
    assert result.no_answer_payload["answer_mode"] == "no_answer"
    assert result.no_answer_payload["reason"] == "insufficient_relevant_evidence"
    assert result.no_answer_payload["snapshot_reused"] is False
    assert result.no_answer_payload["retrieval_count"] == 1
    # Rejected payload MUST carry evidence_snapshot metadata.
    assert "evidence_snapshot" in result.no_answer_payload
    assert result.no_answer_payload["evidence_snapshot"]["top_score"] == 0.1
    assert result.no_answer_payload["evidence_snapshot"]["snapshot_fingerprint"] == "fp-rej"
    assert result.no_answer_payload["snapshot_fingerprint"] == "fp-rej"
    assert result.no_answer_payload["user_notice"] == "知识库中未找到可直接支持该问题的证据。"


def test_ask_probe_snapshot_reuse_loads_existing_snapshot():
    """When evidence_snapshot_id is provided and load succeeds, the probe reuses."""
    from src.application.ask_probe import AskProbe

    fake_snap = _FakeSnapshotService(_accepted_snapshot())
    container = SimpleNamespace(
        search_service=MagicMock(),
        evidence_snapshot_service=fake_snap,
        config=None,
    )
    probe = AskProbe(
        container,
        snapshot_loader=lambda sid, **kw: fake_snap.load(sid, **kw),
        snapshot_builder=lambda q, **kw: fake_snap.build(
            SimpleNamespace(query=q, top_k=kw["top_k"], threshold=kw["threshold"], fetch_k=kw["fetch_k"])
        ),
        snapshot_registerer=lambda s, **kw: fake_snap.register(s, **kw),
    )
    result = probe.probe("营收资金管理办法", evidence_snapshot_id="snap-1")

    assert result.no_answer_payload is None
    assert result.snapshot_reused is True
    assert result.retrieval_count == 0
    assert len(fake_snap.load_calls) == 1
    assert fake_snap.load_calls[0]["snapshot_id"] == "snap-1"


def test_ask_probe_snapshot_load_failure_falls_back_to_build():
    """When load fails, the probe builds a fresh snapshot and records the reason."""
    from src.application.ask_probe import AskProbe

    def failing_loader(sid, **kw):
        return None, "snapshot_not_found", False

    fake_snap = _FakeSnapshotService(_accepted_snapshot())
    container = SimpleNamespace(
        search_service=MagicMock(),
        evidence_snapshot_service=fake_snap,
        config=None,
    )
    probe = AskProbe(
        container,
        snapshot_loader=failing_loader,
        snapshot_builder=lambda q, **kw: fake_snap.build(
            SimpleNamespace(query=q, top_k=kw["top_k"], threshold=kw["threshold"], fetch_k=kw["fetch_k"])
        ),
        snapshot_registerer=lambda s, **kw: fake_snap.register(s, **kw),
    )
    result = probe.probe("营收资金管理办法", evidence_snapshot_id="snap-missing")

    assert result.no_answer_payload is None
    assert result.snapshot_reused is False
    assert result.snapshot_reuse_reason == "snapshot_not_found"
    assert result.retrieval_count == 1
    assert len(fake_snap.build_calls) == 1


def test_ask_probe_returns_empty_result_when_probe_unavailable():
    """When the container is not an AppContainer (test doubles), the probe
    returns an empty ProbeResult so the adapter falls through to the
    post-generation gate."""
    from src.application.ask_probe import AskProbe

    # MagicMock is NOT an AppContainer instance.
    container = MagicMock()
    probe = AskProbe(container)
    result = probe.probe("普通查询 营收资金管理办法")

    assert result.no_answer_payload is None
    assert result.snapshot is None
    assert result.accepted_knowledge_ids == set()
    assert result.accepted_block_ids == set()
    assert result.adjacent_allowlist == []


# --------------------------------------------------------------------------- #
# MCP adapter — boundary delegation                                            #
# --------------------------------------------------------------------------- #


def test_mcp_do_ask_uses_container_ask_probe(monkeypatch):
    """When ``container.ask_probe`` is wired, MCP must delegate to it."""
    from src.application.ask_probe import ProbeResult
    from src.mcp.tools import retrieval

    class _RecordingProbe:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        def probe(self, question, *, evidence_snapshot_id=None, top_k=None, threshold=None):
            self.calls.append({
                "question": question,
                "evidence_snapshot_id": evidence_snapshot_id,
                "top_k": top_k,
                "threshold": threshold,
            })
            return ProbeResult(
                no_answer_payload={
                    "answer_mode": "no_answer",
                    "reason": "test_rejection",
                    "answer": "",
                    "sources": [],
                }
            )

    container = MagicMock()
    fake = _RecordingProbe()
    retrieval = _install_ask_probe(monkeypatch, container, ask_probe=fake)

    out = retrieval._do_ask("测试问题")

    assert len(fake.calls) == 1
    assert fake.calls[0]["question"] == "测试问题"
    assert out["answer_mode"] == "no_answer"
    assert out["reason"] == "test_rejection"


def test_mcp_do_ask_falls_through_when_probe_accepts(monkeypatch):
    """When the probe accepts (no_answer_payload=None), MCP must proceed to
    the runner path using the probe result's accepted sets."""
    from src.application.ask_probe import ProbeResult
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    class _RecordingProbeAccepting:
        """Probe that accepts (returns no_answer_payload=None) and records calls."""

        def __init__(self):
            self.calls = 0

        def probe(self, question, *, evidence_snapshot_id=None, top_k=None, threshold=None):
            self.calls += 1
            return ProbeResult(
                snapshot={"accept": True, "accepted_knowledge_ids": ["k1"]},
                accepted_knowledge_ids={"k1"},
                accepted_block_ids=set(),
                adjacent_allowlist=[],
                snapshot_reused=False,
                snapshot_reuse_reason="",
                retrieval_count=1,
            )

    # Probe accepts: snapshot with accept=True, accepted_kids={"k1"}.
    accepted_probe = _RecordingProbeAccepting()

    monkeypatch.setattr(
        Config,
        "get",
        lambda key, default=None: {
            "rag.ask.total_timeout": 5,
            "rag.ask.max_sources": 5,
            "rag.ask.no_answer_threshold": 0.35,
        }.get(key, default),
    )
    monkeypatch.setattr(retrieval, "_should_use_verified_ask", lambda: False)
    monkeypatch.setattr(retrieval, "AppContainer", type("X", (), {}))

    runner_called = {"n": 0}

    def fake_runner(question, timeout=5):
        runner_called["n"] += 1
        return {
            "answer": "test answer",
            "sources": [{"knowledge_id": "k1", "title": "t"}],
            "answer_mode": "grounded",
        }

    container = SimpleNamespace(
        rag_pipeline=SimpleNamespace(query=fake_runner),
        search_service=MagicMock(),
    )
    container.ask_probe = accepted_probe
    monkeypatch.setattr(retrieval, "_get_container", lambda: container)

    out = retrieval._do_ask("营收资金管理办法")

    assert runner_called["n"] == 1
    assert accepted_probe.calls == 1
    assert out["answer"] == "test answer"


def test_mcp_get_ask_probe_is_idempotent_per_container(monkeypatch):
    """The resolver returns the same container-provided probe on repeated calls."""
    container = MagicMock()
    fake = MagicMock()
    container.ask_probe = fake
    retrieval = _install_ask_probe(monkeypatch, container, ask_probe=fake)

    probe1 = retrieval._get_ask_probe()
    probe2 = retrieval._get_ask_probe()
    assert probe1 is probe2 is fake


def test_mcp_do_ask_live_external_returns_no_answer_without_runner(monkeypatch):
    """Live-external queries must short-circuit before the runner is invoked.

    Regression for the boundary: the probe's no_answer_payload is returned
    directly; the runner must NOT be called.
    """
    from src.application.ask_probe import ProbeResult
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    class _LiveExternalProbe:
        def probe(self, question, *, evidence_snapshot_id=None, top_k=None, threshold=None):
            return ProbeResult(
                no_answer_payload={
                    "answer_mode": "no_answer",
                    "reason": "requires_current_external_data",
                    "answer": "",
                    "sources": [],
                    "snapshot_reused": False,
                    "retrieval_count": 0,
                }
            )

    monkeypatch.setattr(
        Config,
        "get",
        lambda key, default=None: {
            "rag.ask.total_timeout": 5,
            "rag.ask.max_sources": 5,
            "rag.ask.no_answer_threshold": 0.35,
        }.get(key, default),
    )
    monkeypatch.setattr(retrieval, "_should_use_verified_ask", lambda: False)
    monkeypatch.setattr(retrieval, "AppContainer", type("X", (), {}))

    runner_called = {"n": 0}

    def boom(*a, **k):
        runner_called["n"] += 1
        raise AssertionError("runner must not be called for live-external queries")

    container = SimpleNamespace(
        rag_pipeline=SimpleNamespace(query=boom),
        search_service=MagicMock(),
    )
    container.ask_probe = _LiveExternalProbe()
    monkeypatch.setattr(retrieval, "_get_container", lambda: container)

    out = retrieval._do_ask("中国电信股价今天多少")

    assert out["answer_mode"] == "no_answer"
    assert out["reason"] == "requires_current_external_data"
    assert runner_called["n"] == 0


# --------------------------------------------------------------------------- #
# Architecture guard                                                           #
# --------------------------------------------------------------------------- #


def test_mcp_retrieval_does_not_inline_snapshot_build_or_gate_rejection():
    """ADR §6: MCP ``_do_ask`` must delegate snapshot build/load/register and
    gate-rejection envelope construction to :class:`AskProbe`, not inline them."""
    import inspect

    from src.mcp.tools import retrieval

    src = inspect.getsource(retrieval._do_ask)
    # The snapshot lifecycle helpers must be called via the probe, not inline.
    assert "_build_shared_snapshot(" not in src
    assert "_load_snapshot(" not in src
    assert "_register_snapshot(" not in src
    # The gate-rejection envelope (no_answer dict carrying ``evidence_snapshot``
    # metadata) is built by AskProbe via ``_empty_no_answer_payload``.  _do_ask
    # must not inline that envelope.  The accepted-path summary attachment
    # (``result["evidence_snapshot"] = {...}`` after the runner returns) is a
    # separate presentation concern; moving it into the answering layer is
    # tracked as a follow-up thinning target (ADR §6), not a gate-rejection
    # envelope construction.
    assert "_empty_no_answer_payload(" not in src
    # The live-external no-answer envelope must also be in AskProbe.
    assert "requires_current_external_data" not in src
