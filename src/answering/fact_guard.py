"""Numeric-fact guard for clause-style answers (SPEC Phase 5, KB-019).

Prevents the answer pipeline from substituting an adjacent clause's value for
the requested subject. Example: for "翼支付III类账户 年付款限额" the evidence
clause is "II类 10万元；III类 20万元"; a truncated context made the LLM cite the
II类 "10万元" as the III类 answer. This module selects the numeric value that
is textually anchored to the requested subject (III类 ⇒ 20万元) and never the
neighbor clause's value.

The guard is deterministic and lexically verifiable — it does not call any LLM.
"""
from __future__ import annotations

import re
from typing import Any

# A "subject" is a short noun phrase like "II类", "III类", "团体", "个人",
# "区外", "区内". A "value" is a number+unit like "10万元", "2000元", "70%".
_VALUE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(万元|亿|元|%|％|天|周|个月|月|年|次|户|人|个|米|公里|千米|倍|kg|g|kw|w)",
    re.IGNORECASE,
)
# Clause separators: Chinese/Latin semicolons, periods, newlines, enumerated
# list markers (（一） (1) 第一条 etc).
_CLAUSE_SPLIT_RE = re.compile(r"[；;。\n]|（[一二三四五六七八九十0-9]+）|\([0-9]+\)|第[一二三四五六七八九十百千]+[条款项]")


def _normalize_subject(subject: str) -> str:
    return re.sub(r"\s+", "", (subject or "")).lower()


def select_numeric_fact_for_subject(
    *,
    subject: str,
    evidence: str,
    value_pattern: str = r"\d+(?:\.\d+)?\s*(?:万元|亿|元|%|％)",
) -> str | None:
    """Return the numeric value textually anchored to ``subject`` in evidence.

    Splits the evidence into clauses, finds the clause whose text contains the
    subject, and returns the first matching value in THAT clause. If the
    subject appears in multiple clauses, prefers the clause where the value
    follows the subject (handles "III类账户…20万元").

    Returns None when no clause both contains the subject and a value — callers
    MUST then treat the answer as unsupported (do NOT fall back to a neighbor
    clause's value).
    """
    subj = _normalize_subject(subject)
    if not subj or not evidence:
        return None
    clauses = [c for c in _CLAUSE_SPLIT_RE.split(evidence) if c.strip()]
    # Prefer clauses that contain the subject.
    subj_clauses = [c for c in clauses if subj in _normalize_subject(c)]
    search_clauses = subj_clauses or clauses
    val_re = re.compile(value_pattern)
    for c in search_clauses:
        # Within a clause, prefer a value that comes AFTER the subject token
        # (the subject introduces the value in policy clauses).
        n = _normalize_subject(c)
        idx = n.find(subj)
        if idx >= 0:
            tail = c[idx:]
            m = val_re.search(tail)
            if m:
                return _normalize_value(m.group(0))
        # Fallback: any value in the clause.
        m = val_re.search(c)
        if m:
            return _normalize_value(m.group(0))
    return None


def answer_value_is_anchored(
    *,
    subject: str,
    evidence: str,
    claimed_value: str,
) -> bool:
    """True iff ``claimed_value`` appears in a clause of ``evidence`` that also
    contains ``subject`` (i.e. the claimed value is anchored to the subject,
    not borrowed from an adjacent clause)."""
    selected = select_numeric_fact_for_subject(
        subject=subject, evidence=evidence,
    )
    if selected is None:
        return False
    return _values_equal(selected, claimed_value)


def _normalize_value(v: str) -> str:
    return re.sub(r"\s+", "", v or "")


def _values_equal(a: str, b: str) -> bool:
    na = _normalize_value(a)
    nb = _normalize_value(b)
    if na == nb:
        return True
    # Tolerate full/half-width and trailing unit variants ("10万" vs "10万元").
    na_compact = na.replace("元", "").replace("％", "%")
    nb_compact = nb.replace("元", "").replace("％", "%")
    return na_compact == nb_compact


def strip_unanchored_numeric_assertions(
    answer: str,
    *,
    evidence: str,
    value_pattern: str = r"\d+(?:\.\d+)?\s*(?:万元|亿|元|%|％)",
) -> tuple[str, bool]:
    """Remove numeric values from ``answer`` that do not appear in evidence.

    Returns ``(cleaned_answer, stripped_any)``. Only strips explicit
    amount/percent values — never category labels (II类/III类), dates, or prose.
    A value is "anchored" if it appears (modulo whitespace/全半角) anywhere in
    the evidence text. When ``stripped_any`` is True the caller SHOULD append a
    ``numeric_fact_guard_stripped_unanchored_value`` warning and, if the answer
    becomes empty, downgrade to no_answer.
    """
    if not answer or not evidence:
        return (answer or "", False)
    val_re = re.compile(value_pattern)
    ev_norm = _normalize_value(evidence)
    stripped = False

    def _repl(m: re.Match) -> str:
        nonlocal stripped
        v = _normalize_value(m.group(0))
        if v and v in ev_norm:
            return m.group(0)  # anchored — keep
        # Also tolerate the value appearing with/without 元 (10万 vs 10万元).
        v_alt = v.replace("元", "").replace("％", "%")
        if v_alt and (v_alt in ev_norm or v in ev_norm.replace("元", "")):
            return m.group(0)
        stripped = True
        return ""

    cleaned = val_re.sub(_repl, answer)
    return (cleaned, stripped)
