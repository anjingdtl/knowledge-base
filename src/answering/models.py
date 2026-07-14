"""AnswerExecution — application-layer answer contract (Phase-3)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AnswerExecution:
    """One ask request output (immutable core fields).

    Extra MCP-compat fields (route, source_graph, …) are assembled via
    ``to_ask_payload()`` so public Ask snapshots stay stable.
    """

    answer: str
    answer_mode: str
    sources: tuple[dict[str, Any], ...]
    claims_used: tuple[dict[str, Any], ...]
    raw_evidence_used: tuple[dict[str, Any], ...]
    conflicts: tuple[dict[str, Any], ...]
    fallbacks: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    trace_id: str
    # Optional extras carried for payload assembly (not Spec core, but needed)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_ask_payload(self) -> dict[str, Any]:
        """Dict shape expected by MCP ask / public Ask contract."""
        payload: dict[str, Any] = {
            "answer": self.answer,
            "answer_mode": self.answer_mode,
            "sources": list(self.sources),
            "claims_used": list(self.claims_used),
            "raw_evidence_used": list(self.raw_evidence_used),
            "conflicts": list(self.conflicts),
            "fallbacks": list(self.fallbacks),
            "warnings": list(self.warnings),
            "trace_id": self.trace_id,
        }
        # Preserve any additional keys from assembly (conflict_disclosed, search_trace, …)
        for key, value in (self.extras or {}).items():
            if key not in payload:
                payload[key] = value
        payload.setdefault(
            "source_graph",
            {"nodes": [], "edges": [], "truncated": False, "node_count": 0},
        )
        payload.setdefault(
            "route",
            {
                "mode": self.answer_mode,
                "explanation": f"verified answer path: {self.answer_mode}",
            },
        )
        payload.setdefault("query_plan", {})
        payload.setdefault("block_contexts", {})
        payload.setdefault("wiki_context", "")
        payload.setdefault("conflict_disclosed", self.answer_mode == "conflict_disclosure")
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AnswerExecution":
        known = {
            "answer",
            "answer_mode",
            "sources",
            "claims_used",
            "raw_evidence_used",
            "conflicts",
            "fallbacks",
            "warnings",
            "trace_id",
        }
        extras = {k: v for k, v in payload.items() if k not in known}
        return cls(
            answer=str(payload.get("answer") or ""),
            answer_mode=str(payload.get("answer_mode") or ""),
            sources=tuple(payload.get("sources") or ()),
            claims_used=tuple(payload.get("claims_used") or ()),
            raw_evidence_used=tuple(payload.get("raw_evidence_used") or ()),
            conflicts=tuple(payload.get("conflicts") or ()),
            fallbacks=tuple(payload.get("fallbacks") or ()),
            warnings=tuple(payload.get("warnings") or ()),
            trace_id=str(payload.get("trace_id") or ""),
            extras=extras,
        )
