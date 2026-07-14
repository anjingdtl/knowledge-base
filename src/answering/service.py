"""AnswerService — single application-layer ask orchestrator (Phase-3).

Calls SearchService.execute() (which routes through RetrievalOrchestrator).
Does not touch DB/Wiki/Gate/MCP envelopes directly.
"""
from __future__ import annotations

import logging
from typing import Any

from src.answering.generation import Generator
from src.answering.models import AnswerExecution
from src.answering.shadow import compare_answers, log_answer_shadow

logger = logging.getLogger(__name__)

_VALID_MODES = frozenset({"legacy", "shadow", "unified"})


def resolve_answer_orchestrator_mode(config: Any) -> str:
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
    if mode not in _VALID_MODES:
        logger.warning("Unknown answer.orchestrator=%r; using unified", raw)
        return "unified"
    return mode


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
    ) -> AnswerExecution:
        mode = resolve_answer_orchestrator_mode(self._config)
        if mode == "legacy":
            return self._execute_legacy_primary(
                question, top_k=top_k, use_llm=use_llm, llm_answer=llm_answer,
            )
        if mode == "shadow":
            primary = self._execute_legacy_primary(
                question, top_k=top_k, use_llm=use_llm, llm_answer=llm_answer,
            )
            try:
                candidate = self._execute_unified(
                    question, top_k=top_k, use_llm=use_llm, llm_answer=llm_answer,
                )
                diff = compare_answers(primary, candidate)
                log_answer_shadow(diff, question_preview=question)
            except Exception as e:  # noqa: BLE001
                logger.warning("answer shadow unified path failed: %s", e)
            return primary
        return self._execute_unified(
            question, top_k=top_k, use_llm=use_llm, llm_answer=llm_answer,
        )

    def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        use_llm: bool = True,
        llm_answer: str | None = None,
    ) -> dict[str, Any]:
        return self.execute(
            question, top_k=top_k, use_llm=use_llm, llm_answer=llm_answer,
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
            # Merge fallbacks from SearchExecution into trace for assemble
            fb = list(getattr(execution, "fallbacks", ()) or ())
            if fb and "fallbacks" not in trace:
                trace["fallbacks"] = fb
            disclose_rows = list(getattr(execution, "disclose_claims", ()) or ())
            return results, trace, disclose_rows
        results = list(self._search.search(question, top_k=top_k) or [])
        return results, {}, []

    def _assemble_payload(
        self,
        question: str,
        *,
        top_k: int,
        use_llm: bool,
        llm_answer: str | None,
    ) -> dict[str, Any]:
        from src.services.verified_answer import assemble_answer_payload

        results, trace, disclose_rows = self._run_search(question, top_k=top_k)
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
                "intent": (trace.get("route") or {}).get("intent"),
            },
        )
        payload.setdefault("query_plan", {})
        payload.setdefault("block_contexts", {})
        payload.setdefault("wiki_context", "")
        return payload

    def _execute_unified(
        self,
        question: str,
        *,
        top_k: int,
        use_llm: bool,
        llm_answer: str | None,
    ) -> AnswerExecution:
        payload = self._assemble_payload(
            question, top_k=top_k, use_llm=use_llm, llm_answer=llm_answer,
        )
        return AnswerExecution.from_payload(payload)

    def _execute_legacy_primary(
        self,
        question: str,
        *,
        top_k: int,
        use_llm: bool,
        llm_answer: str | None,
    ) -> AnswerExecution:
        """Legacy primary uses the same assemble path (structural parity with unified)."""
        payload = self._assemble_payload(
            question, top_k=top_k, use_llm=use_llm, llm_answer=llm_answer,
        )
        payload = dict(payload)
        st = dict(payload.get("search_trace") or {})
        st.setdefault("answer_path", "legacy_primary")
        payload["search_trace"] = st
        return AnswerExecution.from_payload(payload)
