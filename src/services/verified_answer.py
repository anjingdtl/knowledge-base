"""Compatibility re-exports for verified answer assembly (WP2).

Business logic lives in ``src.answering``. Import from answering for new code.
"""
from __future__ import annotations

from src.answering.assembler import (
    ANSWER_MODE_CONFLICT,
    ANSWER_MODE_HYBRID,
    ANSWER_MODE_NO_ANSWER,
    ANSWER_MODE_RAW,
    assemble_answer_payload,
    build_sources,
)
from src.answering.citations import (
    build_claim_citations,
    build_raw_evidence_used,
    is_claim as _is_claim,
    is_raw as _is_raw,
)
from src.answering.fallbacks import (
    build_generation_context as _build_generation_context,
    fallback_hybrid_text as _fallback_hybrid_text,
    fallback_raw_text as _fallback_raw_text,
    format_conflict_answer as _format_conflict_answer,
    format_no_answer as _format_no_answer,
)
from src.answering.service import AnswerService

# Historical class name — delegates to AnswerService
VerifiedAnswerService = AnswerService

__all__ = [
    "ANSWER_MODE_HYBRID",
    "ANSWER_MODE_RAW",
    "ANSWER_MODE_CONFLICT",
    "ANSWER_MODE_NO_ANSWER",
    "assemble_answer_payload",
    "build_claim_citations",
    "build_raw_evidence_used",
    "build_sources",
    "AnswerService",
    "VerifiedAnswerService",
    "_is_claim",
    "_is_raw",
    "_build_generation_context",
    "_fallback_hybrid_text",
    "_fallback_raw_text",
    "_format_conflict_answer",
    "_format_no_answer",
]
