"""Answer shadow comparison — structural fields only (not LLM full text)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.answering.models import AnswerExecution

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnswerShadowDiff:
    answer_mode_match: bool
    claim_ids_match: bool
    raw_evidence_ids_match: bool
    conflicts_match: bool
    fallbacks_match: bool
    citation_keys_match: bool
    no_answer_match: bool
    notes: tuple[str, ...] = ()


def _claim_ids(ex: AnswerExecution) -> set[str]:
    return {
        str(c.get("claim_id"))
        for c in ex.claims_used
        if c.get("claim_id")
    }


def _raw_ids(ex: AnswerExecution) -> set[str]:
    out: set[str] = set()
    for r in ex.raw_evidence_used:
        kid = str(r.get("knowledge_id") or "")
        bid = str(r.get("block_id") or "")
        out.add(f"{kid}|{bid}")
    return out


def _conflict_keys(ex: AnswerExecution) -> set[str]:
    keys: set[str] = set()
    for c in ex.conflicts:
        if isinstance(c, dict):
            keys.add(
                str(
                    c.get("conflict_id")
                    or (c.get("claim_a_id"), c.get("claim_b_id"))
                    or sorted(c.items()),
                ),
            )
        else:
            keys.add(str(c))
    return keys


def _fallback_keys(ex: AnswerExecution) -> set[str]:
    keys: set[str] = set()
    for f in ex.fallbacks:
        if isinstance(f, dict):
            keys.add(f"{f.get('from')}|{f.get('to')}|{f.get('reason')}")
        else:
            keys.add(str(f))
    return keys


def _citation_keys(ex: AnswerExecution) -> set[str]:
    keys: set[str] = set()
    for c in ex.claims_used:
        for ev in c.get("evidence") or []:
            keys.add(f"ev:{ev.get('knowledge_id')}|{ev.get('block_id')}")
        keys.add(f"claim:{c.get('claim_id')}")
    for s in ex.sources:
        cit = s.get("citation")
        if isinstance(cit, dict):
            keys.add(str(cit.get("id") or cit.get("block_id") or cit.get("knowledge_id") or sorted(cit.keys())))
    return keys


def compare_answers(primary: AnswerExecution, candidate: AnswerExecution) -> AnswerShadowDiff:
    notes: list[str] = []
    mode_ok = primary.answer_mode == candidate.answer_mode
    claims_ok = _claim_ids(primary) == _claim_ids(candidate)
    raw_ok = _raw_ids(primary) == _raw_ids(candidate)
    conf_ok = _conflict_keys(primary) == _conflict_keys(candidate)
    fb_ok = _fallback_keys(primary) == _fallback_keys(candidate)
    cit_ok = _citation_keys(primary) == _citation_keys(candidate)
    no_ans_ok = (
        (primary.answer_mode == "no_answer") == (candidate.answer_mode == "no_answer")
    )
    if not mode_ok:
        notes.append(f"mode:{primary.answer_mode}!={candidate.answer_mode}")
    if not claims_ok:
        notes.append("claim_ids_differ")
    if not raw_ok:
        notes.append("raw_evidence_ids_differ")
    if not conf_ok:
        notes.append("conflicts_differ")
    if not fb_ok:
        notes.append("fallbacks_differ")
    if not cit_ok:
        notes.append("citations_differ")
    if not no_ans_ok:
        notes.append("no_answer_decision_differ")
    return AnswerShadowDiff(
        answer_mode_match=mode_ok,
        claim_ids_match=claims_ok,
        raw_evidence_ids_match=raw_ok,
        conflicts_match=conf_ok,
        fallbacks_match=fb_ok,
        citation_keys_match=cit_ok,
        no_answer_match=no_ans_ok,
        notes=tuple(notes),
    )


def meets_answer_cutover_gates(diff: AnswerShadowDiff) -> bool:
    return all(
        [
            diff.answer_mode_match,
            diff.claim_ids_match,
            diff.raw_evidence_ids_match,
            diff.conflicts_match,
            diff.fallbacks_match,
            diff.citation_keys_match,
            diff.no_answer_match,
        ],
    )


def log_answer_shadow(diff: AnswerShadowDiff, *, question_preview: str = "") -> None:
    logger.info(
        "answer_shadow q=%r mode_ok=%s claims_ok=%s raw_ok=%s conf_ok=%s "
        "fb_ok=%s cit_ok=%s no_ans_ok=%s notes=%s",
        (question_preview or "")[:80],
        diff.answer_mode_match,
        diff.claim_ids_match,
        diff.raw_evidence_ids_match,
        diff.conflicts_match,
        diff.fallbacks_match,
        diff.citation_keys_match,
        diff.no_answer_match,
        list(diff.notes),
    )
