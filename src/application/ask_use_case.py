"""AskUseCase — application entry for grounded Q&A (Phase 2).

Does not build MCP Envelopes. Receives a question and optional snapshot_id;
business answer assembly stays in AnswerService / AnswerPipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AskRequest:
    question: str
    top_k: int = 5
    evidence_snapshot_id: str | None = None
    context: dict[str, Any] | None = None


class AskUseCase:
    """Application facade over AnswerService.

    When evidence_snapshot_id is provided, the answer path should reuse the
    shared snapshot (miss reasons stay stable and are recorded by the
    answer/snapshot layer — not re-invented here).
    """

    def __init__(self, answer_service: Any):
        self._answer = answer_service

    def execute(self, request: AskRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "question": request.question,
            "top_k": request.top_k,
        }
        # Forward snapshot id when the answer service supports it.
        if request.evidence_snapshot_id is not None:
            kwargs["evidence_snapshot_id"] = request.evidence_snapshot_id
        if request.context is not None:
            kwargs["context"] = request.context

        answer = self._answer
        # Prefer structured ask / execute APIs when present.
        if hasattr(answer, "ask"):
            try:
                return dict(answer.ask(**kwargs))
            except TypeError:
                # Older signature without snapshot kwargs
                return dict(
                    answer.ask(
                        question=request.question,
                        top_k=request.top_k,
                    )
                )
        if hasattr(answer, "execute"):
            result = answer.execute(request.question, top_k=request.top_k)
            return dict(result) if isinstance(result, dict) else result
        raise TypeError("answer_service must provide ask() or execute()")
