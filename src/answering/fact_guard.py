"""Numeric-fact guard for clause-style answers (SPEC Phase 5 / v2 Phase 3, KB-019).

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

# Subjects that require clause-level anchoring when present in the question.
_SUBJECT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"III\s*类|Ⅲ\s*类|三类"), "III类"),
    (re.compile(r"II\s*类|Ⅱ\s*类|二类"), "II类"),
    (re.compile(r"(?<![IⅠ二三])I\s*类|(?<![IⅠ二三])Ⅰ\s*类|一类"), "I类"),
    (re.compile(r"区外"), "区外"),
    (re.compile(r"区内"), "区内"),
    (re.compile(r"代理商"), "代理商"),
    (re.compile(r"自然月"), "自然月"),
    (re.compile(r"团体"), "团体"),
    (re.compile(r"个人"), "个人"),
]


def _normalize_subject(subject: str) -> str:
    return re.sub(r"\s+", "", (subject or "")).lower()


def extract_query_subjects(query: str) -> list[str]:
    """Return ordered subject labels detected in the user question."""
    q = query or ""
    found: list[str] = []
    seen: set[str] = set()
    for pat, label in _SUBJECT_PATTERNS:
        if pat.search(q) and label not in seen:
            seen.add(label)
            found.append(label)
    return found


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
    search_clauses = subj_clauses or []
    if not search_clauses:
        return None  # subject absent — never borrow a neighbor value
    val_re = re.compile(value_pattern)
    for c in search_clauses:
        # Within a clause, prefer a value that comes AFTER the subject token
        # (the subject introduces the value in policy clauses).
        if subj in _normalize_subject(c):
            m = None
            # Prefer a value that follows the subject token in the clause.
            subj_match = re.search(
                re.escape(subject).replace(r"\ ", r"\s*"), c, re.I,
            )
            if subj_match is None:
                for token in (subject, subject.replace("类", r"\s*类")):
                    subj_match = re.search(token, c, re.I)
                    if subj_match:
                        break
            if subj_match is not None:
                m = val_re.search(c[subj_match.start():])
            if m is None:
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
        # Fallback: claimed value and subject co-occur in the same clause.
        return _cooccur_in_clause(subject, claimed_value, evidence)
    return _values_equal(selected, claimed_value) or _cooccur_in_clause(
        subject, claimed_value, evidence,
    )


def _cooccur_in_clause(subject: str, value: str, evidence: str) -> bool:
    subj = _normalize_subject(subject)
    val = _normalize_value(value)
    if not subj or not val:
        return False
    for c in _CLAUSE_SPLIT_RE.split(evidence or ""):
        n = _normalize_subject(c)
        if subj in n and (val in n or val.replace("元", "") in n.replace("元", "")):
            return True
    return False


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
    question: str | None = None,
) -> tuple[str, bool]:
    """Remove numeric values from ``answer`` that are not properly anchored.

    Anchoring rules (SPEC v2 Phase 3):
      1. If the question names a subject (III类 / II类 / 区内 …), a value is
         kept only when it is textually anchored to that subject in evidence.
         Neighbor-clause values (II类 10万元 when asking III类) are stripped.
      2. Otherwise a value is kept if it appears anywhere in evidence
         (modulo whitespace / 元 variants).
      3. Comparative phrases built on unanchored values (e.g. "超过10万元"
         when 10万元 is the II类 figure for an III类 question) are stripped.

    Returns ``(cleaned_answer, stripped_any)``.
    """
    if not answer or not evidence:
        return (answer or "", False)
    val_re = re.compile(value_pattern)
    ev_norm = _normalize_value(evidence)
    subjects = extract_query_subjects(question or "")
    # Primary subject: prefer the most specific (III类 over II类 if both match
    # — patterns are ordered so III is first).
    primary_subject = subjects[0] if subjects else None
    stripped = False

    # Allowed values under subject anchoring.
    allowed: set[str] = set()
    if primary_subject:
        for subj in subjects:
            selected = select_numeric_fact_for_subject(subject=subj, evidence=evidence)
            if selected:
                allowed.add(selected)
                allowed.add(selected.replace("元", ""))
    # Also allow any value that co-occurs with the primary subject.
    if primary_subject:
        for m in val_re.finditer(evidence):
            v = _normalize_value(m.group(0))
            if _cooccur_in_clause(primary_subject, v, evidence):
                allowed.add(v)
                allowed.add(v.replace("元", ""))

    def _is_allowed(v: str) -> bool:
        nv = _normalize_value(v)
        if not nv:
            return False
        if primary_subject and allowed:
            if nv in allowed or nv.replace("元", "") in allowed:
                return True
            # Explicit subject-level check
            return answer_value_is_anchored(
                subject=primary_subject, evidence=evidence, claimed_value=nv,
            )
        # No subject: value must appear in evidence.
        if nv in ev_norm:
            return True
        nv_alt = nv.replace("元", "").replace("％", "%")
        if nv_alt and (nv_alt in ev_norm or nv in ev_norm.replace("元", "")):
            return True
        return False

    def _repl(m: re.Match) -> str:
        nonlocal stripped
        v = m.group(0)
        if _is_allowed(v):
            return v
        stripped = True
        return ""

    cleaned = val_re.sub(_repl, answer)

    # Strip comparative leftovers that no longer have a number
    # ("超过" / "高于" / "低于" hanging alone) and "超过X" where X was stripped.
    if stripped:
        cleaned = re.sub(r"(超过|高于|低于|不少于|不超过|大于|小于)\s*(?=[，。,.；;]|$)", "", cleaned)
        # Also drop "超过10万元" style if 10万元 was the wrong subject value —
        # already handled by value strip; clean double spaces.
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return (cleaned, stripped)


def assert_subject_value_or_none(
    *,
    question: str,
    evidence: str,
    answer: str,
) -> tuple[str, bool]:
    """If the question has a clear subject+numeric intent and the answer
    asserts a wrong-subject value, strip it. Convenience wrapper used by
    the assembler.
    """
    return strip_unanchored_numeric_assertions(
        answer, evidence=evidence, question=question,
    )
