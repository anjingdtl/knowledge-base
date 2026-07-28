"""AnswerService — single application-layer ask orchestrator.

Calls SearchService.execute() (RetrievalOrchestrator under the hood).
Does not touch DB/Wiki/Gate/MCP envelopes directly.

WP2: answer.orchestrator pseudo dual-path removed — only unified assemble path.

SPEC v2 Phase 1/3: accepts a pre-gated evidence snapshot so search and ask
share the same candidates; expands adjacent blocks into generation context
before the LLM sees the evidence (KB-019).
"""
from __future__ import annotations

import logging
from typing import Any

from src.answering.assembler import assemble_answer_payload
from src.answering.generation import Generator
from src.answering.models import AnswerExecution

logger = logging.getLogger(__name__)


def resolve_answer_orchestrator_mode(config: Any) -> str:
    """Always unified. Kept for config/docs compatibility (legacy values ignored)."""
    if config is None:
        return "unified"
    raw: Any = None
    if isinstance(config, dict):
        block = config.get("answer") or {}
        if isinstance(block, dict):
            raw = block.get("orchestrator")
        if raw is None:
            raw = config.get("answer.orchestrator")
    else:
        getter = getattr(config, "get", None)
        if callable(getter):
            raw = getter("answer.orchestrator", None)
            if raw is None:
                block = getter("answer", None)
                if isinstance(block, dict):
                    raw = block.get("orchestrator")
    mode = str(raw or "unified").strip().lower()
    if mode and mode not in {"unified", "legacy", "shadow"}:
        logger.warning("Unknown answer.orchestrator=%r; using unified", raw)
    # WP2-T2: no behavioral difference — always unified
    if mode in {"legacy", "shadow"}:
        logger.debug(
            "answer.orchestrator=%s has no separate path; using unified assemble",
            mode,
        )
    return "unified"


class AnswerService:
    """Question → SearchExecution → assemble → AnswerExecution."""

    def __init__(self, search_service: Any, llm: Any = None, config: Any = None):
        self._search = search_service
        self._llm = llm
        self._config = config or {}
        self._generator = Generator(llm)

    def execute(
        self,
        question: str,
        *,
        top_k: int = 5,
        use_llm: bool = True,
        llm_answer: str | None = None,
        evidence_snapshot: dict[str, Any] | None = None,
    ) -> AnswerExecution:
        # resolve for logging/compatibility only
        resolve_answer_orchestrator_mode(self._config)
        payload = self._assemble_payload(
            question,
            top_k=top_k,
            use_llm=use_llm,
            llm_answer=llm_answer,
            evidence_snapshot=evidence_snapshot,
        )
        return AnswerExecution.from_payload(payload)

    def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        use_llm: bool = True,
        llm_answer: str | None = None,
        evidence_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.execute(
            question,
            top_k=top_k,
            use_llm=use_llm,
            llm_answer=llm_answer,
            evidence_snapshot=evidence_snapshot,
        ).to_ask_payload()

    def _run_search(
        self,
        question: str,
        *,
        top_k: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        """Only via SearchService.execute (RetrievalOrchestrator under the hood)."""
        if hasattr(self._search, "execute"):
            execution = self._search.execute(question, top_k=top_k)
            results = list(getattr(execution, "results", ()) or ())
            trace = dict(getattr(execution, "trace", None) or {})
            fb = list(getattr(execution, "fallbacks", ()) or [])
            if fb and "fallbacks" not in trace:
                trace["fallbacks"] = fb
            disclose_rows = list(getattr(execution, "disclose_claims", ()) or [])
            return results, trace, disclose_rows
        results = list(self._search.search(question, top_k=top_k) or [])
        return results, {}, []

    def _list_blocks_for_page(self, page_id: str) -> list[dict[str, Any]]:
        """Load blocks for adjacent expansion; prefers search_service.db."""
        db = getattr(self._search, "_db", None)
        if db is None:
            try:
                from src.services.db import Database
                db = Database
            except Exception:  # noqa: BLE001
                return []
        try:
            conn = db.get_conn() if hasattr(db, "get_conn") else None
            if conn is None:
                return []
            rows = conn.execute(
                """SELECT id, parent_id, page_id, content, block_type, properties,
                          order_idx, created_at, updated_at
                   FROM blocks
                   WHERE page_id = ?
                   ORDER BY order_idx ASC, created_at ASC""",
                (page_id,),
            ).fetchall()
            out = []
            for row in rows:
                d = dict(row)
                d.setdefault("block_id", d.get("id") or "")
                d.setdefault("knowledge_id", d.get("page_id") or page_id)
                d.setdefault("text", d.get("content") or "")
                out.append(d)
            return out
        except Exception as exc:  # noqa: BLE001
            logger.debug("list blocks for adjacent expansion failed: %s", exc)
            return []

    def _results_from_snapshot(
        self,
        snapshot: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        """Use pre-accepted generation items; never re-open unconstrained retrieval."""
        # Prefer generation_items (latest-version filtered) when present.
        items = list(
            snapshot.get("generation_items")
            or snapshot.get("accepted_items")
            or ()
        )
        trace = {
            "mode": "preaccepted_snapshot",
            "query": snapshot.get("query") or "",
            "gate": {
                "accept": snapshot.get("accept"),
                "top_score": snapshot.get("top_score"),
                "threshold": snapshot.get("threshold"),
                "reason": snapshot.get("reason"),
                "intent": snapshot.get("intent"),
            },
            "accepted_knowledge_ids": list(snapshot.get("accepted_knowledge_ids") or []),
            "accepted_block_ids": list(snapshot.get("accepted_block_ids") or []),
            "adjacent_allowlist": list(snapshot.get("adjacent_allowlist") or []),
            "stages": dict(snapshot.get("stages") or {}),
            "sources": {"preaccepted": True},
        }
        return items, trace, []

    def _expand_adjacent_into_results(
        self,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """SPEC v2 Phase 3: join consecutive blocks of the same knowledge item
        into the generation context before the LLM runs."""
        from src.retrieval.canonical_snapshot import expand_results_with_adjacent

        try:
            return expand_results_with_adjacent(
                results,
                list_blocks_fn=self._list_blocks_for_page,
                window=1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("adjacent expansion skipped: %s", exc)
            return results

    def _assemble_payload(
        self,
        question: str,
        *,
        top_k: int,
        use_llm: bool,
        llm_answer: str | None,
        evidence_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if evidence_snapshot is not None:
            results, trace, disclose_rows = self._results_from_snapshot(evidence_snapshot)
            # Diff check: if a caller also left raw re-retrieval rows, refuse
            # to silently mix them. Snapshot is authoritative.
            snap_ids = {
                (str(r.get("knowledge_id") or ""), str(r.get("block_id") or ""))
                for r in results
                if isinstance(r, dict)
            }
            trace.setdefault("stages", {})["snapshot_id_count"] = len(snap_ids)
        else:
            results, trace, disclose_rows = self._run_search(question, top_k=top_k)

        # Adjacent block expansion for clause integrity (KB-019) — production path.
        # Only annotate the trace when neighbors are actually appended, so
        # existing public ask contract snapshots stay stable for single-block
        # fixtures that have no adjacent rows in the DB.
        before_n = len(results)
        results = self._expand_adjacent_into_results(results)
        if len(results) > before_n:
            stages = dict(trace.get("stages") or {})
            stages["adjacent_expanded"] = True
            stages["context_block_count"] = len(results)
            stages["adjacent_added"] = len(results) - before_n
            trace["stages"] = stages

        generate_fn = None
        if use_llm and llm_answer is None:
            generate_fn = self._generator.make_generate_fn()
        payload = assemble_answer_payload(
            question,
            results,
            llm_answer=llm_answer,
            search_trace=trace,
            disclose_claims=disclose_rows,
            generate_fn=generate_fn,
        )
        payload.setdefault(
            "source_graph",
            {"nodes": [], "edges": [], "truncated": False, "node_count": 0},
        )
        payload.setdefault(
            "route",
            {
                "mode": payload["answer_mode"],
                "explanation": f"verified answer path: {payload['answer_mode']}",
                "search_mode": trace.get("mode"),
                "intent": (trace.get("route") or {}).get("intent")
                or (trace.get("gate") or {}).get("intent"),
            },
        )
        payload.setdefault("query_plan", {})
        payload.setdefault("block_contexts", {})
        payload.setdefault("wiki_context", "")
        # Surface snapshot allowlist for MCP citation integrity (do not expand
        # it from pipeline raw_evidence — SPEC v2 §4.2.5 / §5.1.1).
        if evidence_snapshot is not None:
            payload["_evidence_snapshot"] = {
                "accepted_knowledge_ids": list(
                    evidence_snapshot.get("accepted_knowledge_ids") or []
                ),
                "accepted_block_ids": list(
                    evidence_snapshot.get("accepted_block_ids") or []
                ),
                "adjacent_allowlist": list(
                    evidence_snapshot.get("adjacent_allowlist") or []
                ),
                "generation_knowledge_ids": list(
                    evidence_snapshot.get("generation_knowledge_ids") or []
                ),
            }
        return payload
