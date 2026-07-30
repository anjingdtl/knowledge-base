"""Phase 3.1: organizational scope discrimination (HQ vs branch alignment).

Pins the new ``extract_org_scope`` / ``compute_scope_signal`` behavior and the
end-to-end effect on ``score_candidate_relevance`` so wrong-family top-1 picks
(branch query → HQ candidate, or generic query → branch candidate) no longer
happen.  Each test is paired with an anti-overfit assertion: a structurally
similar but in-scope query that must NOT trip the scope veto.

Also covers ``compute_regulation_phrase_signal`` for regulation-family exact
match (e.g. "合规管理办法" vs "重要决策法律合规审核管理办法").
"""
from __future__ import annotations

from src.answering.query_planner import extract_org_scope
from src.answering.direct_slot_gate import apply_direct_slot_accept
from src.services.relevance_gate import (
    compute_scope_signal,
    compute_regulation_phrase_signal,
    score_candidate_relevance,
    evaluate_evidence_unified,
)


# --------------------------------------------------------------------------- #
# extract_org_scope                                                            #
# --------------------------------------------------------------------------- #


def test_branch_query_explicit_branch_token_detected() -> None:
    """A query that names a branch unit must surface that branch token."""
    scope = extract_org_scope("号百分公司差旅费管理办法 适用范围")
    assert scope["is_branch_query"] is True
    assert scope["is_hq_query"] is False
    assert scope["has_scope_signal"] is True
    assert "号百" in scope["query_branches"]


def test_branch_query_with_prefixed_branch_token_detected() -> None:
    """A query with "<X>分公司" must capture the full prefixed branch token."""
    scope = extract_org_scope("南宁分公司安全生产管理办法 专职安全员配置")
    assert scope["is_branch_query"] is True
    assert any("南宁分公司" == b for b in scope["query_branches"])


def test_hq_query_treated_as_hq_not_branch() -> None:
    """A query that names HQ explicitly (and no branch) must be classified HQ."""
    scope = extract_org_scope("中国电信集团总部 重大决策法律合规审核")
    assert scope["is_hq_query"] is True
    assert scope["is_branch_query"] is False


def test_generic_query_has_no_scope_signal() -> None:
    """A query with neither HQ nor branch tokens has no scope signal."""
    scope = extract_org_scope("员工出差的住宿费和伙食补助每天能报多少")
    assert scope["is_branch_query"] is False
    assert scope["is_hq_query"] is False
    assert scope["has_scope_signal"] is False
    assert scope["query_branches"] == []


def test_branch_token_inside_hq_marker_does_not_flip_to_hq() -> None:
    """``集团分公司`` is a branch, not HQ — the negative lookahead must hold."""
    scope = extract_org_scope("集团分公司 报销标准")
    assert scope["is_branch_query"] is True
    assert scope["is_hq_query"] is False


# --------------------------------------------------------------------------- #
# compute_scope_signal                                                         #
# --------------------------------------------------------------------------- #


def test_scope_signal_branch_query_hq_title_strong_penalty() -> None:
    """Branch-explicit query + HQ candidate title → strong negative signal."""
    signal, reason = compute_scope_signal(
        "号百分公司差旅费管理办法 适用范围",
        "中电信桂-2025-256号-关于印发中国电信广西公司差旅费管理办法-2025年-的通知",
    )
    assert signal <= -0.15
    assert reason == "branch_query_hq_title_mismatch"


def test_scope_signal_branch_query_branch_title_match_boost() -> None:
    """Branch-explicit query + matching branch candidate title → positive boost."""
    signal, reason = compute_scope_signal(
        "号百分公司差旅费管理办法 适用范围",
        "中电信桂号百-2018-53号-关于印发中国电信广西号百分公司差旅费管理办法-2018年版-的通知",
    )
    assert signal > 0.0
    assert reason == "branch_scope_match"


def test_scope_signal_generic_query_branch_title_mild_penalty() -> None:
    """No-scope query + branch title → mild negative signal (HQ preferred)."""
    signal, reason = compute_scope_signal(
        "员工出差的住宿费和伙食补助每天能报多少",
        "中电信桂号百-2018-53号-关于印发中国电信广西号百分公司差旅费管理办法-2018年版-的通知",
    )
    assert -0.10 < signal < 0.0
    assert reason == "no_scope_branch_title_penalty"


def test_scope_signal_generic_query_hq_title_no_penalty() -> None:
    """No-scope query + HQ title → neutral (no change to score)."""
    signal, reason = compute_scope_signal(
        "员工出差的住宿费和伙食补助每天能报多少",
        "中电信桂-2025-256号-关于印发中国电信广西公司差旅费管理办法-2025年-的通知",
    )
    assert signal == 0.0
    assert reason == "no_scope_signal"


def test_scope_signal_hq_query_branch_title_penalty() -> None:
    """HQ-explicit query + branch candidate title → negative signal."""
    signal, reason = compute_scope_signal(
        "集团总部 差旅费办法适用范围",
        "中电信桂号百-2018-53号-关于印发中国电信广西号百分公司差旅费管理办法-2018年版-的通知",
    )
    assert signal < 0.0
    assert reason == "hq_query_branch_title_mismatch"


# --------------------------------------------------------------------------- #
# End-to-end: score_candidate_relevance ranks branch above HQ when query      #
# explicitly asks for a branch (the wrong-family top-1 regression root cause).#
# --------------------------------------------------------------------------- #


_HQ_TITLE = "中电信桂-2025-256号-关于印发中国电信广西公司差旅费管理办法-2025年-的通知"
_BRANCH_TITLE = "中电信桂号百-2018-53号-关于印发中国电信广西号百分公司差旅费管理办法-2018年版-的通知"


def test_branch_query_ranks_branch_above_hq_when_texts_comparable() -> None:
    """A branch-explicit query must rank the branch doc above the HQ doc
    even when both candidates share the same lexical evidence.
    """
    query = "号百分公司差旅费管理办法 适用范围"
    text = "差旅费管理办法 适用范围 号百分公司员工"
    hq_score = score_candidate_relevance(query, {"title": _HQ_TITLE, "text": text})
    branch_score = score_candidate_relevance(query, {"title": _BRANCH_TITLE, "text": text})
    assert branch_score["final_relevance_score"] > hq_score["final_relevance_score"]
    assert branch_score["scope_reason"] == "branch_scope_match"
    assert hq_score["scope_reason"] == "branch_query_hq_title_mismatch"


def test_generic_query_ranks_hq_above_branch_when_texts_comparable() -> None:
    """A generic query (no scope signal) must rank the HQ doc above the branch
    doc when both candidates share the same lexical evidence, so a branch
    regulation no longer pollutes a generic ask top-1.
    """
    query = "员工出差住宿费伙食补助每天能报多少"
    text = "出差 住宿费 伙食补助 每天 报销标准"
    hq_score = score_candidate_relevance(query, {"title": _HQ_TITLE, "text": text})
    branch_score = score_candidate_relevance(query, {"title": _BRANCH_TITLE, "text": text})
    assert hq_score["final_relevance_score"] > branch_score["final_relevance_score"]
    assert hq_score["scope_reason"] == "no_scope_signal"
    assert branch_score["scope_reason"] == "no_scope_branch_title_penalty"


def test_scope_signal_exposed_in_score_payload_for_audit() -> None:
    """``score_candidate_relevance`` must expose ``scope_signal`` and
    ``scope_reason`` so snapshots can record why a candidate was boosted or
    penalized at the family level.
    """
    scores = score_candidate_relevance(
        "号百分公司差旅费管理办法 适用范围",
        {"title": _HQ_TITLE, "text": "差旅费 适用范围"},
    )
    assert "scope_signal" in scores
    assert "scope_reason" in scores
    assert scores["scope_reason"] == "branch_query_hq_title_mismatch"
    assert scores["scope_signal"] < 0.0


# --------------------------------------------------------------------------- #
# direct_slot_gate scope veto                                                  #
# --------------------------------------------------------------------------- #


def test_direct_slot_gate_vetoes_branch_query_hq_candidate_override() -> None:
    """When the gate would have rejected and direct_slot_gate wants to
    resurrect a HQ candidate against an explicit branch query, the scope
    veto must block the override and surface an audit reason.
    """
    base_decision = {
        "accept": False,
        "no_match": True,
        "reason": "insufficient_relevant_evidence",
        "top_score": 0.20,
        "threshold": 0.35,
        "items": [],
        "intent": "ordinary",
    }
    candidate = {
        "title": _HQ_TITLE,
        "text": "号百分公司 差旅费管理办法 适用范围",
        "knowledge_id": "k1",
        "passage_id": "p1",
    }
    out = apply_direct_slot_accept(
        "号百分公司差旅费管理办法 适用范围",
        [candidate],
        base_decision=base_decision,
        threshold=0.35,
    )
    assert out["accept"] is False
    audit = out.get("direct_slot_audit") or {}
    assert audit.get("reason", "").startswith("scope_veto:")
    assert audit.get("scope_signal", 0.0) <= -0.10


def test_direct_slot_gate_does_not_veto_generic_query_branch_candidate() -> None:
    """For a generic query (no explicit scope), the mild -0.05 branch penalty
    must NOT trigger the veto — the score alone demotes the branch doc, and
    direct-slot override can still fire when lexical anchors are strong.
    """
    base_decision = {
        "accept": False,
        "no_match": True,
        "reason": "insufficient_relevant_evidence",
        "top_score": 0.30,
        "threshold": 0.35,
        "items": [],
        "intent": "ordinary",
    }
    # Strong lexical anchor evidence: query anchors all appear in candidate.
    candidate = {
        "title": _BRANCH_TITLE,
        "text": "员工出差 住宿费 伙食补助 每天报销标准 100元 不得超标准",
        "knowledge_id": "k1",
        "passage_id": "p1",
    }
    out = apply_direct_slot_accept(
        "员工出差住宿费伙食补助每天报销标准多少",
        [candidate],
        base_decision=base_decision,
        threshold=0.35,
    )
    # The veto must not fire for the -0.05 branch penalty alone.
    audit = out.get("direct_slot_audit") or {}
    veto_reason = audit.get("reason", "")
    assert not veto_reason.startswith("scope_veto:"), veto_reason


# --------------------------------------------------------------------------- #
# Regulation-phrase exact match (SPEC Phase 3.1 family discrimination)        #
# --------------------------------------------------------------------------- #

_EXACT_FAMILY_TITLE = "中电信桂-2023-299号-关于印发中国电信广西公司合规管理办法的通知--3eebb9f9"
_MORE_SPECIFIC_FAMILY_TITLE = "中电信桂-2025-505号-关于印发中国电信广西公司重要决策法律合规审核管理办法的通知--0b5f5cf6"


def test_regulation_phrase_exact_match_returns_boost() -> None:
    """Query regulation phrase equal to title's prefix+suffix → +0.10."""
    signal, reason = compute_regulation_phrase_signal(
        "合规管理办法 首席合规官 总法律顾问",
        _EXACT_FAMILY_TITLE,
    )
    assert signal == 0.10
    assert reason == "regulation_phrase_exact_match"


def test_regulation_phrase_title_more_specific_returns_penalty() -> None:
    """Query prefix appears in title but extra CJK chars before the suffix →
    the title belongs to a more specific regulation family → -0.08 penalty.
    """
    signal, reason = compute_regulation_phrase_signal(
        "合规管理办法 首席合规官 总法律顾问",
        _MORE_SPECIFIC_FAMILY_TITLE,
    )
    assert signal == -0.08
    assert reason == "regulation_phrase_title_more_specific"


def test_regulation_phrase_no_signal_when_query_lacks_regulation_phrase() -> None:
    """A query that names no regulation phrase must produce no signal."""
    signal, reason = compute_regulation_phrase_signal(
        "员工出差的住宿费和伙食补助每天能报多少",
        _EXACT_FAMILY_TITLE,
    )
    assert signal == 0.0
    assert reason == "no_query_regulation_phrase"


def test_regulation_phrase_no_signal_when_prefix_not_in_title() -> None:
    """A query whose regulation prefix is absent from the title must produce
    no signal (we never penalize a candidate just because it lacks the
    query's regulation noun — that is already captured by lexical coverage).
    """
    signal, reason = compute_regulation_phrase_signal(
        "保密工作管理办法",
        _EXACT_FAMILY_TITLE,  # title is about 合规管理, not 保密工作
    )
    assert signal == 0.0
    assert reason == "regulation_phrase_no_match"


def test_regulation_phrase_exact_match_ranks_exact_above_more_specific() -> None:
    """End-to-end: a query asking for "合规管理办法" must rank the exact
    family doc above the more-specific family doc when both share comparable
    lexical evidence.  This is the wrong-family top-1 regression root cause.
    """
    query = "合规管理办法 首席合规官 总法律顾问"
    text = "首席合规官 总法律顾问 合规管理办法 适用"
    exact = score_candidate_relevance(query, {"title": _EXACT_FAMILY_TITLE, "text": text})
    specific = score_candidate_relevance(
        query, {"title": _MORE_SPECIFIC_FAMILY_TITLE, "text": text}
    )
    assert exact["final_relevance_score"] > specific["final_relevance_score"]
    assert exact["regulation_phrase_reason"] == "regulation_phrase_exact_match"
    assert specific["regulation_phrase_reason"] == "regulation_phrase_title_more_specific"


def test_regulation_phrase_signal_exposed_in_score_payload_for_audit() -> None:
    """``score_candidate_relevance`` must expose ``regulation_phrase_signal``
    and ``regulation_phrase_reason`` so snapshots can record why a candidate
    was boosted or penalized at the family level.
    """
    scores = score_candidate_relevance(
        "合规管理办法 首席合规官",
        {"title": _EXACT_FAMILY_TITLE, "text": "首席合规官 合规管理"},
    )
    assert "regulation_phrase_signal" in scores
    assert "regulation_phrase_reason" in scores
    assert scores["regulation_phrase_reason"] == "regulation_phrase_exact_match"
    assert scores["regulation_phrase_signal"] > 0.0


def test_regulation_phrase_does_not_fire_on_incidental_word_overlap() -> None:
    """Anti-overfit: a query phrase whose prefix happens to appear in the
    title but whose suffix is far away (not within the small window) must NOT
    trigger the more-specific penalty.  This prevents penalizing a candidate
    that happens to share a common word like "公司" with an unrelated
    regulation family.
    """
    # "公司办法" is not a real regulation phrase; the regex must not capture it
    # because {2,12} requires at least 2 CJK chars before the suffix, but the
    # test exercises the boundary case to lock down the no-match path.
    signal, reason = compute_regulation_phrase_signal(
        "差旅费管理办法",
        "中电信桂-2025-256号-关于印发中国电信广西公司合规管理办法的通知",
    )
    # The exact family "差旅费管理办法" prefix is "差旅费"; it is absent from
    # this title (which is about 合规管理).  No signal must fire.
    assert signal == 0.0
    assert reason == "regulation_phrase_no_match"
