"""AnswerService — single application-layer ask orchestrator.

Calls SearchService.execute() (RetrievalOrchestrator under the hood).
Does not touch DB/Wiki/Gate/MCP envelopes directly.

WP2: answer.orchestrator pseudo dual-path removed — only unified assemble path.
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
    ) -> AnswerExecution:
        # resolve for logging/compatibility only
        resolve_answer_orchestrator_mode(self._config)
        payload = self._assemble_payload(
            question, top_k=top_k, use_llm=use_llm, llm_answer=llm_answer,
        )
        return AnswerExecution.from_payload(payload)

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
            fb = list(getattr(execution, "fallbacks", ()) or [])
            if fb and "fallbacks" not in trace:
                trace["fallbacks"] = fb
            disclose_rows = list(getattr(execution, "disclose_claims", ()) or [])
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
