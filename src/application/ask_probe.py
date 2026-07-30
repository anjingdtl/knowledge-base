"""AskProbe — pre-LLM evidence gate orchestration for ``ask`` (Phase 2 Task 2.4).

Owns the snapshot load/build/reuse + live-external short-circuit + gate
rejection that previously lived inline in MCP ``_do_ask``. The probe returns
a structured :class:`ProbeResult`; the MCP adapter only needs to:

* return ``ProbeResult.no_answer_payload`` directly when the gate rejected, or
* feed ``snapshot`` / ``accepted_knowledge_ids`` / ``accepted_block_ids`` /
  ``adjacent_allowlist`` into the answer runner + citation filter when the
  gate accepted.

This module does NOT build MCP envelopes — the no_answer payload is a plain
dict that mirrors the public ``ask`` contract; the MCP adapter returns it
as-is. This keeps the boundary clean: business orchestration here, transport
wrapping in MCP.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from src.core.container import AppContainer
from src.utils.config import Config


def _empty_no_answer_payload(
    *,
    reason: str,
    warnings: list[str],
    snapshot_reused: bool = False,
    snapshot_reuse_reason: str = "",
    retrieval_count: int = 0,
    evidence_snapshot: dict[str, Any] | None = None,
    snapshot_fingerprint: str | None = None,
    user_notice: str | None = None,
) -> dict[str, Any]:
    """Build the canonical no-answer payload returned by ``ask``.

    The shape is frozen by ``adr-search-ask-contract-v2.md`` — every field
    here is part of the public contract. Do not add or remove fields without
    updating the contract ADR and the snapshot tests.
    """
    payload: dict[str, Any] = {
        "answer": "",
        "sources": [],
        "source_graph": {"nodes": [], "edges": [], "truncated": False, "node_count": 0},
        "route": {"mode": "no_answer", "explanation": reason},
        "query_plan": {},
        "block_contexts": {},
        "warnings": list(warnings),
        "wiki_context": "",
        "trace_id": "",
        "answer_mode": "no_answer",
        "reason": reason,
        "retrieval_decision": reason,
        "answer_validation_decision": "",
        "snapshot_reused": snapshot_reused,
        "snapshot_reuse_reason": snapshot_reuse_reason,
        "retrieval_count": retrieval_count,
        "conflict_disclosed": False,
        "claims_used": [],
        "raw_evidence_used": [],
        "conflicts": [],
        "fallbacks": [],
    }
    if evidence_snapshot is not None:
        payload["evidence_snapshot"] = evidence_snapshot
    if snapshot_fingerprint is not None:
        payload["snapshot_fingerprint"] = snapshot_fingerprint
    if user_notice is not None:
        payload["user_notice"] = user_notice
    return payload


@dataclass
class ProbeResult:
    """Outcome of :meth:`AskProbe.probe`.

    * ``snapshot`` — the built/loaded evidence snapshot, or ``None`` when
      the probe could not run (test doubles / container without
      ``search_service``). The MCP adapter falls through to the legacy
      post-generation gate in that case.
    * ``accepted_knowledge_ids`` / ``accepted_block_ids`` / ``adjacent_allowlist``
      — the citation allowlist extracted from the snapshot. Empty when
      ``snapshot`` is None.
    * ``snapshot_reused`` / ``snapshot_reuse_reason`` / ``retrieval_count``
      — provenance fields for the public envelope.
    * ``no_answer_payload`` — when the gate rejected (live-external short
      circuit, insufficient evidence, or out-of-domain), a fully-formed
      no-answer dict ready for the MCP adapter to return. ``None`` means
      the probe accepted and the adapter should proceed to the answer runner.
    """

    snapshot: dict[str, Any] | None = None
    accepted_knowledge_ids: set[str] = field(default_factory=set)
    accepted_block_ids: set[str] = field(default_factory=set)
    adjacent_allowlist: list[dict] = field(default_factory=list)
    snapshot_reused: bool = False
    snapshot_reuse_reason: str = ""
    retrieval_count: int = 0
    no_answer_payload: dict[str, Any] | None = None


class AskProbe:
    """Pre-LLM evidence gate orchestrator for ``ask``.

    Constructed with the same snapshot-service helpers used by the MCP
    adapter (so test doubles wired on the container are honored). The probe
    is intentionally side-effect-free apart from snapshot registration —
    it never calls the answer runner or the LLM.
    """

    def __init__(
        self,
        container: Any,
        *,
        snapshot_service_getter: Any | None = None,
        snapshot_loader: Any | None = None,
        snapshot_builder: Any | None = None,
        snapshot_registerer: Any | None = None,
    ):
        self._container = container
        # The MCP adapter passes its own helper callables so the probe uses
        # the same lazy container-resolution path. When None, the probe
        # resolves them lazily from the container.
        self._snapshot_service_getter = snapshot_service_getter
        self._snapshot_loader = snapshot_loader
        self._snapshot_builder = snapshot_builder
        self._snapshot_registerer = snapshot_registerer

    def probe(
        self,
        question: str,
        *,
        evidence_snapshot_id: str | None = None,
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> ProbeResult:
        """Run the pre-LLM evidence probe.

        Returns a :class:`ProbeResult`. When ``no_answer_payload`` is not
        None, the adapter must return it directly (gate rejected). When
        ``None``, the adapter proceeds to the answer runner using the
        accepted sets carried on the result.
        """
        from src.services.relevance_gate import is_current_information_query

        weak_threshold = float(
            threshold if threshold is not None
            else Config.get("rag.ask.no_answer_threshold", 0.35) or 0.35
        )
        probe_k = int(
            top_k if top_k is not None
            else Config.get("rag.ask.max_sources", 5) or 5
        )

        # Gate 1: live-external short-circuit (today/quotes/news/forecasts).
        if is_current_information_query(question):
            payload = _empty_no_answer_payload(
                reason="requires_current_external_data",
                warnings=["requires_current_external_data"],
            )
            return ProbeResult(no_answer_payload=payload)

        container = self._container
        # The probe runs when EITHER:
        #   * the caller provided snapshot helpers (tests with fakes), OR
        #   * the container is a real AppContainer with a search_service
        #     (production path; filters out MagicMock test doubles whose
        #     ``search_service`` attribute is itself a MagicMock, not None).
        probe_available = (
            self._snapshot_builder is not None
            or (
                isinstance(container, AppContainer)
                and getattr(container, "search_service", None) is not None
            )
        )
        if not probe_available:
            # Test doubles / legacy containers: probe cannot run. The
            # adapter falls through to the post-generation evidence gate.
            return ProbeResult()

        snapshot: dict | None = None
        snapshot_reused = False
        snapshot_reuse_reason = ""
        retrieval_count = 0

        # Gate 2: try to reuse the snapshot carried by evidence_snapshot_id.
        if evidence_snapshot_id:
            loaded, miss_reason, reused = self._load_snapshot(
                evidence_snapshot_id, query=question, top_k=probe_k,
            )
            if reused and loaded is not None:
                snapshot = loaded
                snapshot_reused = True
                snapshot_reuse_reason = ""
                retrieval_count = 0
            else:
                snapshot_reuse_reason = miss_reason or "snapshot_load_failed"
                snapshot_reused = False

        # Gate 3: build a fresh snapshot when reuse did not yield one.
        if snapshot is None:
            try:
                from src.retrieval.candidate_pool import CandidatePoolPolicy

                snapshot = self._build_snapshot(
                    question,
                    top_k=probe_k,
                    threshold=weak_threshold,
                    fetch_k=CandidatePoolPolicy.from_request(probe_k).fetch_k,
                )
                retrieval_count = 1
                # Register for potential subsequent reuse (best-effort).
                try:
                    self._register_snapshot(snapshot, query=question, top_k=probe_k)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001 - probe is best-effort
                snapshot = None

        # Gate 4: extract the citation allowlist from the snapshot.
        accepted_kids: set[str] = set()
        accepted_blocks: set[str] = set()
        adjacent_allowlist: list[dict] = []
        if snapshot is not None:
            accepted_kids = {
                k for k in (snapshot.get("accepted_knowledge_ids") or []) if k
            }
            accepted_blocks = {
                b for b in (snapshot.get("accepted_block_ids") or []) if b
            }
            adjacent_allowlist = list(snapshot.get("adjacent_allowlist") or [])
            for entry in adjacent_allowlist:
                bid = (entry.get("block_id") or "").strip()
                kid = (entry.get("knowledge_id") or "").strip()
                if bid:
                    accepted_blocks.add(bid)
                if kid:
                    accepted_kids.add(kid)

        # Gate 5: gate-rejection envelope (insufficient evidence / out-of-domain).
        if snapshot is not None and not snapshot.get("accept"):
            warn = (
                f"evidence gate blocked generation "
                f"(top_score={snapshot.get('top_score', 0)} < {weak_threshold})"
            )
            if snapshot.get("direct_slot_audit"):
                warn += f"; direct_slot={snapshot.get('direct_slot_audit')}"
            gate_reason = snapshot.get("reason") or "retrieval_gate_rejected"
            evidence_snapshot_meta = {
                "accepted_knowledge_ids": list(snapshot.get("accepted_knowledge_ids") or []),
                "accepted_block_ids": list(snapshot.get("accepted_block_ids") or []),
                "accepted_passage_ids": list(snapshot.get("accepted_passage_ids") or []),
                "adjacent_passage_ids": [
                    str(entry.get("passage_id"))
                    for entry in (snapshot.get("adjacent_allowlist") or [])
                    if isinstance(entry, dict) and entry.get("passage_id")
                ],
                "top_score": snapshot.get("top_score"),
                "intent": snapshot.get("intent"),
                "direct_slot_evidence": bool(snapshot.get("direct_slot_evidence")),
                "direct_slot_audit": snapshot.get("direct_slot_audit") or {},
                "adjacent_unit": snapshot.get("adjacent_unit"),
                "adjacent_count": snapshot.get("adjacent_count"),
                "adjacent_fallback_reason": snapshot.get("adjacent_fallback_reason"),
                "snapshot_fingerprint": snapshot.get("snapshot_fingerprint"),
            }
            payload = _empty_no_answer_payload(
                reason=gate_reason,
                warnings=[warn],
                snapshot_reused=snapshot_reused,
                snapshot_reuse_reason=snapshot_reuse_reason,
                retrieval_count=retrieval_count,
                evidence_snapshot=evidence_snapshot_meta,
                snapshot_fingerprint=snapshot.get("snapshot_fingerprint"),
                user_notice="知识库中未找到可直接支持该问题的证据。",
            )
            return ProbeResult(
                snapshot=snapshot,
                accepted_knowledge_ids=accepted_kids,
                accepted_block_ids=accepted_blocks,
                adjacent_allowlist=adjacent_allowlist,
                snapshot_reused=snapshot_reused,
                snapshot_reuse_reason=snapshot_reuse_reason,
                retrieval_count=retrieval_count,
                no_answer_payload=payload,
            )

        # Gate accepted (or probe could not build a snapshot): proceed to runner.
        return ProbeResult(
            snapshot=snapshot,
            accepted_knowledge_ids=accepted_kids,
            accepted_block_ids=accepted_blocks,
            adjacent_allowlist=adjacent_allowlist,
            snapshot_reused=snapshot_reused,
            snapshot_reuse_reason=snapshot_reuse_reason,
            retrieval_count=retrieval_count,
        )

    # ------------------------------------------------------------------ #
    # Snapshot service resolution                                          #
    # ------------------------------------------------------------------ #

    def _load_snapshot(self, snapshot_id: str, *, query: str, top_k: int):
        if self._snapshot_loader is not None:
            return self._snapshot_loader(snapshot_id, query=query, top_k=top_k)
        svc = self._resolve_snapshot_service()
        return svc.load(snapshot_id, query=query, top_k=top_k)

    def _build_snapshot(self, query: str, *, top_k: int, threshold: float, fetch_k: int):
        if self._snapshot_builder is not None:
            return self._snapshot_builder(
                query, top_k=top_k, threshold=threshold, fetch_k=fetch_k,
            )
        svc = self._resolve_snapshot_service()
        from src.application.evidence_snapshot_service import SnapshotBuildRequest

        return svc.build(
            SnapshotBuildRequest(query=query, top_k=top_k, threshold=threshold, fetch_k=fetch_k),
        )

    def _register_snapshot(self, snapshot: dict, *, query: str, top_k: int) -> str:
        if self._snapshot_registerer is not None:
            return self._snapshot_registerer(snapshot, query=query, top_k=top_k)
        svc = self._resolve_snapshot_service()
        return svc.register(snapshot, query=query, top_k=top_k)

    def _resolve_snapshot_service(self):
        if self._snapshot_service_getter is not None:
            return self._snapshot_service_getter()
        svc = getattr(self._container, "evidence_snapshot_service", None)
        if svc is not None:
            return svc
        from src.application.candidate_retrieval_service import (
            CandidateRetrievalService,
        )
        from src.application.evidence_snapshot_service import (
            EvidenceSnapshotService,
        )

        return EvidenceSnapshotService(
            CandidateRetrievalService(self._container),
            config=getattr(self._container, "config", None),
            container=self._container,
        )
